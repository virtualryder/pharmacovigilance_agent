"""Restore-aware model-invocation logging (live-found L6, Tier-1 gate 2026-09-05).

Bedrock model-invocation logging is an ACCOUNT singleton: `PutModelInvocationLoggingConfiguration`
replaces whatever the account had, and a plain `Delete` on stack teardown removes it - which is how a
disposable validation environment silently switched OFF the platform runbook's account-wide
configuration. This custom resource is the fix:

  Create : snapshot the account's CURRENT config into an SSM parameter owned by the stack, then put ours
  Update : put ours (the snapshot is kept - it is the state BEFORE this stack existed)
  Delete : if a snapshot exists -> restore it (put); else -> delete ours; then remove the snapshot

Pure boto3 + stdlib; unit-tested with fakes (tests/test_model_logging_provider.py)."""
import json
import os

import boto3


def _clients():
    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("bedrock", region_name=region), boto3.client("ssm", region_name=region)


def _current(bedrock):
    try:
        return bedrock.get_model_invocation_logging_configuration().get("loggingConfig") or None
    except Exception:
        return None


def on_event(event, context, bedrock=None, ssm=None):
    if bedrock is None or ssm is None:
        b, s = _clients()
        bedrock, ssm = bedrock or b, ssm or s
    props = event.get("ResourceProperties") or {}
    cfg = props.get("LoggingConfig")
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    snapshot_param = props["SnapshotParameter"]
    physical_id = props.get("PhysicalId") or snapshot_param
    req = event["RequestType"]
    if req == "Create":
        prior = _current(bedrock)
        # the snapshot is "what the account had before THIS stack" - never overwrite an existing one
        # (a failed/retried Create must not snapshot our own config as the prior state)
        try:
            ssm.get_parameter(Name=snapshot_param)
        except Exception:
            ssm.put_parameter(Name=snapshot_param, Type="String", Overwrite=True,
                              Value=json.dumps({"prior": prior}, default=str),
                              Description="Account model-invocation logging config before this deployment (L6 restore-aware)")
        bedrock.put_model_invocation_logging_configuration(loggingConfig=cfg)
        return {"PhysicalResourceId": physical_id, "Data": {"HadPrior": str(bool(prior))}}
    if req == "Update":
        bedrock.put_model_invocation_logging_configuration(loggingConfig=cfg)
        return {"PhysicalResourceId": physical_id, "Data": {}}
    # Delete
    prior = None
    try:
        prior = json.loads(ssm.get_parameter(Name=snapshot_param)["Parameter"]["Value"]).get("prior")
    except Exception:
        prior = None
    if prior:
        bedrock.put_model_invocation_logging_configuration(loggingConfig=prior)
        restored = True
    else:
        try:
            bedrock.delete_model_invocation_logging_configuration()
        except Exception:
            pass
        restored = False
    try:
        ssm.delete_parameter(Name=snapshot_param)
    except Exception:
        pass
    return {"PhysicalResourceId": physical_id, "Data": {"Restored": str(restored)}}


def handler(event, context):
    return on_event(event, context)
