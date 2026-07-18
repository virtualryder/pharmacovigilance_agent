import json
import time
import hashlib
import os
import boto3
from botocore.exceptions import ClientError, BotoCoreError

# write_audit — append a tamper-evident audit record for the PV ICSR workflow.
#
# Two stores, both write-once:
#   1. DynamoDB `pv-audit`  — authoritative append-only ledger. Conditional PutItem on
#      attribute_not_exists(audit_id) makes a record un-overwritable. audit_id is the
#      SHA-256 of the logical record (excluding timestamp), so replaying the same logical
#      event collides and is rejected -> that IS the append-only proof.
#   2. S3 `pv-audit-worm-<acct>-<region>` — Object Lock (GOVERNANCE) WORM copy of the record.
#
# The tool's execution role is granted PutItem / PutObject only, and is explicitly DENIED
# DeleteItem/UpdateItem and DeleteObject/BypassGovernanceRetention (see deploy_spine.sh).
# So the principal that writes evidence cannot alter or destroy it. That is the whole point.

TABLE = os.environ.get("AUDIT_TABLE", "pv-audit")


def _coerce(event):
    e = event or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {"_raw": e}
    return e


def handler(event, context):
    e = _coerce(event)
    region = os.environ.get("AWS_REGION", "us-east-1")
    acct = "unknown"
    try:
        acct = context.invoked_function_arn.split(":")[4]
    except Exception:
        pass
    bucket = os.environ.get("AUDIT_BUCKET", "pv-audit-worm-%s-%s" % (acct, region))

    # Logical record (audit_id is a hash of this, so it's content-addressed & append-only).
    logical = {
        "icsr_id": e.get("icsr_id", ""),
        "action": e.get("action", ""),
        "phase": e.get("phase", ""),          # e.g. INTENT | COMMITTED
        "actor": e.get("actor", ""),
        "deidentified": e.get("deidentified"),
        "payload": e.get("payload", {}),
    }
    canonical = json.dumps(logical, sort_keys=True, ensure_ascii=False)
    audit_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    payload_sha = hashlib.sha256(
        json.dumps(logical["payload"], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    record = dict(logical)
    record.update({
        "audit_id": audit_id,
        "payload_sha256": payload_sha,
        "recorded_at": int(time.time()),
        "source": "pv-write_audit",
    })

    # 1) Append-only DynamoDB (authoritative). Conditional put = un-overwritable.
    try:
        ddb = boto3.resource("dynamodb", region_name=region).Table(TABLE)
        ddb.put_item(Item=record, ConditionExpression="attribute_not_exists(audit_id)")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"stored": False, "audit_id": audit_id,
                    "reason": "append-only: this exact record is already recorded (immutable)"}
        return {"stored": False, "audit_id": audit_id,
                "error": "audit ledger write failed: " + exc.response.get("Error", {}).get("Code", "?")}
    except BotoCoreError as exc:
        return {"stored": False, "audit_id": audit_id,
                "error": "audit ledger write failed: " + type(exc).__name__}

    # 2) WORM copy to S3 Object Lock. If this fails, the ledger entry still stands (worm=false).
    worm = False
    key = "%s/%s.json" % (logical["icsr_id"] or "unknown", audit_id)
    try:
        boto3.client("s3", region_name=region).put_object(
            Bucket=bucket, Key=key,
            Body=json.dumps(record, sort_keys=True).encode("utf-8"),
            ContentType="application/json",
        )
        worm = True
    except (ClientError, BotoCoreError):
        worm = False

    return {"stored": True, "worm": worm, "audit_id": audit_id,
            "table": TABLE, "bucket": bucket, "key": key,
            "payload_sha256": payload_sha, "phase": logical["phase"]}
