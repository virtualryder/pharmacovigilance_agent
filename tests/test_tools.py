"""Unit tests for the pharmacovigilance governed tools — contract + fail-closed behavior. No AWS."""
from toolkit import call


def test_intake_extracts_fields():
    r = call("intake_icsr", {"source": "Suspect product: atorvastatin. Patient hospitalized with rhabdomyolysis. Unexpected."})
    assert r["fields"]["suspect_product"]
    assert r["fields"]["seriousness_flags"]["hospitalization"] is True


def test_assess_fail_closed_on_unmasked():
    r = call("assess_seriousness", {"case": "hospitalized", "deidentified": False})
    assert r["assessed"] is False


def test_assess_serious_expedited():
    r = call("assess_seriousness", {"flags": {"hospitalization": True}, "expectedness": "unlisted", "deidentified": True})
    assert r["serious"] is True
    assert r["reporting_category"] == "EXPEDITED"
    assert r["clock_days"] == 15


def test_detect_duplicate():
    r = call("detect_duplicate", {"case_key": "a|b|c|d", "known_keys": "a|b|c|d; x|y|z|w"})
    assert r["duplicate_status"] == "DUPLICATE"
    assert r["hold"] is True


def test_record_causality_requires_rationale():
    r = call("record_causality", {"assessment": "related", "deidentified": True})
    assert r["prepared"] is False


def test_record_causality_prepared():
    r = call("record_causality", {"assessment": "probably related", "rationale": "positive dechallenge and temporal association", "deidentified": True})
    assert r["status"] == "PREPARED"
    assert r["requires_senior_approval"] is True


def test_core_finalize_refused():
    assert call("pv_core", {"icsr_id": "ICSR-1"})["submitted"] is False


def test_core_commit_causality_refused():
    assert call("pv_core", {"causality_id": "CAUS-1"})["committed"] is False
