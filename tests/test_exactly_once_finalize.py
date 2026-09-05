"""Exactly-once finalization - the commit gate that prevents committing the same benefits determination twice.

This control was implemented in edu_financial_aid_agent and Housing_eligibility_agent and never
reached this repo, while the IaC comment, deployment guide, evidence file and threat model all
described it as present (found 2026-08-03). Ported and tested here.

What is actually asserted (updated 2026-09-05 for governed-core 1.10.0). #159 made the COMMITTED
write EVIDENCE-FIRST (the WORM/hash-chained record is written before the FINAL# marker), so the two
distinct guarantees a second finalize of the same case must uphold are now tested separately:

  * a RETRIED / REPLAYED finalize (identical event) writes NO second COMMITTED record and returns the
    ORIGINAL submission id - the dedup comes from the evidence service's content-hash idempotency
    (audit_id = sha256(canonical(event))), and the FINAL# marker returns the original submission id;
  * a SECOND, DIFFERENT approver committing the same case is refused by G2 approval-path verification
    (the pending-approvals row is single-use CONSUMED and records exactly one approver) - NOT by the
    marker, which after #159 no longer gates the COMMITTED write.
"""
import hashlib
import importlib
import json
import sys
import pathlib

import pytest

CONTROLS = pathlib.Path(__file__).resolve().parent.parent / "lib" / "controls"
sys.path.insert(0, str(CONTROLS))


class _CondFail(Exception):
    """Stands in for botocore ClientError with ConditionalCheckFailedException."""

    def __init__(self):
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    """Minimal DynamoDB table honouring attribute_not_exists on the partition key. Serves both the
    audit/marker table (audit_id key) and the pending-approvals register (case_id key)."""

    def __init__(self, store):
        self.store = store
        self.put_calls = 0

    def put_item(self, Item, ConditionExpression=None):
        self.put_calls += 1
        key = Item["audit_id"]
        if ConditionExpression and "attribute_not_exists" in ConditionExpression \
                and key in self.store:
            raise _CondFail()
        self.store[key] = dict(Item)
        return {}

    def get_item(self, Key):
        k = Key.get("audit_id", Key.get("case_id"))
        item = self.store.get(k)
        return {"Item": item} if item else {}


def _idempotent_record_event(committed, seen):
    """A fake evidence.record_event that models the REAL service's content-hash idempotency: an
    identical logical event (an exact replay) is deduped (append-only immutable proof), not appended
    a second time. governed-core keys audit_id = sha256(canonical(event)); we approximate with a
    stable digest of the entry."""
    def record_event(entry, context, source=None):
        eid = hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        if eid in seen:
            return {"stored": False, "worm": True, "audit_id": seen[eid],
                    "reason": "append-only: this exact record is already recorded (immutable)"}
        committed.append(entry)
        seen[eid] = "A%d" % len(committed)
        return {"stored": True, "audit_id": seen[eid], "chain_hash": "deadbeef",
                "seq": len(committed), "worm": True}
    return record_event


def _make_finalize(monkeypatch, allow_unverified):
    store, committed, seen = {}, [], {}
    fake_table = FakeTable(store)

    class FakeResource:
        def Table(self, name):
            return fake_table

    fake_boto3 = type("b3", (), {"resource": staticmethod(lambda *a, **k: FakeResource())})
    fake_exc = type("m", (), {"ClientError": _CondFail})
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", fake_exc)
    if allow_unverified:
        monkeypatch.setenv("SIGNOFF_ALLOW_UNVERIFIED", "true")
    else:
        monkeypatch.delenv("SIGNOFF_ALLOW_UNVERIFIED", raising=False)

    fake_evidence = type("ev", (), {})()
    fake_evidence.record_event = _idempotent_record_event(committed, seen)
    # governed-core 1.6.0: finalize binds the signed tenant pair and routes the ledger through evidence
    fake_evidence.bind_tenant = lambda event: None
    fake_evidence.route_table = lambda name, logical: name
    # governed-core 1.10.0 (#162): finalize recomputes the approval binding; the stub returns a stable
    # value (the seeded pending rows carry no binding, so it is computed but not enforced).
    fake_evidence.approval_binding = lambda fields=None: "bind-stub"
    fake_evidence.is_durable = lambda res: bool(
        (res.get("stored") or res.get("replay") or "already recorded" in (res.get("reason") or "")) and res.get("worm"))
    monkeypatch.setitem(sys.modules, "evidence", fake_evidence)

    mod = importlib.import_module("finalize_signoff")
    importlib.reload(mod)
    return mod, store, committed


