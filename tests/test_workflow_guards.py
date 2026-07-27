"""P0-2 — deterministic workflow guards. Proves each machine-verifiable transition gate a Step Functions
controller would branch on: intake extraction, PROVEN de-identification (signed sanitized_ref, not a
boolean), honest openFDA background (no fabrication), a legal seriousness category, and the duplicate
HOLD. Fail-closed everywhere. Pure logic, no AWS."""
import json

from toolkit import call, make_sanitized_ref
import workflow_guards as g


def _g(name, **kw):
    return call("workflow_guards", {"guard": name, **kw})


# ── extracted ────────────────────────────────────────────────────────────────

def test_extracted_requires_suspect_product():
    assert _g("extracted", fields={"suspect_product": "atorvastatin"})["ok"] is True
    assert _g("extracted", fields={"event_terms": "rash"})["ok"] is False
    assert _g("extracted", fields={})["ok"] is False


# ── deidentified (P0-1 tie-in) ───────────────────────────────────────────────

def test_deidentified_requires_signed_ref_not_boolean():
    assert _g("deidentified", sanitized_ref=make_sanitized_ref())["ok"] is True
    assert _g("deidentified", deidentified=True)["ok"] is False          # boolean is not proof
    assert _g("deidentified")["ok"] is False


# ── background (P0-4 anti-fabrication) ───────────────────────────────────────

def test_background_optional_and_honest():
    assert _g("background")["ok"] is True                                  # absent is allowed (context only)
    assert _g("background", lookup={"found": False, "authoritative": False})["ok"] is True
    assert _g("background", lookup={"found": True, "authoritative": True,
                                    "top_reactions": ["rhabdomyolysis"]})["ok"] is True
    # claims authoritative but carries no aggregate terms -> possible fabrication -> fail-closed
    assert _g("background", lookup={"found": True, "authoritative": True})["ok"] is False


# ── rules_executed ───────────────────────────────────────────────────────────

def test_rules_executed_requires_legal_category():
    assert _g("rules_executed", assessment={"assessed": True, "reporting_category": "EXPEDITED"})["ok"] is True
    assert _g("rules_executed", assessment={"assessed": False})["ok"] is False
    assert _g("rules_executed", assessment={"assessed": True, "reporting_category": "NONSENSE"})["ok"] is False


# ── duplicate HOLD ───────────────────────────────────────────────────────────

def test_duplicate_holds():
    assert _g("duplicate", duplicate={"duplicate_status": "UNIQUE"})["ok"] is True
    assert _g("duplicate", duplicate={"duplicate_status": "DUPLICATE", "hold": True})["ok"] is False


# ── controller invariants ────────────────────────────────────────────────────

def test_unknown_guard_fails_closed():
    assert _g("does_not_exist")["ok"] is False


def test_guard_error_fails_closed():
    # a non-dict `fields` would raise inside the guard; the handler must convert it to a closed deny
    r = _g("rules_executed", assessment="{not json")
    assert r["ok"] is False
