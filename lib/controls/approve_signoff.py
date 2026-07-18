import json
import os
import boto3
from botocore.exceptions import ClientError

# approve_signoff — the human approver's OUT-OF-BAND action (a console/app, NOT an agent tool;
# the agent has no path to it). Enforces the two separation-of-duties properties:
#   - approver must DIFFER from the requester, and
#   - the approval is SINGLE-USE (PENDING -> CONSUMED via a conditional update).
# Only on a valid, first-time approval does it release the Step Functions task token.

PENDING_TABLE = os.environ.get("PENDING_TABLE", "pv-pending-approvals")


def handler(event, context):
    e = event or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {}
    region = os.environ.get("AWS_REGION", "us-east-1")
    icsr_id = e.get("icsr_id")
    approver = e.get("approver")
    if not icsr_id or not approver:
        return {"approved": False, "reason": "icsr_id and approver are required"}

    tbl = boto3.resource("dynamodb", region_name=region).Table(PENDING_TABLE)
    sfn = boto3.client("stepfunctions", region_name=region)

    item = tbl.get_item(Key={"icsr_id": icsr_id}).get("Item")
    if not item:
        return {"approved": False, "reason": "no pending approval for this ICSR (never requested)"}
    requester = item.get("requester")
    token = item.get("task_token")

    # Separation of duties: the approver cannot be the requester.
    if approver == requester:
        return {"approved": False,
                "reason": "separation-of-duties: approver must differ from requester (%s)" % requester}

    # Single-use: consume the pending record atomically (PENDING -> CONSUMED).
    try:
        tbl.update_item(
            Key={"icsr_id": icsr_id},
            UpdateExpression="SET #s = :c, approver = :a",
            ConditionExpression="#s = :p",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":c": "CONSUMED", ":p": "PENDING", ":a": approver},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"approved": False, "reason": "approval already consumed (single-use)"}
        raise

    sfn.send_task_success(taskToken=token, output=json.dumps({"approved": True, "approver": approver}))
    return {"approved": True, "approver": approver, "requester": requester, "icsr_id": icsr_id}
