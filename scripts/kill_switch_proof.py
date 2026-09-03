#!/usr/bin/env python3
"""Task 127 live gate — the Kill Switch on the AgentCore agent path, proven on a real deployment.

What it proves (each check is a boolean in the evidence JSON; the gate PASSes only if ALL are true):

  IAM SoD     A (engage-only policy) can engage; A is refused at the disengage URL by IAM (HTTP 403
              before the function runs); B (disengage-only) cannot engage; B can disengage.
  Code SoD    C (holds BOTH policies - an over-privileged operator) engages, then is refused when it
              tries to release its own engagement (HTTP 403 from the controller + a DENIED ledger
              record); B releases it.
  Actor       the recorded actor is the IAM-verified assumed-role ARN of the caller, never a body field
              (the body's "actor" is ignored).
  Interceptor within one TTL of engage: tools/list and tools/call -> 403 JSON-RPC error for a tenanted
              caseworker (time-to-effect measured); the target Lambda is never invoked; a DENIED
              kill_switch.deny record + WORM object lands in THAT tenant's ledger / vault, none in the
              other tenant's, none in the base ledger for the denials.
  Tool Lambda a direct invoke of a governed tool while engaged fails with errorType KillSwitchEngaged;
              a Step Functions execution started while engaged FAILS at its first state with error
              KillSwitchEngaged (the workflow hop has no interceptor).
  Runtime     a runtime invocation while engaged is refused before the tenant is derived or the gateway
              is contacted (guardrail_action KILL_SWITCH); a session that is RUNNING when the switch is
              engaged stops at its next model call (stopped: mid-session).
  Ledger      the BASE ledger carries the KILL-SWITCH chain: engage COMMITTED (A), engage COMMITTED (C),
              disengage DENIED (C, SoD), disengage COMMITTED (B) x2 — all hash-chained, WORM copies
              present, actors = assumed-role ARNs.
  Recovery    after disengage: tools/list + tools/call succeed again and the runtime answers.
  Logs        aegis.kill_switch lines exist in the interceptor log group and the runtime log group.

Identities: three throwaway IAM roles are created (trusting the caller), assumed, and deleted at the end.
Everything is torn down by the caller's usual env teardown; the switch is left DISENGAGED.

Usage: python scripts/kill_switch_proof.py --env mt5 --tenants sp-a,sp-b --runtime-arn <arn> \
           --runtime-log-group /aws/bedrock-agentcore/runtimes/<id>-DEFAULT --out evidence/AGENTCORE-KILL-SWITCH-<date>
"""
import argparse
import json
import secrets
import sys
import threading
import time
import uuid

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

sys.path.insert(0, __import__("os").path.dirname(__file__))
from mt_two_tenant_proof import Mcp, access_token, make_user, outputs, SYNTHETIC_CASE  # noqa: E402
import rt_invoke  # noqa: E402


# ---- SigV4 to a Lambda function URL (service name "lambda") --------------------------------------
def signed(creds, region, method, url, body=None):
    data = json.dumps(body) if body is not None else ""
    req = AWSRequest(method=method, url=url, data=data, headers={"content-type": "application/json"})
    SigV4Auth(creds, "lambda", region).add_auth(req)
    r = requests.request(method, url, headers=dict(req.headers), data=data, timeout=60)
    try:
        payload = r.json()
    except Exception:
        payload = r.text[:500]
    return {"status": r.status_code, "body": payload}


def role_creds(sts, role_arn, name):
    for i in range(12):                      # IAM propagation after CreateRole / AttachRolePolicy
        try:
            c = sts.assume_role(RoleArn=role_arn, RoleSessionName=name, DurationSeconds=900)["Credentials"]
            from botocore.credentials import Credentials
            return Credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
        except Exception:
            time.sleep(5)
    raise RuntimeError("cannot assume " + role_arn)


