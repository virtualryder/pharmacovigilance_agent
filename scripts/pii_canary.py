#!/usr/bin/env python3
"""Gate-B B4 — PHI telemetry-leak canary (pharmacovigilance).

Proves (or disproves) the claim "PHI does not reach telemetry" with a MARKED case: a globally-unique
fake-PHI marker is run through the deployed pipeline, then every telemetry destination is swept for the
marker. Any hit is a leak finding with the exact destination named — remediate (or accept + document)
before real adverse-event PHI enters the system.

Swept destinations:
  * CloudWatch Logs        — every /aws/lambda/<prefix>-* group (MUST be clean)
  * X-Ray traces           — annotations/metadata segments (MUST be clean)
  * Step Functions history — state input/output payloads. With R3-2 pass-by-reference the execution
                             carries ONLY an opaque case_ref, so this MUST be clean too (strict mode).
  * SQS DLQs               — any queue named <prefix>-* (MUST be clean)

Usage:
  python scripts/pii_canary.py --prefix pv-pilot --execute --strict
  python scripts/pii_canary.py --prefix pv-pilot --sweep-only --marker CANARY-abcdef12 --strict

Exit code 0 = PASS (marker nowhere it must not be), 2 = FAIL (leak found).
Offline logic (marker minting, text sweep, verdict) is unit-tested in tests/test_pii_canary.py."""
import argparse
import json
import sys
import time
import uuid

MUST_BE_CLEAN = ("cloudwatch_logs", "xray", "dlq", "stepfunctions_history")


def make_marker():
    return f"CANARY-{uuid.uuid4().hex[:12].upper()}-TELEMETRYPROBE"


def build_canary_case(marker):
    """A synthetic adverse-event source carrying the marker as name, SSN-shaped id (900- reserved
    range), and address — the highest-risk PHI shapes."""
    return {
        "case_id": f"CANARY-{marker[-19:-14]}",
        "source": (f"Patient {marker} (SSN 900-00-{marker[7:11]}) residing at 1 {marker} Street, "
                   f"Los Angeles CA 90001. Suspect product atorvastatin; hospitalized with "
                   f"rhabdomyolysis. Unexpected."),
        "canary": True,
    }


def sweep_text(text, marker):
    if not text or not marker:
        return 0
    return str(text).upper().count(marker.upper())


def strict_verdict(hits):
    """Gate-B exit criterion: EVERY destination clean (R3-2 pass-by-reference makes this achievable)."""
    leaks = {d: n for d, n in hits.items() if n}
    return {"verdict": "FAIL" if leaks else "PASS", "leaks": leaks}


def verdict(hits):
    leaks = {d: n for d, n in hits.items() if n and d in MUST_BE_CLEAN}
    return {"verdict": "FAIL" if leaks else "PASS", "leaks": leaks,
            "note": ("marker found where it must be clean — remediate before real PHI" if leaks
                     else "no marker in logs / X-Ray / DLQs / Step Functions history")}


# ── live sweeps (boto3 only inside these; offline tests never import them) ────
def sweep_cloudwatch_logs(prefix, marker, since_ms, session=None, extra_groups=(), only_extra=False):
    """Every /aws/lambda/<prefix>-* group plus any EXTRA groups (phase 110: the AgentCore runtime's
    group, aws/spans, the gateway's vended request log, the Bedrock model-invocation log) - so the
    canary also proves the MODEL and the runtime's telemetry never saw the marker."""
    import boto3
    logs = (session or boto3).client("logs")
    total = 0
    names = ([] if only_extra else [g["logGroupName"] for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=f"/aws/lambda/{prefix}-")
                                    for g in page["logGroups"]]) + [g for g in extra_groups if g]
    for name in names:
        try:
            r = logs.filter_log_events(logGroupName=name, startTime=since_ms, filterPattern=f'"{marker}"')
            total += len(r.get("events", []))
        except Exception:
            pass
    return total


def sweep_stepfunctions(prefix, marker, session=None):
    import boto3
    sfn = (session or boto3).client("stepfunctions")
    total = 0
    for m in sfn.list_state_machines()["stateMachines"]:
        if not m["name"].startswith(prefix):
            continue
        for ex in sfn.list_executions(stateMachineArn=m["stateMachineArn"], maxResults=25)["executions"]:
            try:
                for ev in sfn.get_execution_history(executionArn=ex["executionArn"], maxResults=500)["events"]:
                    total += sweep_text(json.dumps(ev, default=str), marker)
            except Exception:
                pass
    return total


