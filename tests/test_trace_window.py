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


# ---- PAR-4: the fallback must not drift away from the shared library ----------------------------
# governed-core 1.11.0 shipped `governed_core.proofs` so this fix would live in ONE place. For four
# weeks nothing imported it, so it was a fifth copy rather than a de-forked harness. trace_case now
# consumes `governed_core.proofs.window_ms` when the pinned core is importable and keeps the local
# definition only as a fallback for an operator without it. Two copies of a rule is exactly the
# condition that produced L39 in the first place, so the agreement is asserted rather than assumed.

def _local_window_ms(start, end):
    """The fallback body, reached by exec'ing trace_case with governed_core hidden."""
    import builtins
    import importlib.util as _u
    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name == "governed_core" or name.startswith("governed_core."):
            raise ImportError("hidden for this test")
        return real_import(name, *a, **k)

    builtins.__import__ = _blocked
    try:
        spec = _u.spec_from_file_location("tc_fallback", os.path.join(ROOT, "scripts", "trace_case.py"))
        mod = _u.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        builtins.__import__ = real_import
    assert mod._WINDOW_MS_SOURCE.startswith("local fallback"), mod._WINDOW_MS_SOURCE
    return mod._window_ms(start, end)


def test_trace_case_consumes_the_shared_proof_library_when_the_core_is_installed():
    pytest.importorskip("governed_core.proofs")
    assert tc._WINDOW_MS_SOURCE == "governed_core.proofs", (
        "the pinned core is installed but trace_case is still using its own copy of window_ms - "
        "PAR-4's shared library is not being consumed (%s)" % tc._WINDOW_MS_SOURCE)


@pytest.mark.parametrize("start,end", [
    (_2026_S, _2026_S + 463),
    (_2026_MS, _2026_MS + 463000),
    (_2026_S, _2026_MS + 1000),
])
def test_the_local_fallback_agrees_with_the_shared_library(start, end):
    pytest.importorskip("governed_core.proofs")
    from governed_core.proofs import window_ms
    assert _local_window_ms(start, end) == window_ms(start, end)


@pytest.mark.parametrize("start,end", [
    (1_500_000_000_000, 1_500_000_400_000),   # pre-2020
    (_2026_MS, _2026_MS),                     # empty
    (_2026_MS + 1000, _2026_MS),              # inverted
])
def test_the_local_fallback_rejects_exactly_what_the_shared_library_rejects(start, end):
    pytest.importorskip("governed_core.proofs")
    from governed_core.proofs import window_ms
    with pytest.raises(ValueError):
        window_ms(start, end)
    with pytest.raises(ValueError):
        _local_window_ms(start, end)
