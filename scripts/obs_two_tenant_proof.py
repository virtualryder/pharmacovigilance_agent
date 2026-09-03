#!/usr/bin/env python3
"""Phase 110 - LIVE full-transparency proof, two tenants, through the real AgentCore Runtime.

For each tenant: a caseworker (tenant_<id> group) ingests a synthetic case (token-verified ingest ->
case_ref + signed tenant pair), then drives the AgentCore Runtime agent (Strands on Bedrock) with an
EXPLICIT runtime session id; the agent calls the governed gateway tools. Then `trace_case` builds the
timeline from the per-tenant WORM ledger + runtime spans/reasoning events + gateway logs + Lambda
aegis.call lines + Bedrock model-invocation logs, and the verdict checks: every hop joined by
session/trace keys, every model invocation tagged with the tenant, masked_before_model for all of them,
and the OTHER tenant's ledger empty for the case.

Usage: python scripts/obs_two_tenant_proof.py --env mt3 --tenants sp-a,sp-b --runtime-arn <arn>
       --runtime-log-group /aws/bedrock-agentcore/runtimes/<agent_id>-DEFAULT [--out evidence/x.json]"""
import argparse
import importlib.util
import json
import pathlib
import secrets
import sys
import time
import uuid

import boto3

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from mt_two_tenant_proof import SYNTHETIC_CASE, access_token, make_user, outputs  # noqa: E402
import rt_invoke  # noqa: E402

spec = importlib.util.spec_from_file_location("trace_case", HERE / "trace_case.py")
tc = importlib.util.module_from_spec(spec); sys.modules["trace_case"] = tc; spec.loader.exec_module(tc)

PROMPT = ("Process the intake for ICSR case {case_id} (requester {req}). The raw adverse-event source is already "
          "ingested as case_ref {ref}; NEVER ask for or restate raw patient details. Steps: 1) intake_icsr with "
          "case_ref; 2) openfda_lookup for the suspect drug from the extracted fields; 3) mask_pii with case_ref; "
          "4) assess_seriousness with the extracted fields/flags, deidentified true and the sanitized_ref; "
          "5) draft_narrative with deidentified true and the sanitized_ref; 6) write_audit an INTENT record "
          "(icsr_id {case_id}, action icsr-determination, actor {req}); 7) request_signoff for icsr_id {case_id}. "
          "If a tool is denied, stop and report the control. End with a short summary.")


