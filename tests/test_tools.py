"""Unit tests for the pharmacovigilance governed tools — contract + fail-closed behavior. No AWS."""
from toolkit import call, make_sanitized_ref


def test_intake_extracts_fields():
    r = call("intake_icsr", {"source": "Suspect product: atorvastatin. Patient hospitalized with rhabdomyolysis. Unexpected."})
    assert r["fields"]["suspect_product"]
    assert r["fields"]["seriousness_flags"]["hospitalization"] is True


def test_assess_fail_closed_on_unmasked():
    r = call("assess_seriousness", {"case": "hospitalized", "deidentified": False})
    assert r["assessed"] is False


def test_assess_serious_expedited():
    r = call("assess_seriousness", {"flags": {"hospitalization": True}, "expectedness": "unlisted",
                                    "sanitized_ref": make_sanitized_ref()})
    assert r["serious"] is True
    assert r["reporting_category"] == "EXPEDITED"
    assert r["clock_days"] == 15


def test_assess_flags_as_json_string_matches_manifest_schema():
    """The manifest types `flags` as a JSON *string*; the agent path sends it that way. Regression for
    the pv-mt e2e sweep of 2026-09-03 (AttributeError: 'str' object has no attribute 'get')."""
    r = call("assess_seriousness", {"flags": '{"hospitalization": true}', "expectedness": "unlisted",
                                    "sanitized_ref": make_sanitized_ref()})
    assert r["assessed"] is True and r["serious"] is True
    assert r["reporting_category"] == "EXPEDITED" and any("hospitalization" in c for c in r["criteria_met"])
    # explicit False in the string form still overrides the text scan
    r = call("assess_seriousness", {"case": "patient was hospitalized", "flags": '{"hospitalization": false}',
                                    "sanitized_ref": make_sanitized_ref(text="patient was hospitalized")})
    assert r["serious"] is False
    # malformed / non-object flags never crash: the text scan decides
    for bad in ("not json", "[1,2]", "null", "", 7, ["death"]):
        r = call("assess_seriousness", {"case": "the patient died", "flags": bad,
                                        "sanitized_ref": make_sanitized_ref(text="the patient died")})
        assert r["assessed"] is True, bad
        assert r["serious"] is True and any("death" in c for c in r["criteria_met"]), bad


def test_detect_duplicate():
    r = call("detect_duplicate", {"case_key": "a|b|c|d", "known_keys": "a|b|c|d; x|y|z|w"})
    assert r["duplicate_status"] == "DUPLICATE"
    assert r["hold"] is True


def test_record_causality_requires_rationale():
    r = call("record_causality", {"assessment": "related", "sanitized_ref": make_sanitized_ref()})
    assert r["prepared"] is False


def test_record_causality_prepared():
    r = call("record_causality", {"assessment": "probably related", "rationale": "positive dechallenge and temporal association",
                                  "sanitized_ref": make_sanitized_ref()})
    assert r["status"] == "PREPARED"
    assert r["requires_senior_approval"] is True


def test_core_finalize_refused():
    assert call("pv_core", {"icsr_id": "ICSR-1"})["submitted"] is False


def test_core_commit_causality_refused():
    assert call("pv_core", {"causality_id": "CAUS-1"})["committed"] is False


# -- L20b: negation-aware seriousness / expectedness (the L20 class, 2026-09-06) --------------------
# "no hospitalization required" matched `hospitalization` and flagged the case serious; "not
# life-threatening" flagged life_threatening. For PV, over-flagging is the SAFE direction - it
# escalates to a human and at worst files a report that was not required - so this was never a
# patient-safety bug. It is still wrong data: an ICSR that records a criterion the narrative rules
# OUT misstates the reporter's own account and cannot be defended to an inspector.

NEGATED_SERIOUSNESS = [
    ("Patient recovered at home; no hospitalization required.", "hospitalization"),
    ("Event was not life-threatening.", "life_threatening"),
    ("Negative for congenital anomaly.", "congenital_anomaly"),
    ("No disability or incapacity reported.", "disability"),
]

ASSERTED_SERIOUSNESS = [
    ("Patient hospitalized for three days with rhabdomyolysis.", "hospitalization"),
    ("The reaction was life-threatening and required intensive care.", "life_threatening"),
    ("Reporter describes a congenital anomaly in the neonate.", "congenital_anomaly"),
]


def test_seriousness_flags_are_negation_aware():
    """A criterion the narrative rules OUT must not be recorded as ruling it IN."""
    for text, flag in NEGATED_SERIOUSNESS:
        r = call("intake_icsr", {"source": text})
        assert r["fields"]["seriousness_flags"][flag] is False, (text, flag)


def test_seriousness_flags_still_fire_when_actually_asserted():
    """...and the negation guard must not suppress a real seriousness criterion."""
    for text, flag in ASSERTED_SERIOUSNESS:
        r = call("intake_icsr", {"source": text})
        assert r["fields"]["seriousness_flags"][flag] is True, (text, flag)


def test_expectedness_is_negation_aware():
    """"not unexpected" must not read as unlisted; "not listed" must not read as listed."""
    assert call("intake_icsr", {"source": "The reaction was not unexpected."})["fields"]["expectedness"] != "unlisted"
    assert call("intake_icsr", {"source": "This reaction is not listed in the RSI."})["fields"]["expectedness"] != "listed"
    assert call("intake_icsr", {"source": "Unexpected reaction."})["fields"]["expectedness"] == "unlisted"


def test_expectedness_degrades_to_unknown_on_a_mixed_clause():
    """KNOWN LIMITATION, asserted so it cannot change silently.

    negation.asserted() reads a fixed clause window, so a negation cue that negates something ELSE
    in the same clause still suppresses the term. "Unexpected reaction, not in the RSI" is a human
    reader's UNLISTED, but "not" sits inside the window and the term is treated as unasserted.

    The result is `unknown`, not `listed` - the extractor declines to decide and the case reaches a
    qualified reviewer. That is the direction this heuristic is built to fail in, and it is why the
    window is not widened to guess: a wrong `listed` would suppress an expedited report, while
    `unknown` only costs a human look. Fixing this properly needs real clause parsing, not a wider
    regex window."""
    assert call("intake_icsr", {"source": "Unexpected reaction, not in the RSI."})["fields"]["expectedness"] == "unknown"