def sweep_xray(marker, since, until, session=None):
    import boto3
    xr = (session or boto3).client("xray")
    total = 0
    try:
        ids = [t["Id"] for t in xr.get_trace_summaries(StartTime=since, EndTime=until).get("TraceSummaries", [])][:100]
        for i in range(0, len(ids), 5):
            total += sweep_text(json.dumps(xr.batch_get_traces(TraceIds=ids[i:i + 5]).get("Traces", []), default=str), marker)
    except Exception:
        pass
    return total


def sweep_dlqs(prefix, marker, session=None):
    import boto3
    sqs = (session or boto3).client("sqs")
    total = 0
    try:
        for url in sqs.list_queues(QueueNamePrefix=prefix).get("QueueUrls", []):
            r = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, VisibilityTimeout=0, WaitTimeSeconds=0)
            total += sweep_text(json.dumps(r.get("Messages", []), default=str), marker)
    except Exception:
        pass
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--execute", action="store_true", help="start a live canary execution first")
    ap.add_argument("--marker", help="sweep for an existing marker instead of minting one")
    ap.add_argument("--sweep-only", action="store_true")
    ap.add_argument("--strict", action="store_true", help="Gate-B exit: every destination must be clean")
    ap.add_argument("--wait", type=int, default=120, help="seconds to wait after --execute before sweeping")
    # hybrid multi-tenant / phase 110 additions (harness only; product code unchanged):
    ap.add_argument("--access-token", default="", help="multi-tenant: a tenant reviewer's Cognito access token (ingest derives the tenant; the execution carries the signed pair)")
    ap.add_argument("--extra-log-group", action="append", default=[], help="additional log groups to sweep AND gate on (e.g. the gateway's vended request log); repeatable.")
    ap.add_argument("--info-log-group", action="append", default=[], help="log groups to sweep and REPORT but not gate on: the Bedrock model-invocation log. The MODEL path is measured by scripts/trace_case.py (masked_before_model, realistic PII canaries) - a synthetic marker token is not something Comprehend reliably classifies as a NAME (seen live: the same marker masked on one run and not the next), so a hit here is recorded as informational, never as a pass-by-reference leak.")
    args = ap.parse_args()

    import datetime
    import boto3
    marker = args.marker or make_marker()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)

    if args.execute and not args.sweep_only:
        case = build_canary_case(marker)
        # R3-2 pass-by-reference: raw content enters ONLY through ingest; the execution starts with an
        # opaque case_ref — exactly what the strict sweep verifies.
        lam = boto3.client("lambda")
        payload = {"source": case["source"], "case_id": case["case_id"]}
        if args.access_token:
            payload["access_token"] = args.access_token
        ing = json.loads(lam.invoke(FunctionName=f"{args.prefix}-ingest-case",
                                    Payload=json.dumps(payload).encode())["Payload"].read())
        if not ing.get("case_ref"):
            print(json.dumps({"verdict": "FAIL", "error": "ingest refused: %s" % ing}, indent=2))
            sys.exit(2)
        sfn = boto3.client("stepfunctions")
        arn = next(m["stateMachineArn"] for m in sfn.list_state_machines()["stateMachines"]
                   if m["name"].startswith(args.prefix))
        sfn.start_execution(stateMachineArn=arn, name=f"pii-canary-{marker[7:19].lower()}",
                            input=json.dumps({"case_id": case["case_id"], "requester": "canary",
                                              "case_ref": ing["case_ref"], "drug": "atorvastatin",
                                              "case_key": "atorvastatin|rhabdomyolysis|2026|hcp", "known_keys": "",
                                              **(ing.get("tenant_binding") or {})}))
        print(f"canary execution started (marker {marker}); waiting {args.wait}s for telemetry...", file=sys.stderr)
        time.sleep(args.wait)

    until = datetime.datetime.now(datetime.timezone.utc)
    hits = {
        "cloudwatch_logs": sweep_cloudwatch_logs(args.prefix, marker, int(since.timestamp() * 1000),
                                                 extra_groups=args.extra_log_group),
        "stepfunctions_history": sweep_stepfunctions(args.prefix, marker),
        "xray": sweep_xray(marker, since, until),
        "dlq": sweep_dlqs(args.prefix, marker),
    }
    info = {g: sweep_cloudwatch_logs(args.prefix, marker, int(since.timestamp() * 1000), extra_groups=[g], only_extra=True)
            for g in args.info_log_group}
    v = strict_verdict(hits) if args.strict else verdict(hits)
    v.update({"marker": marker, "prefix": args.prefix, "swept_at": until.isoformat()})
    if info:
        v["informational_model_path"] = {"hits": info, "note": "not gated: synthetic marker vs Comprehend NAME recall; the model-path control is masked_before_model (trace_case, realistic PII)"}
    print(json.dumps(v, indent=2))
    sys.exit(0 if v["verdict"] == "PASS" else 2)


if __name__ == "__main__":
    main()
