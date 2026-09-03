#!/usr/bin/env python3
"""Phase 110 — ONE timeline for a case: every API call, the model's reasoning, and the WORM evidence,
joined by the correlation keys (tenant · session_id · trace_id · mcp_session_id · execution_arn ·
request_id · case_id) and tagged per tenant.

Sources (all CloudWatch Logs Insights / DynamoDB / Step Functions; read-only):
  * WORM ledger rows for the case (the acting tenant's ledger) -> their `correlation` blocks are the seed;
  * AgentCore Runtime spans + logs   (/aws/bedrock-agentcore/runtimes/<agent>-<endpoint>, aws/spans)
      -> the model's reasoning: invoke_agent / cycle / model-invoke / execute_tool spans by session.id;
  * Gateway vended request logs      (/aws/vendedlogs/bedrock-agentcore/gateway/<prefix>);
  * tool Lambda `aegis.call` lines   (/aws/lambda/<prefix>-*);
  * Bedrock model-invocation log     (/aws/bedrock/modelinvocations/<prefix>) by requestMetadata.case_id,
      each row checked against the PII canary set -> masked_before_model;
  * Step Functions execution history (when an execution_arn is in the keys).
Output: JSON (+ Markdown) — the artefact an auditor is handed. Pure boto3; offline-testable core
(`build_timeline`, `masked_check`, `join_keys`).

Usage: python scripts/trace_case.py --env mt3 --case-id C-1 --tenant sp-a [--runtime-log-group ...]
       [--since-minutes 120] [--out evidence/trace-C-1.json]"""
import argparse
import json
import re
import sys
import time

CANARY = ("900-12-3456", "Jane Q. Sample", "1990-01-01", "12 Elm St")
# Strands span names carry a suffix ("invoke_agent Strands Agents", "chat <model>", "execute_tool <tool>",
# "mcp tools/call <tool>") - classify by prefix. Seen live in aws/spans 2026-09-02.
SPAN_KINDS = (("invoke_agent", "agent"), ("execute_event_loop_cycle", "cycle"), ("chat", "model"),
              ("execute_tool", "tool"), ("mcp tools/call", "mcp-call"), ("mcp tools/list", "mcp-list"),
              ("AgentCore.Runtime.Invoke", "runtime-invoke"), ("POST /invocations", "runtime-http"))


def span_kind(span):
    a = span.get("attributes") or {}
    name = span.get("name", "") or ""
    op = a.get("gen_ai.operation.name") or ""
    for prefix, kind in SPAN_KINDS:
        if name.startswith(prefix) or op == prefix:
            return kind
    # Transaction Search puts the tool Lambdas' X-Ray segments into aws/spans under the SAME trace id
    # (the gateway propagates the runtime's trace to the Lambda invoke) - name them for the reader
    if "/LambdaService" in name or "/LambdaExecutionEnvironment" in name or name.startswith("Overhead/"):
        return "lambda-segment"
    return "span"


# ---------------------------------------------------------------- pure core ------------------------
def join_keys(worm_rows):
    """Union of the correlation keys across the case's WORM rows (seed for every other source)."""
    keys = {"trace_id": set(), "session_id": set(), "mcp_session_id": set(), "execution_arn": set(),
            "request_id": set(), "tenant": set()}
    for r in worm_rows:
        c = r.get("correlation") or {}
        for k in ("trace_id", "session_id", "mcp_session_id", "execution_arn", "tenant"):
            if c.get(k):
                keys[k].add(c[k])
        if (r.get("invocation") or {}).get("request_id"):
            keys["request_id"].add(r["invocation"]["request_id"])
    return {k: sorted(v) for k, v in keys.items()}


def masked_check(body_text, canary=CANARY):
    """True iff none of the raw-PII canaries appear in the model request/response body."""
    t = body_text or ""
    return not any(c.lower() in t.lower() for c in canary)


def _row(ts, source, kind, summary, keys, extra=None):
    d = {"ts": ts, "source": source, "kind": kind, "summary": summary, "keys": {k: v for k, v in keys.items() if v}}
    if extra:
        d["detail"] = extra
    return d


