"""L39: the two CloudWatch time units, and the window that silently found nothing.

filter_log_events takes epoch MILLISECONDS. start_query takes epoch SECONDS. trace_case uses both,
read_lambda_calls was moved from the query engine to a plain scan (L33) without converting the
callers' seconds, and the result was a scan of 1970 that returned nothing on every run - reported
as `lambda_calls_logged: false`, which reads as a governed tool that never audited itself.

It survived two live gates because the callers disagree and only one is wrong:

    lineage_proof.py         computes ms   -> correct   (attempt 20: aegis=10, orphans=0, GREEN)
    obs_two_tenant_proof.py  computes s    -> 1970      (attempts 18 and 20: G111 RED)

Proven live against a standing environment before teardown: a raw scan found 7 aegis.call lines
carrying the case id across 7 log groups for EACH tenant; read_lambda_calls returned 0 before this
fix and 7 after, on identical inputs.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("tc_window", os.path.join(ROOT, "scripts", "trace_case.py"))
tc = importlib.util.module_from_spec(_spec)
sys.modules["tc_window"] = tc
_spec.loader.exec_module(tc)

_2026_S = 1788818472
_2026_MS = 1788818472000


def test_seconds_are_promoted_to_milliseconds():
    assert tc._window_ms(_2026_S, _2026_S + 463) == (_2026_MS, _2026_MS + 463000)


def test_milliseconds_pass_through_unchanged():
    assert tc._window_ms(_2026_MS, _2026_MS + 463000) == (_2026_MS, _2026_MS + 463000)


def test_a_pre_2020_window_raises_instead_of_returning_empty():
    """The point of the whole fix: a caller unit bug must not look like an absence of evidence."""
    with pytest.raises(ValueError) as e:
        tc._window_ms(1_500_000_000_000, 1_500_000_400_000)     # 2017, already in ms
    assert "before 2020" in str(e.value)


def test_an_inverted_or_empty_window_raises():
    with pytest.raises(ValueError):
        tc._window_ms(_2026_MS, _2026_MS)
    with pytest.raises(ValueError):
        tc._window_ms(_2026_MS + 1000, _2026_MS)


def test_read_lambda_calls_hands_milliseconds_to_filter_log_events():
    """The regression itself: seconds in, milliseconds must reach the API."""
    seen = {}

    class _Rec:
        class exceptions:
            class ResourceNotFoundException(Exception):
                pass

        def filter_log_events(self, **kw):
            seen.update(kw)
            return {"events": []}

    tc.read_lambda_calls(_Rec(), ["/aws/lambda/x"], "CASE-1", {}, _2026_S, _2026_S + 463)
    assert seen["startTime"] == _2026_MS, "seconds reached filter_log_events - the L39 defect"
    assert seen["endTime"] == _2026_MS + 463000