@pytest.fixture
def finalize(monkeypatch):
    """Load finalize_signoff with boto3 and evidence stubbed out.

    governed-core 1.5.0 added G2 approval-path verification to finalize (it REFUSES an unverified
    approval). Approval verification has its own coverage; the replay/first/distinct tests isolate the
    exactly-once + evidence-idempotency logic, so they use the handler's documented sandbox escape
    (SIGNOFF_ALLOW_UNVERIFIED) to bypass the approval gate and exercise the commit path directly."""
    return _make_finalize(monkeypatch, allow_unverified=True)


@pytest.fixture
def finalize_strict(monkeypatch):
    """Same stubs, but WITHOUT the sandbox override and with the pending-approvals register readable,
    so G2 approval-path verification actually runs - the guard that stops a SECOND approver from
    committing the same case now that the COMMITTED write is evidence-first (#159)."""
    return _make_finalize(monkeypatch, allow_unverified=False)


def test_first_finalize_commits_and_writes_the_marker(finalize):
    mod, store, committed = finalize
    out = mod.handler({"case_id": "CASE-1", "requester": "alice", "approver": "bob"}, None)

    assert out["committed"] is True
    assert not out.get("idempotent")
    assert "FINAL#CASE-1" in store, "the commit-gate marker was not written"
    assert len(committed) == 1, "expected exactly one COMMITTED evidence record"


def test_replayed_finalize_writes_no_second_committed_record(finalize):
    """The double-reporting guard for an exact replay. This is the assertion that matters."""
    mod, store, committed = finalize
    ev = {"case_id": "CASE-1", "requester": "alice", "approver": "bob"}

    first = mod.handler(ev, None)
    second = mod.handler(ev, None)          # retried Lambda / replayed execution

    assert second["committed"] is True
    assert second["idempotent"] is True, "second finalize was not recognised as a replay"
    assert second["submission_id"] == first["submission_id"], \
        "replay returned a DIFFERENT submission id - that is a second submission"
    assert len(committed) == 1, (
        "a second COMMITTED evidence record was written - this is the double-reporting defect the "
        "evidence-first ordering + content-hash idempotency exist to prevent"
    )


def test_a_second_approver_is_refused_by_the_approval_path(finalize_strict):
    """After #159 the FINAL# marker no longer gates the COMMITTED write; the guard against a SECOND
    approver committing the same case is G2 approval-path verification. With the sandbox override OFF,
    a case whose single-use approval was CONSUMED by bob refuses a finalize by carol, so no second
    COMMITTED record is written for the case."""
    mod, store, committed = finalize_strict
    # the case's single-use approval was CONSUMED by bob (as approve_signoff would have recorded it)
    store["CASE-9"] = {"case_id": "CASE-9", "status": "CONSUMED", "approver": "bob"}

    first = mod.handler({"case_id": "CASE-9", "requester": "alice", "approver": "bob"}, None)
    assert first["committed"] is True and not first.get("idempotent")

    second = mod.handler({"case_id": "CASE-9", "requester": "alice", "approver": "carol"}, None)
    assert second.get("refused") is True, "a different approver was not refused by G2"
    assert second.get("committed") is False
    committed_records = [e for e in committed if e.get("phase") == "COMMITTED"]
    assert len(committed_records) == 1, (
        "exactly one COMMITTED evidence record must exist for the case; the second approver may "
        "only produce a DENIED record"
    )


def test_distinct_cases_each_finalize_independently(finalize):
    mod, store, committed = finalize
    a = mod.handler({"case_id": "CASE-A", "requester": "alice", "approver": "bob"}, None)
    b = mod.handler({"case_id": "CASE-B", "requester": "alice", "approver": "bob"}, None)

    assert a["submission_id"] != b["submission_id"]
    assert len(committed) == 2
    assert {"FINAL#CASE-A", "FINAL#CASE-B"} <= set(store)