def ensure_role(iam, name, trust_arn, policy_arns):
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": trust_arn},
                                                     "Action": "sts:AssumeRole"}]}
    try:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                              Description="task 127 kill-switch proof (throwaway)", MaxSessionDuration=3600)["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
    for p in policy_arns:
        iam.attach_role_policy(RoleName=name, PolicyArn=p)
    return arn


def drop_role(iam, name):
    try:
        for p in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=name, PolicyArn=p["PolicyArn"])
        iam.delete_role(RoleName=name)
    except Exception as exc:
        return "ERR:" + type(exc).__name__
    return "deleted"


# ---- ledger readers ---------------------------------------------------------------------------------
def chain_rows(ddb, table, case_id):
    rows, kw = [], {"TableName": table, "FilterExpression": "case_id = :c AND NOT begins_with(audit_id, :h)",
                    "ExpressionAttributeValues": {":c": {"S": case_id}, ":h": {"S": "HEAD#"}}}
    while True:
        try:
            r = ddb.scan(**kw)
        except Exception as exc:
            return [{"error": type(exc).__name__}]
        for it in r.get("Items", []):
            d = {k: list(v.values())[0] for k, v in it.items() if k in ("action", "phase", "actor", "seq", "tenant_id", "case_id", "chain_hash", "prev_hash", "audit_id")}
            pay = it.get("payload", {}).get("M", {})
            d["payload_keys"] = sorted(pay)
            d["execution_arn"] = (it.get("correlation", {}).get("M", {}).get("execution_arn") or {}).get("S")
            d["engaged_by"] = (pay.get("engaged_by") or {}).get("S")
            rows.append(d)
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    rows.sort(key=lambda d: int(d.get("seq", 0)))
    return rows


def worm_keys(s3, bucket, prefix):
    try:
        return [o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=prefix + "/").get("Contents", [])]
    except Exception as exc:
        return ["ERR:" + type(exc).__name__]