def trace(args_ns, case_id, tenant, since, session_id=None):
    """Run trace_case's readers in-process (same code path as the CLI)."""
    prefix = f"pv-{args_ns.env}"
    logs = boto3.client("logs", region_name=args_ns.region)
    ddb = boto3.client("dynamodb", region_name=args_ns.region)
    sfn = boto3.client("stepfunctions", region_name=args_ns.region)
    end = int(time.time()) + 60
    ledger = f"{prefix}-{tenant}-audit-ledger"
    worm = tc.read_worm_rows(ddb, ledger, case_id)
    keys = tc.join_keys(worm)
    if session_id and session_id not in keys["session_id"]:
        keys["session_id"].append(session_id)          # the runtime session the harness chose
    spans = tc.read_spans(logs, [args_ns.runtime_log_group, "aws/spans"], keys["session_id"], keys["trace_id"], since, end)
    for s in spans:
        sid = (s.get("attributes") or {}).get("session.id")
        if sid and sid not in keys["session_id"]:
            keys["session_id"].append(sid)
    reasoning = tc.read_reasoning_events(logs, [args_ns.runtime_log_group], keys["session_id"], since, end)
    for s in spans:
        if s.get("spanId") in reasoning:
            s["_reasoning"] = reasoning[s["spanId"]][:3]
    gw = tc.read_gateway_rows(logs, f"/aws/vendedlogs/bedrock-agentcore/gateway/{prefix}", keys["session_id"], keys["mcp_session_id"], keys["trace_id"], since, end)
    existing = {g["logGroupName"] for g in logs.describe_log_groups(logGroupNamePrefix=f"/aws/lambda/{prefix}-").get("logGroups", [])}
    calls = tc.read_lambda_calls(logs, sorted(existing), case_id, keys, since, end)
    model = tc.read_model_rows(logs, f"/aws/bedrock/modelinvocations/{prefix}", case_id, keys["session_id"], since, end)
    sfn_ev = tc.read_sfn(sfn, keys["execution_arn"])
    tr = tc.build_timeline(case_id, tenant, worm, spans, gw, calls, model, sfn_ev)
    tr["join_keys"] = keys
    return tr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="mt3")
    ap.add_argument("--tenants", default="sp-a,sp-b")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--runtime-arn", required=True)
    ap.add_argument("--runtime-log-group", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--settle-seconds", type=int, default=150, help="wait for spans/logs to land")
    a = ap.parse_args()
    prefix = f"pv-{a.env}"
    ta, tb = [t.strip() for t in a.tenants.split(",")][:2]
    cf = boto3.client("cloudformation", region_name=a.region)
    idp = boto3.client("cognito-idp", region_name=a.region)
    lam = boto3.client("lambda", region_name=a.region)
    ident = outputs(cf, f"{prefix}-identity")
    obs = outputs(cf, f"{prefix}-observability")
    pool, client = ident["UserPoolId"], ident["ClientId"]
    since = int(time.time()) - 60
    ev = {"env": a.env, "prefix": prefix, "tenants": [ta, tb], "runtime_arn": a.runtime_arn,
          "runtime_log_group": a.runtime_log_group, "observability_outputs": obs, "steps": []}

    pw = "Obs-" + secrets.token_urlsafe(12) + "aA1!"
    users = {"cw-a": ["pv_reviewer", f"tenant_{ta}"], "cw-b": ["pv_reviewer", f"tenant_{tb}"]}
    for name, groups in users.items():
        make_user(idp, pool, name, groups, pw)
    time.sleep(3)
    tok = {n: access_token(pool, client, a.region, n, pw) for n in users}

    runs = {}
    for name, tenant in (("cw-a", ta), ("cw-b", tb)):
        case_id = "OBS-%s-%s" % (tenant.upper().replace("-", ""), uuid.uuid4().hex[:5].upper())
        ing = json.loads(lam.invoke(FunctionName=f"{prefix}-ingest-case",
                                    Payload=json.dumps({"source": SYNTHETIC_CASE, "case_id": case_id,
                                                        "access_token": tok[name]}).encode())["Payload"].read())
        session_id = "aegis-%s-%s" % (tenant, uuid.uuid4().hex)
        t0 = time.time()
        inv = rt_invoke.invoke(a.runtime_arn, tok[name], case_id, name, session_id, a.region,
                               PROMPT.format(case_id=case_id, req=name, ref=ing.get("case_ref", "")))
        runs[tenant] = {"identity": name, "case_id": case_id, "ingest": {k: v for k, v in ing.items() if k != "tenant_binding"},
                        "session_id": session_id, "runtime_status": inv["status"], "runtime_seconds": round(time.time() - t0, 1),
                        "runtime_response": (json.dumps(inv["response"])[:1500] if inv["status"] == 200 else inv["response"]),
                        "runtime_request_id": inv.get("request_id")}
        ev["steps"].append({"step": "runtime_invocation", **runs[tenant]})

    print("settling %ss for spans/logs..." % a.settle_seconds, file=sys.stderr)
    time.sleep(a.settle_seconds)

    traces, cross = {}, {}
    for tenant, other in ((ta, tb), (tb, ta)):
        traces[tenant] = trace(a, runs[tenant]["case_id"], tenant, since, runs[tenant]["session_id"])
        # the OTHER tenant's ledger must hold nothing for this case
        ddb = boto3.client("dynamodb", region_name=a.region)
        cross[tenant] = len(tc.read_worm_rows(ddb, f"{prefix}-{other}-audit-ledger", runs[tenant]["case_id"]))
    ev["traces"] = traces
    ev["cross_tenant_worm_rows"] = cross

    def ok(t):
        v = traces[t]["verdict"]
        return {
            "runtime_invoked_200": runs[t]["runtime_status"] == 200,
            "worm_records": v["worm_records"] >= 1,
            "agent_span_with_session": v["agent_spans"] >= 1 and runs[t]["session_id"] in v["sessions"],
            "model_reasoning_spans": v["model_spans"] >= 1,
            "tool_spans": v["tool_spans"] >= 1,
            "lambda_calls_logged": v["lambda_calls"] >= 1,
            "lambda_calls_joined_to_evidence": v["lambda_calls_joined_to_evidence"] >= 1,
            "model_invocations_logged": v["model_invocations"] >= 1,
            "model_invocations_tagged_tenant": v["model_invocations"] >= 1 and v["model_invocations_tagged_tenant"] == v["model_invocations"],
            "model_invocations_joined_to_spans": v["model_invocations_joined_to_spans"] >= 1,
            "masked_before_model_all": v["masked_before_model_all"] is True,
            "single_tenant_timeline": v["single_tenant"] is True,
            "other_tenant_ledger_empty": cross[t] == 0,
        }
    verdict = {ta: ok(ta), tb: ok(tb)}
    verdict["PASS"] = all(all(v.values()) for v in (verdict[ta], verdict[tb]))
    ev["verdict"] = verdict
    js = json.dumps(ev, indent=1, default=str)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(js)
        for t in (ta, tb):
            open(a.out.replace(".json", "-%s.md" % t), "w", encoding="utf-8").write(tc.to_markdown(traces[t]))
    print(js)
    sys.exit(0 if verdict["PASS"] else 1)


if __name__ == "__main__":
    main()