def build_timeline(case_id, tenant, worm_rows, spans, gateway_rows, lambda_calls, model_rows, sfn_events):
    """Merge every source into one time-ordered timeline; every row shows its join keys."""
    tl = []
    for r in worm_rows:
        c = r.get("correlation") or {}
        tl.append(_row(int(r.get("recorded_at", 0)) * 1000, "worm", "evidence",
                       "%s %s seq=%s chain=%s…" % (r.get("phase"), r.get("action"), r.get("seq"), str(r.get("chain_hash", ""))[:12]),
                       {"trace_id": c.get("trace_id"), "session_id": c.get("session_id"), "execution_arn": c.get("execution_arn"),
                        "request_id": (r.get("invocation") or {}).get("request_id"), "tenant": r.get("tenant_id"), "audit_id": r.get("audit_id")},
                       {"table": r.get("_table"), "worm_key": r.get("_key")}))
    for s in spans:
        a = s.get("attributes") or {}
        kind = span_kind(s)
        summ = s.get("name", "")
        if a.get("gen_ai.tool.name"):
            summ += " tool=%s" % a["gen_ai.tool.name"]
        if a.get("gen_ai.request.model"):
            summ += " model=%s in=%s out=%s" % (a["gen_ai.request.model"], a.get("gen_ai.usage.input_tokens"), a.get("gen_ai.usage.output_tokens"))
        t0, t1 = int(s.get("startTimeUnixNano") or 0), int(s.get("endTimeUnixNano") or 0)   # in-flight spans: end=None
        tl.append(_row(t0 // 1_000_000, "runtime-span", kind, summ,
                       {"trace_id": s.get("traceId"), "span_id": s.get("spanId"), "session_id": a.get("session.id"),
                        "tenant": a.get("tenant"), "case_id": a.get("case_id"),
                        # the Bedrock client span's aws.request_id == the model-invocation log's requestId
                        "request_id": a.get("aws.request_id") if "bedrock" in str(a.get("server.address", "")) or a.get("gen_ai.request.model") else None},
                       {"duration_ms": (t1 - t0) // 1_000_000 if t1 else None,
                        "reasoning": s.get("_reasoning")}))
    for g in gateway_rows:
        tl.append(_row(g.get("ts", 0), "gateway", "request", g.get("summary", ""), g.get("keys", {}), g.get("detail")))
    for l in lambda_calls:
        tl.append(_row(l.get("ts", 0), "lambda", "call", "%s -> %s" % (l.get("tool"), l.get("outcome")),
                       {k: l.get(k) for k in ("trace_id", "session_id", "mcp_session_id", "execution_arn", "request_id", "tenant", "case_id")},
                       {"arg_keys": l.get("arg_keys"), "args_sha256": l.get("args_sha256"), "duration_ms": l.get("duration_ms")}))
    for m in model_rows:
        meta = m.get("requestMetadata") or {}
        body = json.dumps(m.get("input", {}).get("inputBodyJson", {})) + json.dumps(m.get("output", {}).get("outputBodyJson", {}))
        tl.append(_row(_iso_ms(m.get("timestamp")), "bedrock-model-log", "model-invocation",
                       "%s %s in=%s out=%s masked_before_model=%s" % (m.get("operation"), m.get("modelId"),
                                                                        m.get("input", {}).get("inputTokenCount"), m.get("output", {}).get("outputTokenCount"),
                                                                        masked_check(body)),
                       {"request_id": m.get("requestId"), "session_id": meta.get("session_id"), "tenant": meta.get("tenant"), "case_id": meta.get("case_id")},
                       {"masked_before_model": masked_check(body), "identity": (m.get("identity") or {}).get("arn"),
                        "requestMetadata": meta, "reasoning_excerpt": _assistant_text(m)[:600]}))
    for e in sfn_events:
        tl.append(_row(_iso_ms(e.get("timestamp")), "stepfunctions", "state", "%s %s" % (e.get("type"), e.get("name", "")),
                       {"execution_arn": e.get("execution_arn")}))
    tl.sort(key=lambda r: (r["ts"], r["source"]))
    verdict = summarize(case_id, tenant, tl, worm_rows)
    return {"case_id": case_id, "tenant": tenant, "generated_at": int(time.time()), "timeline": tl, "verdict": verdict}


