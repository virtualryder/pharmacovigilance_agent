"""Offline logic tests for the PHI telemetry-leak canary (scripts/pii_canary.py). The live sweeps use
boto3 and run only during EP1; here we test the pure logic: marker minting, text sweep, and verdicts."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pii_canary", ROOT / "scripts" / "pii_canary.py")
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


def test_marker_is_unique_and_shaped():
    a, b = canary.make_marker(), canary.make_marker()
    assert a != b and a.startswith("CANARY-") and a.endswith("-TELEMETRYPROBE")


def test_canary_case_carries_the_marker_as_phi_shapes():
    m = canary.make_marker()
    case = canary.build_canary_case(m)
    assert m in case["source"] and "SSN 900-00-" in case["source"]


def test_sweep_counts_case_insensitively():
    m = "CANARY-ABCDEF123456-TELEMETRYPROBE"
    assert canary.sweep_text(f"...{m.lower()}... and {m} again", m) == 2
    assert canary.sweep_text("nothing here", m) == 0
    assert canary.sweep_text(None, m) == 0


def test_strict_verdict_fails_on_any_hit():
    assert canary.strict_verdict({"cloudwatch_logs": 0, "stepfunctions_history": 0, "xray": 0, "dlq": 0})["verdict"] == "PASS"
    v = canary.strict_verdict({"cloudwatch_logs": 0, "stepfunctions_history": 3, "xray": 0, "dlq": 0})
    assert v["verdict"] == "FAIL" and v["leaks"] == {"stepfunctions_history": 3}


def test_verdict_flags_must_be_clean_destinations():
    assert canary.verdict({"cloudwatch_logs": 0, "xray": 0, "dlq": 0, "stepfunctions_history": 0})["verdict"] == "PASS"
    assert canary.verdict({"cloudwatch_logs": 1, "xray": 0, "dlq": 0, "stepfunctions_history": 0})["verdict"] == "FAIL"
