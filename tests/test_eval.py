"""Eval / regression harness for the pharmacovigilance seriousness rules engine (assess_seriousness).

Golden cases pin the ICH E2B(R3) seriousness + reporting-clock DETERMINATION so a rules change fails CI.
Basis: serious per ICH E2B(R3)/21 CFR 314.80 (death, life-threatening, hospitalization, disability,
congenital anomaly, other medically important); serious + unexpected -> EXPEDITED 15-day; serious +
listed -> PERIODIC; non-serious -> ROUTINE.
"""
import pytest
from toolkit import call

GOLDEN = [
    ("serious_unexpected_expedited",
     {"flags": {"hospitalization": True}, "expectedness": "unlisted", "deidentified": True},
     {"serious": True, "reporting_category": "EXPEDITED", "clock_days": 15}),
    ("death_expedited",
     {"flags": {"death": True}, "expectedness": "unlisted", "deidentified": True},
     {"serious": True, "reporting_category": "EXPEDITED", "clock_days": 15}),
    ("serious_listed_periodic",
     {"flags": {"hospitalization": True}, "expectedness": "listed", "deidentified": True},
     {"serious": True, "reporting_category": "PERIODIC", "clock_days": None}),
    ("non_serious_routine",
     {"case": "mild transient headache, resolved", "expectedness": "listed", "deidentified": True},
     {"serious": False, "reporting_category": "ROUTINE"}),
]

NEGATIVE = [
    ("assess_unmasked", "assess_seriousness",
     {"flags": {"death": True}, "deidentified": False},
     lambda r: r["assessed"] is False),
    ("causality_unmasked", "record_causality",
     {"assessment": "related", "rationale": "positive dechallenge documented", "deidentified": False},
     lambda r: r["prepared"] is False),
]


@pytest.mark.parametrize("label,inp,expected", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden_determination(label, inp, expected):
    r = call("assess_seriousness", inp)
    for k, v in expected.items():
        assert r.get(k) == v, f"{label}: {k} expected {v!r}, got {r.get(k)!r}"


@pytest.mark.parametrize("label,tool,inp,check", NEGATIVE, ids=[n[0] for n in NEGATIVE])
def test_negative_fail_closed(label, tool, inp, check):
    assert check(call(tool, inp)), f"{label}: fail-closed guard did not hold"