def summarize(case_id, tenant, tl, worm_rows):
    by = lambda src, kind=None: [r for r in tl if r["source"] == src and (kind is None or r["kind"] == kind)]  # noqa: E731
    model = by("bedrock-model-log")
    sess = {r["keys"].get("session_id") for r in tl if r["keys"].get("session_id")}
    calls = by("lambda")
    ev = by("worm")
    tenants = {r["keys"].get("tenant") for r in tl if r["keys"].get("tenant")}
    linked_calls = [c for c in calls if any(e["keys"].get("trace_id") and e["keys"].get("trace_id") == c["keys"].get("trace_id")
                                             or (e["keys"].get("execution_arn") and e["keys"].get("execution_arn") == c["keys"].get("execution_arn"))
                                             for e in ev)]
    span_req = {r["keys"].get("request_id") for r in by("runtime-span") if r["keys"].get("request_id")}
    model_joined = [m for m in model if m["keys"].get("request_id") in span_req]
    return {
        "worm_records": len(ev),
        "model_invocations_joined_to_spans": len(model_joined),
        "agent_spans": len(by("runtime-span", "agent")), "model_spans": len(by("runtime-span", "model")),
        "tool_spans": len(by("runtime-span", "tool")), "gateway_requests": len(by("gateway")),
        "lambda_calls": len(calls), "lambda_calls_joined_to_evidence": len(linked_calls),
        "model_invocations": len(model),
        "model_invocations_tagged_tenant": sum(1 for m in model if m["keys"].get("tenant") == tenant),
        "masked_before_model_all": all(m["detail"]["masked_before_model"] for m in model) if model else None,
        "sessions": sorted(sess), "tenants_seen": sorted(tenants),
        "single_tenant": tenants <= {tenant} if tenants else None,
    }


def _iso_ms(ts):
    if not ts:
        return 0
    if isinstance(ts, (int, float)):
        return int(ts)
    from datetime import datetime, timezone
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)
    except Exception:
        return 0


def _assistant_text(m):
    out = m.get("output", {}).get("outputBodyJson", {}) or {}
    try:
        return " ".join(c.get("text", "") for c in out["output"]["message"]["content"] if isinstance(c, dict))
    except Exception:
        return ""


def to_markdown(tr):
    v = tr["verdict"]
    lines = ["# Case trace — `%s` (tenant `%s`)" % (tr["case_id"], tr["tenant"]), "",
             "| metric | value |", "|---|---|"]
    lines += ["| %s | %s |" % (k, v[k]) for k in sorted(v)]
    lines += ["", "| time (UTC) | source | kind | what | join keys |", "|---|---|---|---|---|"]
    from datetime import datetime, timezone
    for r in tr["timeline"]:
        t = datetime.fromtimestamp(r["ts"] / 1000, timezone.utc).strftime("%H:%M:%S.%f")[:-3] if r["ts"] else "-"
        keys = " ".join("%s=%s" % (k, str(val)[:18]) for k, val in r["keys"].items() if val and k != "audit_id")
        lines.append("| %s | %s | %s | %s | %s |" % (t, r["source"], r["kind"], r["summary"].replace("|", "/"), keys))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- AWS readers ----------------------
def _insights(logs, groups, query, start, end, limit=1000):
    groups = [g for g in groups if g]
    if not groups:
        return []
    try:
        qid = logs.start_query(logGroupNames=groups, startTime=start, endTime=end, queryString=query, limit=limit)["queryId"]
    except logs.exceptions.ResourceNotFoundException:
        return []
    for _ in range(60):
        time.sleep(1)
        r = logs.get_query_results(queryId=qid)
        if r["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
    return [{f["field"]: f["value"] for f in row} for row in r.get("results", [])]


def _parse(row):
    try:
        return json.loads(row.get("@message", ""))
    except Exception:
        return None


def read_worm_rows(ddb, table, case_id):
    rows, kw = [], {"TableName": table, "FilterExpression": "case_id = :c", "ExpressionAttributeValues": {":c": {"S": case_id}}}
    from boto3.dynamodb.types import TypeDeserializer
    des = TypeDeserializer().deserialize
    while True:
        r = ddb.scan(**kw)
        for it in r.get("Items", []):
            d = {k: des(v) for k, v in it.items()}
            if not str(d.get("audit_id", "")).startswith("HEAD#"):
                d["_table"] = table
                rows.append(_plain(d))
        if not r.get("LastEvaluatedKey"):
            return rows
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def _plain(o):
    from decimal import Decimal
    if isinstance(o, Decimal):
        return int(o) if o == int(o) else float(o)
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_plain(v) for v in o]
    return o


