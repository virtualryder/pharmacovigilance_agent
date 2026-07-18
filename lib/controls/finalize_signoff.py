import os
import time
import hashlib
import boto3
from botocore.exceptions import ClientError

# finalize_signoff — the PRIVILEGED submission task, invoked by the pv-signoff state machine
# ONLY after a valid separation-of-duties approval. This is the sole path that finalizes an
# ICSR; the agent can never reach it (Cedar forbids the direct finalize tool, and this Lambda
# is not exposed on the Gateway). It records the COMMITTED audit entry.

AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "pv-audit")


def handler(event, context):
    region = os.environ.get("AWS_REGION", "us-east-1")
    icsr_id = event.get("icsr_id")
    requester = event.get("requester")
    approver = event.get("approver")

    submission_id = "SUB-" + hashlib.sha256(
        ("%s|%s" % (icsr_id, approver)).encode("utf-8")
    ).hexdigest()[:12].upper()

    try:
        rec = {
            "audit_id": hashlib.sha256(
                ("%s|finalize|COMMITTED|%s|%s" % (icsr_id, requester, approver)).encode("utf-8")
            ).hexdigest(),
            "icsr_id": icsr_id, "action": "finalize_submission", "phase": "COMMITTED",
            "actor": approver, "requester": requester, "submission_id": submission_id,
            "recorded_at": int(time.time()), "source": "pv-finalize",
        }
        boto3.resource("dynamodb", region_name=region).Table(AUDIT_TABLE).put_item(
            Item=rec, ConditionExpression="attribute_not_exists(audit_id)")
    except ClientError:
        pass  # committed audit is idempotent; a duplicate is harmless

    return {"committed": True, "submission_id": submission_id, "icsr_id": icsr_id,
            "requester": requester, "approver": approver}
