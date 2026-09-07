"""L31: correlation of aegis.call lines must happen in Python, not in an Insights OR-chain.

Gate attempt 15 failed LIN_zero_orphans on `invoked_not_audited:write_audit` after a full 906s
settle. The audit line was never missing - it sat in /aws/lambda/pv-fp-write-audit at 11:02:37.666,
inside the window, carrying the case_id, the trace_id AND the execution_arn. The reader could not
see it: probed against that one log group, each `like` term matched alone and any PAIR matched, but
the three-term disjunction the reader built returned zero rows, reproducibly. The proof got weaker
the more correlation keys it had, and it lost `write_audit` - the evidence writer itself.

These tests pin the behaviour that replaced it: the server is asked only for the cheap invariant,
and key matching happens here, where it can be tested without CloudWatch.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import trace_case as tc  # noqa: E402

CASE = "LIN-E02DBE"
TRACE = "6a9e99c2a2d110c3facef6271573ff14"
EXEC = "arn:aws:states:us-east-1:1234:execution:pv-fp-determination-workflow:lineage-lin-e02dbe"

# the real write_audit line from the failing run, abbreviated but structurally identical
WRITE_AUDIT = ('{"aegis": "call", "args_sha256": "a9920d77", "case_id": "%s", '
               '"execution_arn": "%s", "outcome": "stored=True", "tool": "write_audit", '
               '"trace_id": "%s", "ts": 1788778957666}' % (CASE, EXEC, TRACE))
OTHER_CASE = ('{"aegis": "call", "args_sha256": "ffff", "case_id": "OTHER-1", '
              '"tool": "mask_pii", "trace_id": "deadbeef", "ts": 1}')
NOT_JSON = "START RequestId: 6b3106ce-1d3e-492c-9ea8-b7ef2e4c4f35 Version: $LATEST"


class _NotFound(Exception):
    pass


class _Logs:
    """Stands in for CloudWatch Logs. L33: the reader must use the plain scan, never the query
    engine - so start_query raises if anything reaches for it."""

    class exceptions:
        ResourceNotFoundException = _NotFound

    def __init__(self, messages):
        self.messages = messages
        self.scans = []

    def filter_log_events(self, **kw):
        self.scans.append(kw)
        return {"events": [{"message": m} for m in self.messages]}

    def start_query(self, **kw):                      # pragma: no cover - must never be called
        raise AssertionError("read_lambda_calls must not use CloudWatch Insights (L33)")


def _read(messages, keys, case_id=CASE):
    logs = _Logs(messages)
    rows = tc.read_lambda_calls(logs, ["/aws/lambda/pv-fp-write-audit"], case_id, keys, 0, 1)
    return rows, logs.scans[0]


def test_the_server_is_asked_only_for_a_window():
    """L33: no filter language reaches the server at all - just a log group and a time range."""
    _, scan = _read([WRITE_AUDIT], {"trace_id": [TRACE], "execution_arn": [EXEC], "session_id": []})
    assert scan["logGroupName"] == "/aws/lambda/pv-fp-write-audit"
    assert "startTime" in scan and "endTime" in scan
    assert not any("filter" in k.lower() or "query" in k.lower() for k in scan)


def test_write_audit_is_found_with_all_three_keys_present():
    """The exact shape that returned zero rows against live CloudWatch."""
    rows, _ = _read([WRITE_AUDIT], {"trace_id": [TRACE], "execution_arn": [EXEC], "session_id": []})
    assert [r["tool"] for r in rows] == ["write_audit"]


def test_each_key_alone_still_correlates():
    for keys in ({"trace_id": [TRACE]}, {"execution_arn": [EXEC]}, {}):
        rows, _ = _read([WRITE_AUDIT], keys)
        assert [r["tool"] for r in rows] == ["write_audit"], keys


def test_unrelated_lines_are_still_excluded():
    """Correlating client-side must not become 'accept everything in the window'."""
    rows, _ = _read([WRITE_AUDIT, OTHER_CASE, NOT_JSON],
                    {"trace_id": [TRACE], "execution_arn": [EXEC], "session_id": []})
    assert [r["tool"] for r in rows] == ["write_audit"]


def test_non_json_and_non_call_lines_are_dropped():
    rows, _ = _read([NOT_JSON, '{"aegis": "boot", "args_sha256": "x", "case_id": "%s"}' % CASE], {})
    assert rows == []
