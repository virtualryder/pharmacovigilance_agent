"""Live-found L6: the model-invocation logging custom resource must RESTORE the account's prior config on
delete (account singleton), not just delete ours. Pure fakes; no AWS."""
import json
import pathlib
import sys

import importlib.util  # noqa: E402

# load under a UNIQUE module name: test_gateway_provider imports its own `handler`, and a shared name
# would return whichever provider was imported first
_spec = importlib.util.spec_from_file_location(
    "model_logging_provider_handler",
    pathlib.Path(__file__).resolve().parents[1] / "cdk" / "model_logging_provider" / "handler.py")
h = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(h)

OURS = {"cloudWatchConfig": {"logGroupName": "/aws/bedrock/modelinvocations/ben-x", "roleArn": "arn:r"}, "textDataDeliveryEnabled": True}
PRIOR = {"cloudWatchConfig": {"logGroupName": "/aegis/bedrock/model-invocations", "roleArn": "arn:p"}, "textDataDeliveryEnabled": True}


class FakeBedrock:
    def __init__(self, current=None):
        self.current = current; self.calls = []

    def get_model_invocation_logging_configuration(self):
        return {"loggingConfig": self.current} if self.current else {}

    def put_model_invocation_logging_configuration(self, loggingConfig):
        self.calls.append(("put", loggingConfig)); self.current = loggingConfig; return {}

    def delete_model_invocation_logging_configuration(self):
        self.calls.append(("delete", None)); self.current = None; return {}


class FakeSsm:
    def __init__(self):
        self.params = {}

    def get_parameter(self, Name):
        if Name not in self.params:
            raise KeyError(Name)
        return {"Parameter": {"Value": self.params[Name]}}

    def put_parameter(self, Name, Value, **_):
        self.params[Name] = Value; return {}

    def delete_parameter(self, Name):
        self.params.pop(Name, None); return {}


def _ev(req, cfg=OURS):
    return {"RequestType": req, "ResourceProperties": {"LoggingConfig": json.dumps(cfg), "SnapshotParameter": "/ben-x/model-logging/prior"}}


def test_create_snapshots_the_prior_config_then_puts_ours():
    b, s = FakeBedrock(current=PRIOR), FakeSsm()
    out = h.on_event(_ev("Create"), None, bedrock=b, ssm=s)
    assert json.loads(s.params["/ben-x/model-logging/prior"])["prior"] == PRIOR
    assert b.current == OURS and out["Data"]["HadPrior"] == "True"


def test_delete_restores_the_prior_config_and_removes_the_snapshot():
    b, s = FakeBedrock(current=PRIOR), FakeSsm()
    h.on_event(_ev("Create"), None, bedrock=b, ssm=s)
    out = h.on_event(_ev("Delete"), None, bedrock=b, ssm=s)
    assert b.current == PRIOR and out["Data"]["Restored"] == "True"
    assert "/ben-x/model-logging/prior" not in s.params
    assert ("delete", None) not in b.calls, "a prior config is RESTORED, never deleted"


def test_delete_without_a_prior_config_deletes_ours():
    b, s = FakeBedrock(current=None), FakeSsm()
    h.on_event(_ev("Create"), None, bedrock=b, ssm=s)
    assert json.loads(s.params["/ben-x/model-logging/prior"])["prior"] is None
    out = h.on_event(_ev("Delete"), None, bedrock=b, ssm=s)
    assert b.current is None and out["Data"]["Restored"] == "False" and ("delete", None) in b.calls


def test_retried_create_never_overwrites_the_snapshot_with_our_own_config():
    b, s = FakeBedrock(current=PRIOR), FakeSsm()
    h.on_event(_ev("Create"), None, bedrock=b, ssm=s)
    h.on_event(_ev("Create"), None, bedrock=b, ssm=s)   # CloudFormation retry after our put succeeded
    assert json.loads(s.params["/ben-x/model-logging/prior"])["prior"] == PRIOR


def test_update_puts_ours_and_keeps_the_snapshot():
    b, s = FakeBedrock(current=PRIOR), FakeSsm()
    h.on_event(_ev("Create"), None, bedrock=b, ssm=s)
    new = dict(OURS, imageDataDeliveryEnabled=True)
    h.on_event(_ev("Update", new), None, bedrock=b, ssm=s)
    assert b.current == new and json.loads(s.params["/ben-x/model-logging/prior"])["prior"] == PRIOR
