#!/usr/bin/env python3
"""Phase 111 - LIVE two-tenant proof for the hybrid multi-tenant control plane.

Drives the deployed AgentCore gateway as THREE identities and records verbatim results:
  * cw-a   : pv_reviewer + tenant_sp-a  -> allowed; mask_pii routes to sp-a's OWN store
  * cw-b   : pv_reviewer + tenant_sp-b  -> allowed; routes to sp-b's OWN store
  * cw-none: pv_reviewer, NO tenant      -> DENIED at the gateway (require_tenant / interceptor)
Then proves physical isolation: after cw-a's call only sp-a's sanitized store holds the artifact,
and after cw-b's only sp-b's - never the other tenant's, never the base silo table.

governed-core 1.6.0 (cross-repo per-tenant AUDIT routing) adds:
  * gateway write_audit as cw-a / cw-b -> the hash-chained record + WORM copy land ONLY in that
    tenant's ledger (<prefix>-<tenant>-audit-ledger) and vault (<prefix>-<tenant>-worm-<acct>);
  * the WORKFLOW hop (no interceptor): ingest with cw-a's verified token mints the signed tenant
    pair; an execution started with it writes its INTENT evidence + pending-approval into sp-a's
    stores only (execution stopped at the sign-off pause); an execution started WITHOUT the pair
    fails at the first state (fail-closed) and writes nothing.

Usage: python scripts/mt_two_tenant_proof.py --env mt --tenants sp-a,sp-b --region us-east-1
Creates disposable Cognito users (admin-create, permanent password) and authenticates via SRP.
Synthetic data only. Writes evidence JSON to stdout."""
import argparse
import json
import secrets
import sys
import time
import uuid

import boto3
import requests
from pycognito import Cognito

SYNTHETIC_CASE = ("Patient Jane Q. Sample, DOB 1990-01-01, 12 Elm St Springfield, phone 555-0100. Suspect product "
                  "atorvastatin 40 mg daily; adverse event: rhabdomyolysis, hospitalized 2026-08-01; reporter: physician.")


def outputs(cf, stack):
    d = cf.describe_stacks(StackName=stack)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in d.get("Outputs", [])}


def make_user(idp, pool, name, groups, password):
    try:
        idp.admin_create_user(UserPoolId=pool, Username=name, MessageAction="SUPPRESS",
                              TemporaryPassword=password)
    except idp.exceptions.UsernameExistsException:
        pass
    idp.admin_set_user_password(UserPoolId=pool, Username=name, Password=password, Permanent=True)
    for g in groups:
        idp.admin_add_user_to_group(UserPoolId=pool, Username=name, GroupName=g)


def access_token(pool, client, region, name, password):
    u = Cognito(pool, client, user_pool_region=region, username=name)
    u.authenticate(password=password)
    return u.access_token


class Mcp:
    """Minimal streamable-HTTP JSON-RPC client for an AgentCore gateway."""
    def __init__(self, url, token):
        self.url, self.token, self.sid, self.n = url, token, None, 0

    def call(self, method, params=None):
        self.n += 1
        body = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            body["params"] = params
        h = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        r = requests.post(self.url, headers=h, json=body, timeout=60)
        self.sid = r.headers.get("Mcp-Session-Id", self.sid)
        text = r.text or ""
        payload = None
        try:
            payload = r.json()
        except Exception:
            for line in text.splitlines():           # SSE frames: data: {...}
                if line.startswith("data:"):
                    try:
                        payload = json.loads(line[5:].strip())
                    except Exception:
                        pass
        return {"status": r.status_code, "body": payload if payload is not None else text[:800]}

    def init(self):
        out = self.call("initialize", {"protocolVersion": "2025-03-26",
                                       "capabilities": {}, "clientInfo": {"name": "mt-proof", "version": "1"}})
        try:
            self.call("notifications/initialized")
        except Exception:
            pass
        return out


def count_items(ddb, table):
    try:
        return ddb.scan(TableName=table, Select="COUNT")["Count"]
    except Exception as exc:
        return "ERR:" + type(exc).__name__


