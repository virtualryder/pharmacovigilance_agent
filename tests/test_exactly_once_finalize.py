"""Exactly-once finalization — the commit gate that prevents double-reporting an ICSR.

This control was implemented in edu_financial_aid_agent and Housing_eligibility_agent and never
reached this repo, while the IaC comment, deployment guide, evidence file and threat model all
described it as present (found 2026-08-03). Ported and now tested here.

What is actually being asserted: a second finalize of the same case — a retried Lambda, a replayed
Step Functions execution, or a second approval path — must NOT write a second COMMITTED record, and
must return the ORIGINAL submission id.
"""
import importlib
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
    """Minimal DynamoDB table honouring attribute_not_exists on the partition key."""

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
        item = self.store.get(Key["audit_id"])
        return {"Item": item} if item else {}


@pytest.fixture
def finalize(monkeypatch):
    """Load finalize_signoff with boto3 and evidence stubbed out."""
    store = {}
    committed = []

    fake_table = FakeTable(store)

    class FakeResource:
        def Table(self, name):
            return fake_table

    # governed-core 1.5.0 added G2 approval-path verification to finalize (it now REFUSES an
    # unverified approval). Approval verification has its own coverage; THIS test isolates the
    # exactly-once FINAL# marker, so it uses the handler's documented sandbox escape to bypass
    # the approval gate (SIGNOFF_ALLOW_UNVERIFIED) and exercise the commit-once logic directly.
    monkeypatch.setenv("SIGNOFF_ALLOW_UNVERIFIED", "true")

    fake_boto3 = type("b3", (), {"resource": staticmethod(lambda *a, **k: FakeResource())})
    fake_exc = type("m", (), {"ClientError": _CondFail})

    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", fake_exc)

    fake_evidence = type("ev", (), {})()

    def record_event(entry, context, source=None):
        committed.append(entry)
        return {"stored": True, "audit_id": "A%d" % len(committed),
                "chain_hash": "deadbeef", "seq": len(committed), "worm": True}

    fake_evidence.record_event = record_event
    # governed-core 1.6.0: finalize binds the signed tenant pair and routes the ledger through evidence
    fake_evidence.bind_tenant = lambda event: None
    fake_evidence.route_table = lambda name, logical: name
    monkeypatch.setitem(sys.modules, "evidence", fake_evidence)

    mod = importlib.import_module("finalize_signoff")
    importlib.reload(mod)
    return mod, store, committed, fake_table


def test_first_finalize_commits_and_writes_the_marker(finalize):
    mod, store, committed, _ = finalize
    out = mod.handler({"case_id": "ICSR-1", "requester": "alice", "approver": "bob"}, None)

    assert out["committed"] is True
    assert not out.get("idempotent")
    assert "FINAL#ICSR-1" in store, "the commit-gate marker was not written"
    assert len(committed) == 1, "expected exactly one COMMITTED evidence record"


def test_replayed_finalize_writes_no_second_committed_record(finalize):
    """The double-reporting guard. This is the assertion that matters."""
    mod, store, committed, _ = finalize
    ev = {"case_id": "ICSR-1", "requester": "alice", "approver": "bob"}

    first = mod.handler(ev, None)
    second = mod.handler(ev, None)          # retried Lambda / replayed execution

    assert second["committed"] is True
    assert second["idempotent"] is True, "second finalize was not recognised as a replay"
    assert second["submission_id"] == first["submission_id"], \
        "replay returned a DIFFERENT submission id — that is a second submission"
    assert len(committed) == 1, (
        "a second COMMITTED evidence record was written — this is the double-reporting defect "
        "the FINAL# marker exists to prevent"
    )


def test_a_different_approver_cannot_create_a_second_submission(finalize):
    """A second approval path must not produce a second submission for the same case."""
    mod, store, committed, _ = finalize
    first = mod.handler({"case_id": "ICSR-9", "requester": "alice", "approver": "bob"}, None)
    second = mod.handler({"case_id": "ICSR-9", "requester": "alice", "approver": "carol"}, None)

    assert second["idempotent"] is True
    assert second["submission_id"] == first["submission_id"]
    assert len(committed) == 1


def test_distinct_cases_each_finalize_independently(finalize):
    mod, store, committed, _ = finalize
    a = mod.handler({"case_id": "ICSR-A", "requester": "alice", "approver": "bob"}, None)
    b = mod.handler({"case_id": "ICSR-B", "requester": "alice", "approver": "bob"}, None)

    assert a["submission_id"] != b["submission_id"]
    assert len(committed) == 2
    assert {"FINAL#ICSR-A", "FINAL#ICSR-B"} <= set(store)