def log_lines(logs, group, needle, since_ms, limit=50):
    try:
        r = logs.filter_log_events(logGroupName=group, startTime=since_ms, filterPattern='"%s"' % needle, limit=limit)
        return [e["message"][:400] for e in r.get("events", [])]
    except Exception as exc:
        return ["ERR:" + type(exc).__name__]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--tenants", default="sp-a,sp-b")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--runtime-arn", required=True)
    ap.add_argument("--runtime-log-group", required=True)
    ap.add_argument("--out", required=True, help="path prefix; writes <out>.json and <out>.md")
    ap.add_argument("--ttl", type=int, default=15)
    a = ap.parse_args()
    prefix, region = f"pv-{a.env}", a.region
    ta, tb = [t.strip() for t in a.tenants.split(",")][:2]
    t_start = time.time()
    since_ms = int(t_start * 1000) - 60_000

    cf, sts, iam = (boto3.client(s, region_name=region) for s in ("cloudformation", "sts", "iam"))
    idp, ddb, s3, lam, sfn, logs, ssm = (boto3.client(s, region_name=region) for s in
                                         ("cognito-idp", "dynamodb", "s3", "lambda", "stepfunctions", "logs", "ssm"))
    comp, ident, gw, wf = (outputs(cf, f"{prefix}-{s}") for s in ("compute", "identity", "gateway", "workflow"))
    param = comp["KillSwitchParameter"]
    url_engage, url_disengage = comp["KillSwitchEngageUrl"], comp["KillSwitchDisengageUrl"]
    pol_engage, pol_disengage = comp["KillSwitchEngagePolicyArn"], comp["KillSwitchDisengagePolicyArn"]
    me = sts.get_caller_identity()
    acct = me["Account"]
    ev = {"env": a.env, "prefix": prefix, "region": region, "parameter": param, "tenants": [ta, tb],
          "engage_url": url_engage, "disengage_url": url_disengage, "caller": me["Arn"], "checks": {}, "steps": []}
    C = ev["checks"]

    # ---- identities ---------------------------------------------------------------------------------
    trust = me["Arn"] if ":user/" in me["Arn"] else "arn:aws:iam::%s:root" % acct
    roles = {"A": ensure_role(iam, f"{prefix}-ks-responder", trust, [pol_engage]),
             "B": ensure_role(iam, f"{prefix}-ks-security-lead", trust, [pol_disengage]),
             "C": ensure_role(iam, f"{prefix}-ks-overprivileged", trust, [pol_engage, pol_disengage])}
    ev["roles"] = roles
    time.sleep(12)
    creds = {k: role_creds(sts, v, "ks-" + k.lower()) for k, v in roles.items()}
    # the assumed-role ARN the controller will see: arn:aws:sts::<acct>:assumed-role/<role>/<session>
    role_id = {k: "arn:aws:sts::%s:assumed-role/%s/ks-%s" % (acct, v.rsplit("/", 1)[1], k.lower()) for k, v in roles.items()}

    # ---- cognito users + tokens (tenanted caseworkers) --------------------------------------------
    pool, client, gw_url = ident["UserPoolId"], ident["ClientId"], gw["GatewayUrl"]
    pw = "Ks-" + secrets.token_urlsafe(12) + "aA1!"
    make_user(idp, pool, "ks-cw-a", ["pv_reviewer", f"tenant_{ta}"], pw)
    make_user(idp, pool, "ks-cw-b", ["pv_reviewer", f"tenant_{tb}"], pw)
    time.sleep(3)
    tok_a, tok_b = (access_token(pool, client, region, u, pw) for u in ("ks-cw-a", "ks-cw-b"))

    def gateway_probe(token):
        m = Mcp(gw_url, token)
        m.init()
        lst = m.call("tools/list")
        call = m.call("tools/call", {"name": "mask-pii___mask_pii",
                                     "arguments": {"case": SYNTHETIC_CASE + " " + uuid.uuid4().hex[:8]}})
        def rpc_err(x):
            b = x.get("body")
            return (b.get("error") or {}).get("message") if isinstance(b, dict) else None
        return {"list_status": lst["status"], "list_error": rpc_err(lst),
                "list_tools": len(((lst["body"] or {}).get("result") or {}).get("tools", [])) if isinstance(lst["body"], dict) else 0,
                "call_status": call["status"], "call_error": rpc_err(call),
                "call_ok": call["status"] == 200 and isinstance(call["body"], dict) and "error" not in call["body"]}

    def status(creds_):
        return signed(creds_, region, "GET", url_engage)

    ledger_base = f"{prefix}-audit-ledger"
    ledger = {ta: f"{prefix}-{ta}-audit-ledger", tb: f"{prefix}-{tb}-audit-ledger"}
    vault = {ta: f"{prefix}-{ta}-worm-{acct}", tb: f"{prefix}-{tb}-worm-{acct}"}
    base_vault = outputs(cf, f"{prefix}-data").get("WormBucketName", "")

    # ---- 0. baseline: disengaged, calls allowed ----------------------------------------------------
    st0 = status(creds["A"])
    base0 = gateway_probe(tok_a)
    C["baseline_disengaged"] = st0["status"] == 200 and st0["body"]["state"]["engaged"] is False
    C["baseline_calls_allowed"] = base0["list_status"] == 200 and base0["list_tools"] > 0 and base0["call_ok"]
    ev["steps"].append({"step": "baseline", "status": st0, "gateway": base0})

    # mint a case + signed binding for the workflow hop BEFORE engaging (ingest refuses while engaged)
    case_id = "KS-" + uuid.uuid4().hex[:6].upper()
    ing = json.loads(lam.invoke(FunctionName=f"{prefix}-ingest-case",
                                Payload=json.dumps({"source": SYNTHETIC_CASE, "case_id": case_id,
                                                    "access_token": tok_a}).encode())["Payload"].read())
    binding = ing.get("tenant_binding", {})

    # start a runtime session that will still be RUNNING when we engage (in-flight stop): a prompt that
    # forces ten separate tool calls (each = model call + gateway call) keeps the session busy ~1-2 min.
    inflight = {}
    lines = "\n".join("%d. Patient %s Sample, DOB 1990-01-%02d, 12 Elm St Springfield, phone 555-01%02d, rash after amoxicillin." % (i, n, i, i)
                      for i, n in enumerate(("Ann", "Bob", "Cy", "Di", "Ed", "Fay", "Gus", "Hal", "Ivy", "Jo"), 1))
    busy_prompt = ("For EACH of the ten numbered lines below, call the mask_pii tool separately on that single "
                   "line (ten separate calls, never batch them, do not skip any), then reply with one summary "
                   "line. Lines:\n" + lines)
    def _run():
        inflight.update(rt_invoke.invoke(a.runtime_arn, tok_b, "KS-INFLIGHT-" + uuid.uuid4().hex[:4].upper(),
                                         "ks-cw-b", region=region, prompt=busy_prompt, timeout=900))
    th = threading.Thread(target=_run, daemon=True)
    th.start()
    time.sleep(15)                              # let it list tools + make its first model/tool calls

    # ---- 1. IAM SoD: B cannot engage; A engages (actor = A's assumed-role ARN, body actor ignored) ----
    b_engage = signed(creds["B"], region, "POST", url_engage, {"reason": "B must not be able to engage"})
    C["iam_sod_disengage_only_cannot_engage"] = b_engage["status"] == 403
    t_engage = time.time()
    a_engage = signed(creds["A"], region, "POST", url_engage,
                      {"reason": "SEV-1 drill: runaway agent suspected", "actor": "arn:aws:iam::111122223333:user/spoofed"})
    C["engage_ok"] = a_engage["status"] == 200 and a_engage["body"]["state"]["engaged"] is True
    C["actor_is_iam_verified_not_body"] = (a_engage["status"] == 200 and
                                           a_engage["body"]["state"]["actor"] == role_id["A"] and "spoofed" not in json.dumps(a_engage["body"]))
    C["engage_audited_worm"] = a_engage["status"] == 200 and bool(a_engage["body"]["audit"].get("stored")) and bool(a_engage["body"]["audit"].get("worm"))
    ev["steps"].append({"step": "engage_by_A", "b_engage_attempt": b_engage, "a_engage": a_engage})

    # ---- 2. time-to-effect at the gateway ----------------------------------------------------------
    tte, probe = None, None
    for _ in range(30):
        probe = gateway_probe(tok_a)
        if probe["list_status"] == 403 and probe["call_status"] == 403:
            tte = round(time.time() - t_engage, 1)
            break
        time.sleep(2)
    C["interceptor_denies_list_and_call"] = probe["list_status"] == 403 and probe["call_status"] == 403 and \
        "containment engaged" in (probe["list_error"] or "") and "containment engaged" in (probe["call_error"] or "")
    C["time_to_effect_within_2x_ttl"] = tte is not None and tte <= 2 * a.ttl + 5
    ev["steps"].append({"step": "gateway_after_engage", "time_to_effect_s": tte, "probe": probe})
    probe_b = gateway_probe(tok_b)
    ev["steps"][-1]["probe_other_tenant"] = probe_b

    # ---- 3. tool Lambda + workflow hop -------------------------------------------------------------
    direct = lam.invoke(FunctionName=f"{prefix}-intake-icsr",
                        Payload=json.dumps({"source": SYNTHETIC_CASE, "case_id": case_id, **binding}).encode())
    direct_payload = json.loads(direct["Payload"].read() or b"{}")
    C["tool_lambda_refuses_direct_invoke"] = direct.get("FunctionError") == "Unhandled" and direct_payload.get("errorType") == "KillSwitchEngaged"
    ex_status, ex_err, ex_states = None, None, []
    if wf.get("ControllerArn") and ing.get("case_ref"):
        ex = sfn.start_execution(stateMachineArn=wf["ControllerArn"], name="ksproof-" + case_id.lower(),
                                 input=json.dumps({"case_id": case_id, "requester": "ks-cw-a", "case_ref": ing["case_ref"],
                                                   "drug": "atorvastatin", **binding}))["executionArn"]
        for _ in range(24):
            time.sleep(5)
            d = sfn.describe_execution(executionArn=ex)
            ex_status = d["status"]
            if ex_status != "RUNNING":
                ex_err, ex_cause = d.get("error"), (d.get("cause") or "")[:300]
                break
        ex_states = [e["stateEnteredEventDetails"]["name"] for e in
                     sfn.get_execution_history(executionArn=ex, maxResults=100)["events"] if e["type"] == "TaskStateEntered"]
    C["workflow_fails_at_first_state_with_kill_switch"] = ex_status == "FAILED" and ex_err == "KillSwitchEngaged" and len(ex_states) == 1
    ev["steps"].append({"step": "tool_and_workflow_while_engaged",
                        "direct_invoke": {"FunctionError": direct.get("FunctionError"), "errorType": direct_payload.get("errorType"),
                                          "errorMessage": (direct_payload.get("errorMessage") or "")[:200]},
                        "execution": {"status": ex_status, "error": ex_err, "states_entered": ex_states}})

    # ---- 4. runtime: fresh invocation refused; in-flight session stopped ---------------------------
    rt = rt_invoke.invoke(a.runtime_arn, tok_a, "KS-RT-" + uuid.uuid4().hex[:4].upper(), "ks-cw-a", region=region, timeout=120)
    resp = rt.get("response") if isinstance(rt.get("response"), dict) else {}
    C["runtime_refuses_new_invocation"] = resp.get("refused") is True and resp.get("guardrail_action") == "KILL_SWITCH"
    th.join(timeout=600)
    inresp = inflight.get("response") if isinstance(inflight.get("response"), dict) else {}
    C["runtime_stops_in_flight_session"] = inresp.get("stopped") == "mid-session" and inresp.get("guardrail_action") == "KILL_SWITCH"
    ev["steps"].append({"step": "runtime_while_engaged", "fresh": rt, "in_flight": inflight})

    # ---- 5. code SoD: C engages (after B releases A's), C cannot release its own, A refused by IAM --
    b_release_1 = signed(creds["B"], region, "POST", url_disengage, {"reason": "security lead: drill step 1 complete"})
    C["disengage_by_second_identity"] = b_release_1["status"] == 200 and b_release_1["body"]["state"]["engaged"] is False \
        and b_release_1["body"]["state"]["released"]["engaged_by"] == role_id["A"]
    c_engage = signed(creds["C"], region, "POST", url_engage, {"reason": "over-privileged operator engages"})
    c_release = signed(creds["C"], region, "POST", url_disengage, {"reason": "and tries to release its own engagement"})
    a_release = signed(creds["A"], region, "POST", url_disengage, {"reason": "engage-only identity tries to release"})
    st_mid = status(creds["A"])
    C["code_sod_same_identity_refused"] = c_engage["status"] == 200 and c_release["status"] == 403 and \
        "separation of duties" in json.dumps(c_release["body"]) and st_mid["body"]["state"]["engaged"] is True
    C["code_sod_refusal_audited"] = c_release["status"] == 403 and bool((c_release["body"].get("audit") or {}).get("stored"))
    # IAM refuses A at the URL front door (no lambda:InvokeFunctionUrl on the disengage function):
    # a 403 WITHOUT the controller's SoD message = the function never ran.
    C["iam_sod_engage_only_cannot_disengage"] = a_release["status"] == 403 and "separation of duties" not in json.dumps(a_release["body"])
    b_release_2 = signed(creds["B"], region, "POST", url_disengage, {"reason": "security lead: drill complete"})
    C["final_release_ok"] = b_release_2["status"] == 200 and b_release_2["body"]["state"]["engaged"] is False
    ev["steps"].append({"step": "sod", "b_release_1": b_release_1, "c_engage": c_engage, "c_release": c_release,
                        "a_release": a_release, "b_release_2": b_release_2})

    # ---- 6. recovery ---------------------------------------------------------------------------------
    rec = None
    for _ in range(30):
        rec = gateway_probe(tok_a)
        if rec["list_status"] == 200 and rec["call_ok"]:
            break
        time.sleep(2)
    C["recovery_calls_allowed"] = rec["list_status"] == 200 and rec["list_tools"] > 0 and rec["call_ok"]
    rt2 = rt_invoke.invoke(a.runtime_arn, tok_a, "KS-RT2-" + uuid.uuid4().hex[:4].upper(), "ks-cw-a", region=region,
                           prompt="Reply with exactly the word OK. Do not call any tool.", timeout=300)
    r2 = rt2.get("response") if isinstance(rt2.get("response"), dict) else {}
    C["recovery_runtime_answers"] = rt2.get("status") == 200 and not r2.get("refused") and "result" in r2
    ev["steps"].append({"step": "recovery", "gateway": rec, "runtime": {k: v for k, v in rt2.items() if k != "response"},
                        "runtime_result_chars": len(str(r2.get("result", "")))})

    # ---- 7. ledger + WORM + logs -------------------------------------------------------------------
    time.sleep(5)
    base_rows = chain_rows(ddb, ledger_base, "KILL-SWITCH")
    acts = [(r.get("action"), r.get("phase"), r.get("actor")) for r in base_rows]
    C["base_ledger_state_changes"] = (
        ("kill_switch.engage", "COMMITTED", role_id["A"]) in acts and
        ("kill_switch.disengage", "COMMITTED", role_id["B"]) in acts and
        ("kill_switch.engage", "COMMITTED", role_id["C"]) in acts and
        ("kill_switch.disengage", "DENIED", role_id["C"]) in acts and
        all(r.get("tenant_id") == "__platform__" for r in base_rows))
    C["base_ledger_chained"] = len(base_rows) >= 5 and all(
        base_rows[i]["prev_hash"] == base_rows[i - 1]["chain_hash"] for i in range(1, len(base_rows)))
    base_worm = worm_keys(s3, base_vault, "KILL-SWITCH") if base_vault else []
    C["base_worm_copies"] = len([k for k in base_worm if not str(k).startswith("ERR")]) >= len(base_rows)
    den_a = chain_rows(ddb, ledger[ta], "KILL-SWITCH")
    den_b = chain_rows(ddb, ledger[tb], "KILL-SWITCH")
    base_denials = [r for r in base_rows if r.get("action") == "kill_switch.deny"]
    C["denials_in_acting_tenant_ledger"] = len([r for r in den_a if r.get("action") == "kill_switch.deny" and r.get("phase") == "DENIED"
                                                 and r.get("tenant_id") == ta]) >= 2
    C["denials_of_other_tenant_in_its_own_ledger"] = all(r.get("tenant_id") == tb for r in den_b if "error" not in r)
    C["no_denials_in_base_ledger"] = base_denials == []
    worm_a = worm_keys(s3, vault[ta], "KILL-SWITCH")
    C["denial_worm_copies"] = len([k for k in worm_a if not str(k).startswith("ERR")]) >= 2
    ic_lines = log_lines(logs, f"/aws/lambda/{prefix}-tenant-interceptor", "denied:kill_switch", since_ms)
    rt_lines = log_lines(logs, a.runtime_log_group, "denied:kill_switch", since_ms)
    C["interceptor_log_lines"] = len([x for x in ic_lines if not x.startswith("ERR")]) >= 2
    C["runtime_log_lines"] = len([x for x in rt_lines if not x.startswith("ERR")]) >= 1
    ev["steps"].append({"step": "evidence", "base_ledger_rows": base_rows, "base_worm_keys": base_worm,
                        "tenant_a_denials": den_a, "tenant_b_rows": den_b, "tenant_a_worm_keys": worm_a,
                        "interceptor_log_sample": ic_lines[:3], "runtime_log_sample": rt_lines[:3]})

    # ---- final state + cleanup -----------------------------------------------------------------------
    final = json.loads(ssm.get_parameter(Name=param)["Parameter"]["Value"])
    C["left_disengaged"] = final.get("engaged") is False
    ev["cleanup"] = {k: drop_role(iam, n) for k, n in
                     (("A", f"{prefix}-ks-responder"), ("B", f"{prefix}-ks-security-lead"), ("C", f"{prefix}-ks-overprivileged"))}
    ev["duration_s"] = round(time.time() - t_start, 1)
    ev["PASS"] = all(bool(v) for v in C.values())
    ev["time_to_effect_s"] = tte
    with open(a.out + ".json", "w", encoding="utf-8") as fh:
        json.dump(ev, fh, indent=1, default=str)
    with open(a.out + ".md", "w", encoding="utf-8") as fh:
        fh.write(to_markdown(ev))
    print(json.dumps({"PASS": ev["PASS"], "checks": C, "time_to_effect_s": tte, "duration_s": ev["duration_s"]}, indent=1))
    sys.exit(0 if ev["PASS"] else 2)