def read_spans(logs, groups, session_ids, trace_ids, start, end):
    out = []
    terms = [t for t in list(session_ids) + list(trace_ids) if t]
    if not terms:
        return out
    cond = " or ".join('@message like "%s"' % t for t in terms)
    q = "fields @timestamp, @message | filter %s | sort @timestamp asc" % cond
    seen = set()
    for row in _insights(logs, groups, q, start, end, 5000):
        s = _parse(row)
        # OTEL LOG records in the runtime group carry traceId/spanId too (the span they were emitted
        # under) - only a record with a name + start time is a span; the log record would otherwise
        # shadow its span in the dedup (found live 2026-09-02: invoke_agent "missing").
        if s and s.get("spanId") and s.get("name") and s.get("startTimeUnixNano") and s["spanId"] not in seen:
            seen.add(s["spanId"])
            out.append(s)
    return out


def read_reasoning_events(logs, groups, session_ids, start, end):
    """The Strands message events (model input/output) for the sessions - the reasoning text."""
    if not session_ids:
        return {}
    cond = " or ".join('@message like "%s"' % s for s in session_ids)
    q = "fields @timestamp, @message | filter %s | sort @timestamp asc" % cond
    by_span = {}
    for row in _insights(logs, groups, q, start, end, 2000):
        e = _parse(row)
        if e and e.get("spanId") and e.get("body"):
            by_span.setdefault(e["spanId"], []).append(e["body"])
    return by_span


def read_gateway_rows(logs, group, session_ids, mcp_ids, trace_ids, start, end):
    terms = [s for s in list(session_ids) + list(mcp_ids) + list(trace_ids) if s]
    if not terms:
        return []
    cond = " or ".join('@message like "%s"' % t for t in terms)
    q = "fields @timestamp, @message | filter %s | sort @timestamp asc" % cond
    out = []
    for row in _insights(logs, [group], q, start, end, 2000):
        m = _parse(row) or {}
        out.append({"ts": _iso_ms(row.get("@timestamp", "").replace(" ", "T") + "Z"),
                    "summary": (m.get("message") or m.get("eventType") or row.get("@message", ""))[:160],
                    "keys": {"session_id": next((s for s in session_ids if s in row.get("@message", "")), None),
                             "mcp_session_id": next((s for s in mcp_ids if s in row.get("@message", "")), None),
                             "trace_id": next((s for s in trace_ids if s in row.get("@message", "")), None)},
                    "detail": {k: m.get(k) for k in ("requestId", "method", "toolName", "statusCode", "decision") if m.get(k)}})
    return out


def read_lambda_calls(logs, groups, case_id, keys, start, end):
    cond = ['@message like "aegis" and @message like "args_sha256"', '(@message like "%s"' % case_id]
    for t in keys.get("trace_id", []) + keys.get("execution_arn", []) + keys.get("session_id", []):
        cond[1] += ' or @message like "%s"' % t
    cond[1] += ")"
    q = "fields @timestamp, @message | filter %s | sort @timestamp asc" % " and ".join(cond)
    out = []
    for row in _insights(logs, groups, q, start, end, 2000):
        m = _parse(row)
        if m and m.get("aegis") == "call":
            out.append(m)
    return out


def read_model_rows(logs, group, case_id, session_ids, start, end):
    cond = '@message like "%s"' % case_id
    for s in session_ids:
        cond += ' or @message like "%s"' % s
    q = "fields @timestamp, @message | filter %s | sort @timestamp asc" % cond
    out = []
    for row in _insights(logs, [group], q, start, end, 500):
        m = _parse(row)
        if m and m.get("schemaType") == "ModelInvocationLog":
            out.append(m)
    return out


