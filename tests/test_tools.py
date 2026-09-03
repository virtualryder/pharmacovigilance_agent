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
