"""case_store.py — R3-2: ZERO-PHI PASS-BY-REFERENCE orchestration (ported from the financial-aid agent).

THE FINDING THIS CLOSES: Step Functions execution history is a DATA STORE. When the raw adverse-event
`source` travels as state input/output, the workflow engine becomes an additional sensitive-data
repository — visible to anyone with `states:GetExecutionHistory`, retained ~90 days, and outside the
purpose-built encrypted stores. The strict PHI canary flags this.

THE FIX: raw content is written ONCE to an encrypted, TTL'd DynamoDB store (CASE_TABLE — CMK-encrypted
under `kms=customer-managed`), and ONLY an opaque `case_ref` travels through the controller. Tools that
legitimately need the content (intake extraction, masking) fetch it server-side by ref; nothing returns
raw text into state output. Masked content is likewise stored server-side and reached via the signed
`sanitized_ref` (sanitized.py), so it too never crosses execution state.

Follow-on: B5 tenant-scoped fetch (refuse a ref from another tenant) requires the tenancy module the
financial-aid agent uses; PV stamps TENANT_ID onto the record but does not yet enforce cross-tenant
fail-closed on read. Documented in PV-PILOT-READINESS-PLAN."""
import os
import time
import uuid

_TABLE_ENV = "CASE_TABLE"
_TTL_SECONDS = int(os.environ.get("CASE_TTL_SECONDS", "604800"))  # 7d working data; WORM holds evidence


def _table():
    name = os.environ.get(_TABLE_ENV, "")
    if not name:
        return None
    import boto3
    return boto3.resource("dynamodb").Table(name)


class MemoryCaseStore:
    """In-process store for tests/offline runs (module-level singleton)."""
    items = {}


def put_case(text, kind="case", case_id=""):
    """Store content; return the opaque ref (never the content)."""
    ref = f"case-{uuid.uuid4().hex}"
    item = {"case_ref": ref, "text": text, "kind": kind, "case_id": str(case_id or ""),
            "expires_at": int(time.time()) + _TTL_SECONDS}
    if os.environ.get("TENANT_ID"):
        item["tenant"] = os.environ["TENANT_ID"]
    t = _table()
    if t is not None:
        t.put_item(Item=item)
    else:
        MemoryCaseStore.items[ref] = item
    return ref


def get_case(ref):
    """Fetch content by ref. None (fail-closed) on missing/unknown ref. Never raises content into the
    caller's error path."""
    if not ref or not isinstance(ref, str):
        return None
    t = _table()
    if t is not None:
        try:
            item = t.get_item(Key={"case_ref": ref}).get("Item")
        except Exception:
            return None
    else:
        item = MemoryCaseStore.items.get(ref)
    return item.get("text") if item else None
