import json

# detect_duplicate — deterministic duplicate-ICSR detection. Adverse-event cases frequently arrive
# from multiple channels (patient, HCP, literature, partner), and double-reporting the same case to
# the regulator is a data-quality and compliance problem. This tool compares a de-identified case KEY
# (suspect product | event term | onset | reporter type) against the set of already-received keys and
# returns DUPLICATE (with a HOLD so the case isn't reported twice) or UNIQUE. It operates on the
# de-identified key only — not on PHI — so the governance point is the HOLD state, not de-identification.
#
# The agent can detect and flag a suspected duplicate; confirming a merge/close is a safety-reviewer
# action recorded through the normal path.


def _coerce(e):
    e = e or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {"case_key": e}
    return e


def _norm(k):
    return "|".join(p.strip().lower() for p in str(k).replace(",", "|").split("|") if p.strip())


def _as_keys(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, str):
        # allow a JSON list or a semicolon/newline separated string of keys
        try:
            j = json.loads(v)
            if isinstance(j, list):
                return [_norm(x) for x in j]
        except Exception:
            pass
        return [_norm(x) for x in v.replace("\n", ";").split(";") if x.strip()]
    return []


def handler(event, context):
    e = _coerce(event)
    key = _norm(e.get("case_key", ""))
    if not key:
        return {"checked": False, "error": "provide a de-identified case_key (product|event|onset|reporter)"}
    known = set(_as_keys(e.get("known_keys")))

    is_dup = key in known
    status = "DUPLICATE" if is_dup else "UNIQUE"

    # Short proof fields FIRST (MCP client truncates ~200 chars).
    return {
        "checked": True,
        "duplicate_status": status,             # DUPLICATE | UNIQUE
        "hold": is_dup,                          # a suspected duplicate is HELD (not reported twice)
        "case_key": key,
        "note": ("suspected DUPLICATE of an already-received case; HELD pending safety-reviewer confirmation "
                 "before any submission" if is_dup else
                 "no matching prior case; unique ICSR may proceed through the normal governed path"),
    }
