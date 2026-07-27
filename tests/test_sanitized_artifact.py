"""P0-1 — server-issued sanitized-artifact references (ported from the financial-aid/housing control
plane). Proves the PHI de-identification gate now rests on a mask_pii-SIGNED reference (proof-of-masking)
with a CONTENT-BINDING hash — and that the previously spoofable `deidentified: true` boolean is no longer
accepted as proof by any tool. Pure logic, no AWS (the artifact store is in-memory)."""
import json

from toolkit import call, make_sanitized_ref
import sanitized

MASKED = "[REDACTED:NAME] hospitalized with rhabdomyolysis after [REDACTED:AGE] on atorvastatin"


def _ref(text=MASKED, store=None):
    return sanitized.mint_ref(text, engine="comprehend:DetectPiiEntities", entities_masked=2, store=store)


# ── the primitive ─────────────────────────────────────────────────────────────

def test_mint_then_verify_roundtrips():
    ref = _ref()
    assert ref["authoritative"] is True and ref["sig"]
    assert sanitized.verify_ref(ref) is True
    assert sanitized.verify_ref(json.dumps(ref)) is True   # JSON form (as it crosses the gateway)


def test_verify_rejects_forged_and_tampered_refs():
    ref = _ref()
    assert sanitized.verify_ref(dict(ref, sig="deadbeef" * 8)) is False
    assert sanitized.verify_ref(dict(ref, sanitized_sha256=sanitized.sha256_text("other"))) is False
    assert sanitized.verify_ref({"deidentified": True}) is False
    assert sanitized.verify_ref(True) is False
    assert sanitized.verify_ref(None) is False


def test_mint_without_secret_is_not_authoritative(monkeypatch):
    monkeypatch.delenv("PROVENANCE_SECRET", raising=False)
    ref = _ref()
    assert ref["authoritative"] is False
    assert sanitized.verify_ref(ref) is False               # fail-closed both ends


def test_store_roundtrip_and_content_binding():
    st = sanitized.MemoryStore()
    ref = _ref(store=st)
    assert ref["stored"] is True
    assert sanitized.load_text(ref, store=st) == MASKED                          # server-side channel
    assert sanitized.load_text(ref, candidate_text=MASKED, store=None) == MASKED # hash-bound candidate
    assert sanitized.load_text(ref, candidate_text="UNMASKED PHI: John Doe SSN 123-45-6789", store=None) is None


# ── the tools refuse the spoofed boolean (the P0-1 attack) ────────────────────

def test_spoofed_boolean_is_refused_by_assess():
    r = call("assess_seriousness", {"case": "unmasked: John Doe, SSN 123-45-6789", "deidentified": True,
                                    "flags": {"hospitalization": True}, "expectedness": "unlisted"})
    assert r["assessed"] is False


def test_spoofed_boolean_is_refused_by_causality():
    r = call("record_causality", {"assessment": "related", "rationale": "temporal association documented",
                                  "deidentified": True})
    assert r["prepared"] is False


def test_spoofed_boolean_is_refused_by_draft():
    r = call("pv_core", {"case": "unmasked: Jane Roe DOB 1990-01-01", "deidentified": True})
    assert r.get("drafted_by") is None and "refused" in r.get("error", "")


def test_valid_ref_is_accepted():
    ref = make_sanitized_ref()
    r = call("record_causality", {"assessment": "probably related",
                                  "rationale": "positive dechallenge and plausible mechanism", "sanitized_ref": ref})
    assert r["status"] == "PREPARED"


def test_draft_refuses_substituted_content():
    """A valid ref but a DIFFERENT (unmasked) case body must refuse — content binding defeats swap."""
    ref = make_sanitized_ref("[REDACTED:NAME] rash after amoxicillin")
    r = call("pv_core", {"case": "SUBSTITUTED unmasked John Doe SSN 123-45-6789", "sanitized_ref": ref})
    assert r.get("drafted_by") is None
    assert r.get("content_bound") is False
