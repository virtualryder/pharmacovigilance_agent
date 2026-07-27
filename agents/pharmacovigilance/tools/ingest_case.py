"""ingest_case — R3-2: the ONLY door through which raw adverse-event content enters the system.

Called BEFORE the workflow starts (by the intake API/operator script). Writes the raw `source` to the
encrypted, TTL'd case store and returns an OPAQUE ref — the Step Functions execution is then started
with {case_id, requester, case_ref, drug, ...} and NO raw content ever enters execution input/output
(the strict PHI canary's gate). The response echoes only length + ref, never the content."""
import json

import case_store


def _coerce(e):
    e = e or {}
    if isinstance(e, str):
        try:
            return json.loads(e)
        except Exception:
            return {"source": e}
    return e


def handler(event, context):
    e = _coerce(event)
    text = e.get("source", e.get("case", ""))
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    if not text.strip():
        return {"ingested": False, "error": "empty source"}
    ref = case_store.put_case(text, kind="adverse-event", case_id=e.get("case_id", ""))
    return {"ingested": True, "case_ref": ref, "case_id": e.get("case_id", ""), "chars": len(text),
            "note": "start the workflow with {case_id, requester, case_ref, drug} - raw content never enters Step Functions state"}
