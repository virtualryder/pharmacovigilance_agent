#!/usr/bin/env python3
"""Phase 111 — the consolidated post-SaaS validation gate, on ONE deployment of the release tag.

Runs, in order, against the same live environment (multi-tenant, 2 tenants, model logging on, the
AgentCore Runtime launched):
  1. scripts/mt_two_tenant_proof.py   — cross-tenant deny, per-tenant store routing, per-tenant audit
                                        ledger / WORM vault / approvals routing on the gateway AND the
                                        workflow hop, fail-closed without the signed pair   (12 checks)
  2. scripts/obs_two_tenant_proof.py  — full transparency through the real Runtime, per tenant (13 each)
  3. scripts/pii_canary.py --strict   — Gate-B B4 telemetry-leak canary on the workflow path (tenant A)
and writes ONE verdict JSON. Each sub-proof's verbatim output is kept beside it.

Usage: python scripts/gate_111.py --env mt4 --tenants sp-a,sp-b --runtime-arn <arn>
       --runtime-log-group <group> --out .build/gate111.json"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def run(cmd, out_path):
    t0 = time.time()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    pathlib.Path(out_path).write_text(p.stdout, encoding="utf-8")
    pathlib.Path(str(out_path) + ".err").write_text(p.stderr, encoding="utf-8")
    body = None
    try:
        txt = p.stdout
        body = json.loads(txt[txt.find("{"):])
    except Exception:
        pass
    return {"cmd": " ".join(cmd), "exit": p.returncode, "seconds": round(time.time() - t0, 1), "json": body}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--tenants", default="sp-a,sp-b")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--runtime-arn", required=True)
    ap.add_argument("--runtime-log-group", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"pv-{a.env}"
    ta = a.tenants.split(",")[0].strip()
    py = sys.executable
    gate = {"env": a.env, "prefix": prefix, "tenants": a.tenants.split(","), "started_at": int(time.time()),
            "git": {}, "steps": {}}
    try:
        gate["git"] = {"describe": subprocess.run(["git", "describe", "--tags", "--always"], capture_output=True, text=True).stdout.strip(),
                       "sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
                       "dirty": bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())}
    except Exception:
        pass

    gate["steps"]["mt"] = run([py, str(HERE / "mt_two_tenant_proof.py"), "--env", a.env, "--tenants", a.tenants, "--region", a.region],
                              out.with_name(out.stem + "-mt.json"))
    gate["steps"]["obs"] = run([py, str(HERE / "obs_two_tenant_proof.py"), "--env", a.env, "--tenants", a.tenants, "--region", a.region,
                                "--runtime-arn", a.runtime_arn, "--runtime-log-group", a.runtime_log_group,
                                "--out", str(out.with_name(out.stem + "-obs.json"))], out.with_name(out.stem + "-obs.stdout"))
    # a tenant caseworker token for the canary's multi-tenant ingest (the same disposable users the proofs made)
    import boto3
    from mt_two_tenant_proof import access_token, make_user, outputs
    import secrets
    cf = boto3.client("cloudformation", region_name=a.region); idp = boto3.client("cognito-idp", region_name=a.region)
    ident = outputs(cf, f"{prefix}-identity")
    pw = "Gate-" + secrets.token_urlsafe(12) + "aA1!"
    make_user(idp, ident["UserPoolId"], "cw-a", ["pv_reviewer", f"tenant_{ta}"], pw)
    time.sleep(2)
    tok = access_token(ident["UserPoolId"], ident["ClientId"], a.region, "cw-a", pw)
    obs_outputs = outputs(cf, f"{prefix}-observability")
    cmd = [py, str(HERE / "pii_canary.py"), "--prefix", prefix, "--execute", "--strict", "--wait", "150", "--access-token", tok]
    if obs_outputs.get("GatewayRequestLogGroup"):
        cmd += ["--extra-log-group", obs_outputs["GatewayRequestLogGroup"]]      # gated: the gateway never sees raw content
    if obs_outputs.get("ModelInvocationLogGroup"):
        cmd += ["--info-log-group", obs_outputs["ModelInvocationLogGroup"]]      # reported only (see pii_canary --help)
    gate["steps"]["canary"] = run(cmd, out.with_name(out.stem + "-canary.json"))
    gate["steps"]["canary"]["cmd"] = gate["steps"]["canary"]["cmd"].replace(tok, "<access-token>")

    mt = gate["steps"]["mt"]["json"] or {}; obs = gate["steps"]["obs"]["json"] or {}; can = gate["steps"]["canary"]["json"] or {}
    verdict = {
        "isolation_and_audit_routing_12_checks": bool((mt.get("verdict") or {}).get("PASS")),
        "transparency_13_checks_per_tenant": bool((obs.get("verdict") or {}).get("PASS")),
        "pii_canary_strict": can.get("verdict") == "PASS",
    }
    verdict["PASS"] = all(verdict.values())
    gate["verdict"] = verdict
    gate["finished_at"] = int(time.time())
    out.write_text(json.dumps(gate, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "git": gate["git"], "mt": (mt.get("verdict") or {}), "obs": (obs.get("verdict") or {}),
                      "canary": {k: can.get(k) for k in ("verdict", "hits", "marker") if k in can}}, indent=1, default=str))
    sys.exit(0 if verdict["PASS"] else 1)


if __name__ == "__main__":
    main()
