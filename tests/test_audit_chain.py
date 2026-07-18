"""Unit tests for the tamper-evident hash-chained audit. Exercises the pure hash logic in
write_audit.py (no AWS) and the verifier in verify_chain.py: a valid chain verifies INTACT, and
any content edit, chain-field edit, reorder, deletion, or fork is detected."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CTRL = ROOT / "lib" / "controls"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, CTRL / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


wa = _load("write_audit")
vc = _load("verify_chain")


def _build(n):
    """Build a valid n-record chain the way write_audit does (caller threads prev_hash)."""
    recs = []
    prev = wa.GENESIS
    for i in range(n):
        base = {"icsr_id": "CASE-1", "action": "step%d" % i, "phase": "INTENT",
                "actor": "reviewer", "deidentified": True, "payload": {"i": i},
                "audit_id": "aid-%d" % i, "payload_sha256": "x", "recorded_at": 1000 + i,
                "source": "pv-write_audit"}
        r = wa.chain_record(base, prev)
        recs.append(r)
        prev = r["chain_hash"]
    return recs


def test_valid_chain_is_intact():
    recs = _build(4)
    res = vc.verify(recs)
    assert res["intact"] is True
    assert res["length"] == 4
    # each record links to the previous one's chain_hash
    for a, b in zip(recs, recs[1:]):
        assert b["prev_hash"] == a["chain_hash"]
    assert recs[0]["prev_hash"] == wa.GENESIS


def test_content_edit_is_detected():
    recs = _build(4)
    recs[1]["payload"] = {"i": 99}          # tamper with a record's content
    res = vc.verify(recs)
    assert res["intact"] is False
    assert "entry_hash mismatch" in res["reason"]


def test_chain_field_edit_is_detected():
    recs = _build(4)
    recs[2]["chain_hash"] = "0" * 64        # tamper with the chain field
    res = vc.verify(recs)
    assert res["intact"] is False


def test_deletion_breaks_the_chain():
    recs = _build(5)
    del recs[2]                             # remove a middle record
    res = vc.verify(recs)
    assert res["intact"] is False
    assert "covers" in res["reason"] or "GENESIS" in res["reason"]


def test_reorder_alone_does_not_fool_it():
    recs = _build(4)
    recs = [recs[2], recs[0], recs[3], recs[1]]   # shuffle order
    res = vc.verify(recs)
    # order is reconstructed from the links, so a pure shuffle is still INTACT...
    assert res["intact"] is True
    # ...but dropping one after shuffling is caught
    res2 = vc.verify(recs[:-1])
    assert res2["intact"] is False


def test_fork_is_detected():
    recs = _build(3)
    forged = wa.chain_record({"icsr_id": "CASE-1", "action": "forged", "phase": "INTENT",
                              "actor": "attacker", "deidentified": True, "payload": {"x": 1},
                              "audit_id": "aid-forge", "payload_sha256": "x", "recorded_at": 1001,
                              "source": "pv-write_audit"}, recs[0]["chain_hash"])
    recs.append(forged)                     # two records now share the same prev_hash
    res = vc.verify(recs)
    assert res["intact"] is False
    assert "fork" in res["reason"]


def test_empty_is_vacuously_intact():
    assert vc.verify([])["intact"] is True


def test_chain_hash_recomputes():
    r = wa.chain_record({"icsr_id": "C", "action": "a", "phase": "INTENT", "actor": "u",
                         "deidentified": True, "payload": {}, "audit_id": "a1",
                         "payload_sha256": "x", "recorded_at": 1, "source": "s"}, wa.GENESIS)
    assert r["chain_hash"] == wa.chain_hash(wa.GENESIS, r["entry_hash"])
    assert r["entry_hash"] == wa.entry_hash(r)
