import json
import time
import hashlib
import os
import boto3
from botocore.exceptions import ClientError, BotoCoreError

# write_audit — append a TAMPER-EVIDENT, HASH-CHAINED audit record for the governed workflow.
#
# Two write-once stores:
#   1. DynamoDB `<p>-audit`  — authoritative append-only ledger. Conditional PutItem on
#      attribute_not_exists(audit_id) makes a record un-overwritable. audit_id is the SHA-256 of the
#      logical record (excluding timestamp), so replaying the same logical event collides and is
#      rejected -> that IS the append-only proof.
#   2. S3 `<p>-audit-worm-<acct>-<region>` — Object Lock (GOVERNANCE) WORM copy of the record.
#
# HASH CHAIN (tamper-evidence by construction). Beyond "you can't overwrite a record", each record is
# cryptographically linked to the one before it for the same case:
#     entry_hash = SHA-256(canonical record content, excluding the 3 chain fields)
#     chain_hash = SHA-256(prev_hash + ":" + entry_hash)
# The caller threads the previous record's chain_hash back in as `prev_hash` (the first record of a case
# uses GENESIS). Altering ANY earlier record changes its entry_hash -> its chain_hash -> and breaks the
# prev_hash link of every record after it. Because the store is append-only + WORM (the writer role is
# denied Delete/Update and BypassGovernanceRetention), a tampered ledger cannot be silently re-chained.
# verify_chain.py replays the links and reports INTACT or the first broken record. This makes the audit
# trail court-defensible: not just un-deletable, but provably un-editable.
#
# The tool's execution role is granted PutItem / PutObject only (no reads, no deletes) — so this stays
# a pure append. Threading prev_hash keeps it stateless; in the sequential governed workflow there is one
# writer per case, so no fork. (Auto-discovering the tip would need read access + fork handling; that is
# an adopter hardening, called out in the docs.)

TABLE = os.environ.get("AUDIT_TABLE", "pv-audit")

GENESIS = "GENESIS"
_CHAIN_FIELDS = ("prev_hash", "entry_hash", "chain_hash")


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def entry_hash(record):
    """SHA-256 over the record's content, excluding the three chain fields. Pure; no AWS."""
    body = {k: v for k, v in record.items() if k not in _CHAIN_FIELDS}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def chain_hash(prev_hash, eh):
    """Link this record's entry_hash to the previous record's chain_hash. Pure; no AWS."""
    return hashlib.sha256(((prev_hash or GENESIS) + ":" + eh).encode("utf-8")).hexdigest()


def chain_record(record, prev_hash):
    """Return a copy of `record` with prev_hash / entry_hash / chain_hash set. Pure; no AWS."""
    prev = prev_hash or GENESIS
    eh = entry_hash(record)
    out = dict(record)
    out["prev_hash"] = prev
    out["entry_hash"] = eh
    out["chain_hash"] = chain_hash(prev, eh)
    return out


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
    # Hash-chain link: caller threads the previous record's chain_hash as prev_hash (GENESIS if first).
    record = chain_record(record, e.get("prev_hash"))

    # 1) Append-only DynamoDB (authoritative). Conditional put = un-overwritable.
    try:
        ddb = boto3.resource("dynamodb", region_name=region).Table(TABLE)
        ddb.put_item(Item=record, ConditionExpression="attribute_not_exists(audit_id)")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"stored": False, "audit_id": audit_id, "chain_hash": record["chain_hash"],
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

    # proof fields FIRST — the MCP client truncates tool output to ~220 chars, so chain_hash / prev_hash
    # must fit early enough for the governance demo to read them.
    return {"stored": True, "worm": worm, "chain_hash": record["chain_hash"],
            "prev_hash": record["prev_hash"], "phase": logical["phase"], "audit_id": audit_id,
            "entry_hash": record["entry_hash"], "payload_sha256": payload_sha,
            "table": TABLE, "bucket": bucket, "key": key}