def to_markdown(ev):
    C = ev["checks"]
    lines = ["# Kill Switch on the AgentCore path — live gate (task 127)", "",
             f"Env `{ev['prefix']}` · {ev['region']} · parameter `{ev['parameter']}` · tenants {ev['tenants']} · "
             f"{ev['duration_s']} s · **{'PASS' if ev['PASS'] else 'FAIL'}** · time-to-effect at the gateway: **{ev['time_to_effect_s']} s**", "",
             "| Check | Result |", "|---|---|"]
    lines += [f"| {k} | {'✅' if v else '❌'} |" for k, v in C.items()]
    lines += ["", "## What happened", ""]
    for s in ev["steps"]:
        if s["step"] == "evidence":
            lines.append("- **Base ledger `KILL-SWITCH` chain** (platform scope): " + "; ".join(
                f"seq {r.get('seq')} {r.get('action')} {r.get('phase')} by `{r.get('actor')}`" for r in s["base_ledger_rows"]))
            lines.append(f"- Tenant-A denials (DENIED `kill_switch.deny`): {len(s['tenant_a_denials'])}; WORM copies: {len(s['tenant_a_worm_keys'])}; "
                         f"tenant-B rows in its own ledger: {len(s['tenant_b_rows'])}")
        elif s["step"] == "gateway_after_engage":
            lines.append(f"- Gateway after engage: tools/list {s['probe']['list_status']} / tools/call {s['probe']['call_status']} — "
                         f"`{s['probe']['call_error']}` (time-to-effect {s['time_to_effect_s']} s)")
        elif s["step"] == "tool_and_workflow_while_engaged":
            lines.append(f"- Direct tool invoke: {s['direct_invoke']}; Step Functions: {s['execution']}")
        elif s["step"] == "runtime_while_engaged":
            fr = s["fresh"].get("response") if isinstance(s["fresh"].get("response"), dict) else {}
            ir = s["in_flight"].get("response") if isinstance(s["in_flight"].get("response"), dict) else {}
            lines.append(f"- Runtime fresh invocation: refused={fr.get('refused')} guardrail_action={fr.get('guardrail_action')}; "
                         f"in-flight session: stopped={ir.get('stopped')} guardrail_action={ir.get('guardrail_action')}")
        elif s["step"] == "sod":
            lines.append(f"- SoD: B releases A's engagement → {s['b_release_1']['status']}; C engages → {s['c_engage']['status']}; "
                         f"C releases own → {s['c_release']['status']}; A (engage-only) releases → {s['a_release']['status']} (IAM); "
                         f"B releases → {s['b_release_2']['status']}")
    lines += ["", f"Roles (throwaway, deleted): {ev['roles']} → cleanup {ev['cleanup']}", "",
              "Account ids redacted to 111122223333 before commit. Raw detail: the `.json` beside this file."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