def read_sfn(sfn, arns):
    out = []
    for arn in arns:
        try:
            for e in sfn.get_execution_history(executionArn=arn, maxResults=500)["events"]:
                d = e.get("stateEnteredEventDetails") or e.get("stateExitedEventDetails") or {}
                if e["type"].endswith("StateEntered") or e["type"] in ("ExecutionStarted", "ExecutionSucceeded", "ExecutionFailed", "ExecutionAborted"):
                    out.append({"timestamp": e["timestamp"].isoformat(), "type": e["type"], "name": d.get("name", ""), "execution_arn": arn})
        except Exception as exc:
            out.append({"timestamp": None, "type": "history-unavailable:" + type(exc).__name__, "execution_arn": arn})
    return out


def main():
    import boto3
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--tenant", default="")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--runtime-log-group", default="", help="/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint>")
    ap.add_argument("--session-id", action="append", default=[], help="seed: runtime session id(s) (repeatable)")
    ap.add_argument("--since-minutes", type=int, default=180)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    prefix = f"pv-{a.env}"
    logs = boto3.client("logs", region_name=a.region)
    ddb = boto3.client("dynamodb", region_name=a.region)
    sfn = boto3.client("stepfunctions", region_name=a.region)
    end = int(time.time()); start = end - a.since_minutes * 60
    ledger = f"{prefix}-{a.tenant}-audit-ledger" if a.tenant else f"{prefix}-audit-ledger"
    worm = read_worm_rows(ddb, ledger, a.case_id)
    keys = join_keys(worm)
    for sid in a.session_id:
        if sid not in keys["session_id"]:
            keys["session_id"].append(sid)
    span_groups = [g for g in (a.runtime_log_group, "aws/spans") if g]
    spans = read_spans(logs, span_groups, keys["session_id"], keys["trace_id"], start, end)
    # sessions discovered from spans (the runtime's own) widen the search for the model log + gateway
    for s in spans:
        sid = (s.get("attributes") or {}).get("session.id")
        if sid and sid not in keys["session_id"]:
            keys["session_id"].append(sid)
    reasoning = read_reasoning_events(logs, [a.runtime_log_group] if a.runtime_log_group else [], keys["session_id"], start, end)
    for s in spans:
        if s.get("spanId") in reasoning:
            s["_reasoning"] = reasoning[s["spanId"]][:3]
    gw = read_gateway_rows(logs, f"/aws/vendedlogs/bedrock-agentcore/gateway/{prefix}", keys["session_id"], keys["mcp_session_id"], keys["trace_id"], start, end)
    lam_groups = ["/aws/lambda/%s-%s" % (prefix, n) for n in ("ingest-case", "intake-icsr", "openfda-lookup", "mask-pii", "assess-seriousness",
                                                             "detect-duplicate", "record-causality", "core-tools", "write-audit", "request-signoff",
                                                             "signoff-register", "finalize", "workflow-guards", "approve-signoff")]
    existing = {g["logGroupName"] for g in logs.describe_log_groups(logGroupNamePrefix=f"/aws/lambda/{prefix}-").get("logGroups", [])}
    calls = read_lambda_calls(logs, [g for g in lam_groups if g in existing], a.case_id, keys, start, end)
    model = read_model_rows(logs, f"/aws/bedrock/modelinvocations/{prefix}", a.case_id, keys["session_id"], start, end)
    sfn_ev = read_sfn(sfn, keys["execution_arn"])
    tr = build_timeline(a.case_id, a.tenant, worm, spans, gw, calls, model, sfn_ev)
    tr["join_keys"] = keys
    tr["sources"] = {"ledger": ledger, "span_groups": span_groups, "gateway_log_group": f"/aws/vendedlogs/bedrock-agentcore/gateway/{prefix}",
                     "model_log_group": f"/aws/bedrock/modelinvocations/{prefix}", "lambda_groups": sorted(existing)}
    js = json.dumps(tr, indent=1, default=str)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(js)
        open(re.sub(r"\.json$", "", a.out) + ".md", "w", encoding="utf-8").write(to_markdown(tr))
    print(js)


if __name__ == "__main__":
    main()
