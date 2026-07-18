import json
import os
import time
import hashlib
import boto3
from botocore.exceptions import ClientError, BotoCoreError

# request_signoff — the SANCTIONED path to submission. The agent/reviewer NEVER finalizes
# directly (Cedar `no_self_submit` forbids that). Instead this tool:
#   1. records an INTENT audit entry, then
#   2. starts the `pv-signoff` Step Functions execution, which pauses at waitForTaskToken
#      until a DIFFERENT qualified person approves (separation of duties).
#
# NOTE: for the accelerator, `requester` is passed in the payload. In production it is
# derived from the verified JWT identity on whose behalf the agent acts, not from input.

AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "pv-audit")
SM_NAME = os.environ.get("SM_NAME", "pv-signoff")


def _audit_intent(region, icsr_id, requester):
    try:
        rec = {
            "audit_id": hashlib.sha256(
                ("%s|request_signoff|INTENT|%s" % (icsr_id, requester)).encode("utf-8")
            ).hexdigest(),
            "icsr_id": icsr_id, "action": "request_signoff", "phase": "INTENT",
            "actor": requester, "recorded_at": int(time.time()), "source": "pv-request_signoff",
        }
        boto3.resource("dynamodb", region_name=region).Table(AUDIT_TABLE).put_item(
            Item=rec, ConditionExpression="attribute_not_exists(audit_id)")
    except ClientError:
        pass  # duplicate INTENT is fine; audit is best-effort here (COMMITTED is the durable one)
    except BotoCoreError:
        pass


def handler(event, context):
    e = event or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {}
    region = os.environ.get("AWS_REGION", "us-east-1")
    acct = context.invoked_function_arn.split(":")[4]
    icsr_id = e.get("icsr_id", "")
    requester = e.get("requester", "")
    if not icsr_id or not requester:
        return {"requested": False, "error": "icsr_id and requester are required"}

    _audit_intent(region, icsr_id, requester)

    sm_arn = "arn:aws:states:%s:%s:stateMachine:%s" % (region, acct, SM_NAME)
    try:
        r = boto3.client("stepfunctions", region_name=region).start_execution(
            stateMachineArn=sm_arn,
            input=json.dumps({"icsr_id": icsr_id, "requester": requester}),
        )
        return {"requested": True, "phase": "PENDING_APPROVAL",
                "execution_arn": r["executionArn"], "icsr_id": icsr_id,
                "note": "awaiting a DIFFERENT qualified person's approval (separation of duties)"}
    except (ClientError, BotoCoreError) as exc:
        return {"requested": False,
                "error": "start_execution failed: " + type(exc).__name__ + ": " + str(exc)}