def count_objects(s3, bucket):
    try:
        n, tok = 0, None
        while True:
            kw = {"Bucket": bucket, "MaxKeys": 1000}
            if tok:
                kw["ContinuationToken"] = tok
            r = s3.list_objects_v2(**kw)
            n += r.get("KeyCount", 0)
            tok = r.get("NextContinuationToken")
            if not tok:
                return n
    except Exception as exc:
        return "ERR:" + type(exc).__name__


def has_item(ddb, table, key):
    try:
        return "Item" in ddb.get_item(TableName=table, Key=key)
    except Exception as exc:
        return "ERR:" + type(exc).__name__


def tool_result(call):
    """The JSON the tool returned (AgentCore wraps it as result.content[0].text)."""
    b = call.get("body")
    try:
        return json.loads(b["result"]["content"][0]["text"])
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="mt")
    ap.add_argument("--tenants", default="sp-a,sp-b")
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()
    prefix = f"pv-{a.env}"
    ta, tb = [t.strip() for t in a.tenants.split(",")][:2]
    cf = boto3.client("cloudformation", region_name=a.region)
    idp = boto3.client("cognito-idp", region_name=a.region)
    ddb = boto3.client("dynamodb", region_name=a.region)

    ident, gw = outputs(cf, f"{prefix}-identity"), outputs(cf, f"{prefix}-gateway")
    pool, client, url = ident["UserPoolId"], ident["ClientId"], gw["GatewayUrl"]
    ev = {"env": a.env, "prefix": prefix, "tenants": [ta, tb], "gateway_url": url,
          "enforcement": gw.get("Enforcement"), "policy_engine": gw.get("PolicyEngineId"), "steps": []}

    pw = "Mt-" + secrets.token_urlsafe(12) + "aA1!"
    users = {"cw-a": ["pv_reviewer", f"tenant_{ta}"],
             "cw-b": ["pv_reviewer", f"tenant_{tb}"],
             "cw-none": ["pv_reviewer"]}
    for name, groups in users.items():
        make_user(idp, pool, name, groups, pw)
    ev["steps"].append({"step": "users", "created": {k: v for k, v in users.items()}})
    time.sleep(3)

    tok = {name: access_token(pool, client, a.region, name, pw) for name in users}

    san = {ta: f"{prefix}-{ta}-sanitized-artifacts", tb: f"{prefix}-{tb}-sanitized-artifacts",
           "base": f"{prefix}-sanitized-artifacts"}
    before = {k: count_items(ddb, v) for k, v in san.items()}
    ev["steps"].append({"step": "store_counts_before", "counts": before})

    def drive(name, expect_allowed):
        m = Mcp(url, tok[name])
        init = m.init()
        lst = m.call("tools/list")
        tools = []
        if isinstance(lst["body"], dict):
            tools = [t.get("name") for t in (lst["body"].get("result", {}) or {}).get("tools", [])]
        call = m.call("tools/call", {"name": "mask-pii___mask_pii",
                                     "arguments": {"case": SYNTHETIC_CASE + " " + str(uuid.uuid4())}})
        rec = {"identity": name, "groups": users[name], "expect_allowed": expect_allowed,
               "initialize": init, "tools_list": {"status": lst["status"], "tools": tools,
                                                  "error": (lst["body"].get("error") if isinstance(lst["body"], dict) else lst["body"])},
               "mask_pii_call": call}
        ev["steps"].append(rec)
        return rec

    ra = drive("cw-a", True)
    mid = {k: count_items(ddb, v) for k, v in san.items()}
    ev["steps"].append({"step": "store_counts_after_cw-a", "counts": mid})
    rb = drive("cw-b", True)
    after = {k: count_items(ddb, v) for k, v in san.items()}
    ev["steps"].append({"step": "store_counts_after_cw-b", "counts": after})
    rn = drive("cw-none", False)

    def ok_call(r):
        b = r["mask_pii_call"]["body"]
        return r["mask_pii_call"]["status"] == 200 and isinstance(b, dict) and "error" not in b and not (
            isinstance(b.get("result"), dict) and b["result"].get("isError"))

    def grew(k, x, y):
        return isinstance(x.get(k), int) and isinstance(y.get(k), int) and y[k] > x[k]

    # ---- governed-core 1.6.0: per-tenant AUDIT routing ------------------------------------------
    acct = boto3.client("sts", region_name=a.region).get_caller_identity()["Account"]
    s3 = boto3.client("s3", region_name=a.region)
    lam = boto3.client("lambda", region_name=a.region)
    sfn = boto3.client("stepfunctions", region_name=a.region)
    base_data = outputs(cf, f"{prefix}-data")
    aud = {ta: f"{prefix}-{ta}-audit-ledger", tb: f"{prefix}-{tb}-audit-ledger", "base": f"{prefix}-audit-ledger"}
    worm = {ta: f"{prefix}-{ta}-worm-{acct}", tb: f"{prefix}-{tb}-worm-{acct}", "base": base_data.get("WormBucketName", "")}
    pend = {ta: f"{prefix}-{ta}-pending-approvals", tb: f"{prefix}-{tb}-pending-approvals", "base": f"{prefix}-pending-approvals"}

    def audit_counts():
        return {"ledger": {k: count_items(ddb, v) for k, v in aud.items()},
                "worm": {k: count_objects(s3, v) for k, v in worm.items()}}

    def write_audit(name):
        m = Mcp(url, tok[name])
        m.init()
        call = m.call("tools/call", {"name": "write-audit___write_audit",
                                     "arguments": {"icsr_id": "MT-" + uuid.uuid4().hex[:8].upper(),
                                                   "action": "mt-audit-routing-proof", "phase": "INTENT",
                                                   "actor": name, "payload": "{\"synthetic\": true}"}})
        return {"identity": name, "call": call, "tool_result": tool_result(call)}

    a0 = audit_counts()
    wa = write_audit("cw-a")
    a1 = audit_counts()
    wb = write_audit("cw-b")
    a2 = audit_counts()
    ev["steps"].append({"step": "audit_gateway", "before": a0, "cw-a": wa, "after_cw-a": a1,
                        "cw-b": wb, "after_cw-b": a2})

    # workflow hop: token-verified ingest mints the signed pair; the execution carries it
    ctrl = outputs(cf, f"{prefix}-workflow").get("ControllerArn")
    case_id = "MT-WF-" + uuid.uuid4().hex[:6].upper()
    ing = json.loads(lam.invoke(FunctionName=f"{prefix}-ingest-case",
                                Payload=json.dumps({"source": SYNTHETIC_CASE, "case_id": case_id,
                                                    "access_token": tok["cw-a"]}).encode())["Payload"].read())
    ing_notoken = json.loads(lam.invoke(FunctionName=f"{prefix}-ingest-case",
                                        Payload=json.dumps({"source": SYNTHETIC_CASE, "case_id": case_id + "-X",
                                                            "tenant": ta}).encode())["Payload"].read())
    p0 = {k: has_item(ddb, v, {"case_id": {"S": case_id}}) for k, v in pend.items()}
    w0 = audit_counts()
    wf = {"ingest": {k: v for k, v in ing.items() if k != "tenant_binding"},
          "ingest_minted_binding": bool(ing.get("tenant_binding")),
          "ingest_without_token": ing_notoken, "pending_before": p0}
    ex_status, ex_hist = None, []
    if ing.get("case_ref") and ctrl:
        ex = sfn.start_execution(stateMachineArn=ctrl, name="mtproof-" + case_id.lower(),
                                 input=json.dumps({"case_id": case_id, "requester": "cw-a",
                                                   "case_ref": ing["case_ref"],
                                                   "drug": "atorvastatin",
                                                   **ing.get("tenant_binding", {})}))["executionArn"]
        for _ in range(60):
            time.sleep(5)
            d = sfn.describe_execution(executionArn=ex)
            ex_status = d["status"]
            names = [e.get("stateEnteredEventDetails", {}).get("name") for e in
                     sfn.get_execution_history(executionArn=ex, maxResults=200)["events"]
                     if e["type"] == "TaskStateEntered"]
            ex_hist = [n for n in names if n]
            if ex_status != "RUNNING" or "HumanSignoff" in ex_hist:
                break
        if ex_status == "RUNNING":
            time.sleep(5)      # let signoff_register + AuditIntent settle
            sfn.stop_execution(executionArn=ex, cause="mt proof complete (sign-off pause reached)")
        wf.update({"execution": ex, "status": ex_status, "states": ex_hist})
        # fail-closed: the same execution WITHOUT the signed pair
        ex2 = sfn.start_execution(stateMachineArn=ctrl, name="mtproof-nobind-" + case_id.lower(),
                                  input=json.dumps({"case_id": case_id + "-NB", "requester": "cw-a",
                                                    "case_ref": ing["case_ref"],
                                                    "drug": "atorvastatin"}))["executionArn"]
        for _ in range(24):
            time.sleep(5)
            d2 = sfn.describe_execution(executionArn=ex2)
            if d2["status"] != "RUNNING":
                break
        wf["execution_without_binding"] = {"status": d2["status"], "error": d2.get("error"),
                                           "cause": (d2.get("cause") or "")[:300]}
    p1 = {k: has_item(ddb, v, {"case_id": {"S": case_id}}) for k, v in pend.items()}
    w1 = audit_counts()
    wf.update({"pending_after": p1, "audit_before": w0, "audit_after": w1})
    ev["steps"].append({"step": "audit_workflow", **wf})

    def only(k, x, y, kind):
        others = [o for o in x[kind] if o != k]
        return grew(k, x[kind], y[kind]) and not any(grew(o, x[kind], y[kind]) for o in others)

    verdict = {
        "cw-a_allowed": ok_call(ra),
        "cw-b_allowed": ok_call(rb),
        "cw-none_denied": (not ok_call(rn)) and (rn["tools_list"]["status"] in (401, 403) or not rn["tools_list"]["tools"]
                                                 or rn["mask_pii_call"]["status"] in (401, 403)
                                                 or (isinstance(rn["mask_pii_call"]["body"], dict) and "error" in rn["mask_pii_call"]["body"])),
        "routing_cw-a_only_to_sp-a": grew(ta, before, mid) and not grew(tb, before, mid) and not grew("base", before, mid),
        "routing_cw-b_only_to_sp-b": grew(tb, mid, after) and not grew(ta, mid, after) and not grew("base", mid, after),
    }
    verdict.update({
        "audit_cw-a_ledger_and_worm_only_sp-a": (wa["tool_result"].get("stored") is True and wa["tool_result"].get("worm") is True
                                                  and only(ta, a0, a1, "ledger") and only(ta, a0, a1, "worm")),
        "audit_cw-b_ledger_and_worm_only_sp-b": (wb["tool_result"].get("stored") is True and wb["tool_result"].get("worm") is True
                                                  and only(tb, a1, a2, "ledger") and only(tb, a1, a2, "worm")),
        "ingest_refuses_without_verified_token": ing_notoken.get("ingested") is False,
        "workflow_reached_signoff_with_binding": "HumanSignoff" in (wf.get("states") or []),
        "workflow_intent_evidence_only_sp-a": only(ta, w0, w1, "ledger") and only(ta, w0, w1, "worm"),
        "workflow_pending_approval_only_sp-a": p1.get(ta) is True and p1.get(tb) is False and p1.get("base") is False,
        "workflow_without_binding_fails_closed": (wf.get("execution_without_binding") or {}).get("status") == "FAILED",
    })
    verdict["PASS"] = all(verdict.values())
    ev["verdict"] = verdict
    print(json.dumps(ev, indent=1, default=str))
    sys.exit(0 if verdict["PASS"] else 1)


if __name__ == "__main__":
    main()
