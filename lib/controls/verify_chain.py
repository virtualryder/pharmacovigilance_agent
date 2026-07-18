import json
import hashlib
import sys

# verify_chain — replay a case's audit records and prove the hash chain is intact (tamper-evidence).
#
# Given the audit records for one case (as written by write_audit.py), this:
#   1. recomputes each record's entry_hash from its content (all fields except the 3 chain fields) and
#      checks it matches the stored entry_hash  -> detects ANY content edit;
#   2. recomputes chain_hash = SHA-256(prev_hash + ":" + entry_hash) and checks it matches  -> detects
#      tampering with the chain fields themselves;
#   3. reconstructs the order by following the links (GENESIS -> record whose prev_hash == GENESIS ->
#      the record whose prev_hash == that record's chain_hash -> ...) and checks every record is visited
#      exactly once with no fork, orphan, gap, or reordering.
# Returns {"intact": bool, "length": n, "broken_at": <chain_hash|null>, "reason": str}. Read-only; pure
# stdlib, so it runs anywhere (a verifier does not need — and should not have — write access to the store).
#
# Usage as a CLI:  python verify_chain.py records.json      (records.json = a JSON array of the case's items)

GENESIS = "GENESIS"
_CHAIN_FIELDS = ("prev_hash", "entry_hash", "chain_hash")


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _entry_hash(record):
    body = {k: v for k, v in record.items() if k not in _CHAIN_FIELDS}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _chain_hash(prev_hash, eh):
    return hashlib.sha256(((prev_hash or GENESIS) + ":" + eh).encode("utf-8")).hexdigest()


def verify(records):
    recs = list(records or [])
    n = len(recs)
    if n == 0:
        return {"intact": True, "length": 0, "broken_at": None, "reason": "empty chain (vacuously intact)"}

    # 1 + 2: every record must be internally consistent (content -> entry_hash -> chain_hash).
    for r in recs:
        for f in _CHAIN_FIELDS:
            if f not in r:
                return {"intact": False, "length": n, "broken_at": r.get("chain_hash"),
                        "reason": "record missing chain field '%s' (audit_id=%s)" % (f, r.get("audit_id"))}
        eh = _entry_hash(r)
        if eh != r["entry_hash"]:
            return {"intact": False, "length": n, "broken_at": r.get("chain_hash"),
                    "reason": "content was altered (entry_hash mismatch) for audit_id=%s" % r.get("audit_id")}
        if _chain_hash(r["prev_hash"], eh) != r["chain_hash"]:
            return {"intact": False, "length": n, "broken_at": r.get("chain_hash"),
                    "reason": "chain_hash does not match prev_hash+entry_hash for audit_id=%s" % r.get("audit_id")}

    # 3: the links must form one unbroken list from GENESIS with no fork / gap / duplicate.
    by_prev = {}
    for r in recs:
        by_prev.setdefault(r["prev_hash"], []).append(r)
    if len(by_prev.get(GENESIS, [])) != 1:
        return {"intact": False, "length": n, "broken_at": None,
                "reason": "expected exactly one GENESIS record, found %d" % len(by_prev.get(GENESIS, []))}

    seen = 0
    tip = GENESIS
    while True:
        nxt = by_prev.get(tip, [])
        if not nxt:
            break
        if len(nxt) > 1:
            return {"intact": False, "length": n, "broken_at": tip,
                    "reason": "fork: %d records share prev_hash %s" % (len(nxt), tip[:12])}
        cur = nxt[0]
        seen += 1
        tip = cur["chain_hash"]
    if seen != n:
        return {"intact": False, "length": n, "broken_at": None,
                "reason": "chain covers %d of %d records (orphaned / gap / reordered)" % (seen, n)}

    return {"intact": True, "length": n, "broken_at": None, "reason": "chain intact: %d linked records" % n}


def handler(event, context):
    e = event or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {}
    return verify(e.get("records", []))


if __name__ == "__main__":
    data = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(sys.stdin)
    if isinstance(data, dict):
        data = data.get("records", [])
    result = verify(data)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["intact"] else 1)
