"""Phase 110 — the case-trace correlation tool's pure core: join keys from WORM rows, the merged
timeline across runtime spans / gateway / Lambda aegis.call lines / Bedrock model-invocation rows /
Step Functions, the masked-before-model check, and the per-tenant verdict. Offline fixtures."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("trace_case", ROOT / "scripts" / "trace_case.py")
tc = importlib.util.module_from_spec(spec); sys.modules["trace_case"] = tc; spec.loader.exec_module(tc)

WORM = [{"audit_id": "a1", "case_id": "C-1", "phase": "INTENT", "action": "mask_pii", "seq": 0, "recorded_at": 1000,
         "chain_hash": "abcdef0123456789", "tenant_id": "sp-a",
         "correlation": {"trace_id": "t1", "session_id": "s1", "mcp_session_id": "m1", "tenant": "sp-a"},
         "invocation": {"request_id": "r1", "span_id": "p1"}},
        {"audit_id": "a2", "case_id": "C-1", "phase": "INTENT", "action": "icsr-determination", "seq": 1, "recorded_at": 1010,
         "chain_hash": "0123", "tenant_id": "sp-a",
         "correlation": {"execution_arn": "arn:exec:1", "tenant": "sp-a"}, "invocation": {"request_id": "r2"}}]
SPANS = [{"traceId": "t1", "spanId": "p0", "name": "invoke_agent Strands Agents", "startTimeUnixNano": 990_000_000_000, "endTimeUnixNano": 1_020_000_000_000,
          "attributes": {"session.id": "s1", "gen_ai.operation.name": "invoke_agent", "tenant": "sp-a", "case_id": "C-1", "gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5}},
         {"traceId": "t1", "spanId": "p1", "name": "execute_tool mask-pii___mask_pii", "startTimeUnixNano": 999_000_000_000, "endTimeUnixNano": 1_001_000_000_000,
          "attributes": {"session.id": "s1", "gen_ai.tool.name": "mask_pii", "tenant": "sp-a"}},
         {"traceId": "t1", "spanId": "p2", "name": "chat m", "startTimeUnixNano": 995_000_000_000, "endTimeUnixNano": 998_000_000_000,
          "attributes": {"session.id": "s1", "gen_ai.request.model": "m", "aws.request_id": "b1"}, "_reasoning": [{"output": {"messages": [{"role": "assistant", "content": "call mask_pii"}]}}]}]
LAMBDA = [{"aegis": "call", "ts": 1_000_500, "tool": "mask_pii", "outcome": "deidentified=True", "trace_id": "t1", "session_id": "s1",
           "request_id": "r1", "tenant": "sp-a", "case_id": "C-1", "arg_keys": ["case"], "args_sha256": "d"},
          {"aegis": "call", "ts": 1_010_500, "tool": "write_audit", "outcome": "stored=True", "execution_arn": "arn:exec:1",
           "request_id": "r2", "tenant": "sp-a", "case_id": "C-1", "arg_keys": [], "args_sha256": "e"}]
MODEL = [{"schemaType": "ModelInvocationLog", "timestamp": "1970-01-01T00:16:36Z", "requestId": "b1", "operation": "Converse",
          "modelId": "m", "identity": {"arn": "arn:role"}, "requestMetadata": {"tenant": "sp-a", "session_id": "s1", "case_id": "C-1"},
          "input": {"inputBodyJson": {"messages": [{"content": [{"text": "household 3, SSN [REDACTED]"}]}]}, "inputTokenCount": 10},
          "output": {"outputBodyJson": {"output": {"message": {"content": [{"text": "Assessing eligibility."}]}}}, "outputTokenCount": 5}}]


def test_join_keys_union_across_worm_rows():
    k = tc.join_keys(WORM)
    assert k == {"trace_id": ["t1"], "session_id": ["s1"], "mcp_session_id": ["m1"], "execution_arn": ["arn:exec:1"],
                 "request_id": ["r1", "r2"], "tenant": ["sp-a"]}


def test_masked_before_model_check():
    assert tc.masked_check("household 3, SSN [REDACTED]")
    assert not tc.masked_check("Applicant Jane Q. Sample, SSN 900-12-3456")


def test_timeline_merges_every_source_with_join_keys_and_verdict():
    tr = tc.build_timeline("C-1", "sp-a", WORM, SPANS, [{"ts": 999_100, "summary": "tools/call mask_pii", "keys": {"session_id": "s1", "mcp_session_id": "m1"}}],
                           LAMBDA, MODEL, [{"timestamp": "1970-01-01T00:16:50Z", "type": "TaskStateEntered", "name": "AuditIntent", "execution_arn": "arn:exec:1"}])
    tr2 = tc.build_timeline("C-1", "sp-a", [], [{"traceId": "t", "spanId": "x", "name": "chat m", "startTimeUnixNano": 1, "endTimeUnixNano": None, "attributes": {}}], [], [], [], [])
    assert tr2["timeline"][0]["detail"]["duration_ms"] is None                                       # in-flight span tolerated
    srcs = [r["source"] for r in tr["timeline"]]
    assert set(srcs) == {"worm", "runtime-span", "gateway", "lambda", "bedrock-model-log", "stepfunctions"}
    assert [r["ts"] for r in tr["timeline"]] == sorted(r["ts"] for r in tr["timeline"])          # time-ordered
    v = tr["verdict"]
    assert v["worm_records"] == 2 and v["agent_spans"] == 1 and v["model_spans"] == 1 and v["tool_spans"] == 1
    assert v["lambda_calls"] == 2 and v["lambda_calls_joined_to_evidence"] == 2                   # trace_id and execution_arn joins
    assert v["model_invocations"] == 1 and v["model_invocations_tagged_tenant"] == 1 and v["masked_before_model_all"] is True
    assert v["model_invocations_joined_to_spans"] == 1                                            # aws.request_id == requestId
    assert v["sessions"] == ["s1"] and v["single_tenant"] is True
    model_row = next(r for r in tr["timeline"] if r["source"] == "bedrock-model-log")
    assert model_row["keys"]["tenant"] == "sp-a" and "Assessing" in model_row["detail"]["reasoning_excerpt"]
    md = tc.to_markdown(tr)
    assert "| masked_before_model_all | True |" in md and "runtime-span" in md


def test_verdict_flags_cross_tenant_rows_and_unmasked_model_input():
    leak = dict(MODEL[0]); leak = {**leak, "requestMetadata": {"tenant": "sp-b", "case_id": "C-1"},
                                   "input": {"inputBodyJson": {"messages": [{"content": [{"text": "SSN 900-12-3456"}]}]}}}
    tr = tc.build_timeline("C-1", "sp-a", WORM, [], [], [], [leak], [])
    v = tr["verdict"]
    assert v["masked_before_model_all"] is False and v["single_tenant"] is False and v["model_invocations_tagged_tenant"] == 0
