import os
import time
import boto3

# signoff_register — invoked by the pv-signoff state machine via the
# arn:aws:states:::lambda:invoke.waitForTaskToken integration. It persists the task token
# (bound to this ICSR + requester) into pv-pending-approvals and returns. The execution then
# STAYS PAUSED until an out-of-band approver calls SendTaskSuccess for that token.

PENDING_TABLE = os.environ.get("PENDING_TABLE", "pv-pending-approvals")


def handler(event, context):
    region = os.environ.get("AWS_REGION", "us-east-1")
    icsr_id = event.get("icsr_id")
    requester = event.get("requester")
    token = event.get("taskToken")
    boto3.resource("dynamodb", region_name=region).Table(PENDING_TABLE).put_item(
        Item={"icsr_id": icsr_id, "requester": requester, "task_token": token,
              "status": "PENDING", "created": int(time.time())}
    )
    return {"registered": True, "icsr_id": icsr_id}
