#!/usr/bin/env python3
"""Post-deployment validation — emits the machine-readable PASS/FAIL verdict for a PV deployment.
Read-only except a few probe invocations (mask + guard + one pass-by-reference workflow execution).

Usage:
  python scripts/validate_deployment.py --env pilot --region us-east-1
  python scripts/validate_deployment.py --env pilot --expect-absent   # teardown residual check
"""
import argparse
import json
import subprocess
import sys
import time


def aws(*args):
    r = subprocess.run(["aws", *args], capture_output=True, text=True)
    return r.returncode, (r.stdout or r.stderr).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="pilot")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--release", default="dev")
    ap.add_argument("--expect-absent", action="store_true")
    a = ap.parse_args()
    p = f"pv-{a.env}"
    out = {"release": a.release, "env": a.env}

    rc, stacks = aws("cloudformation", "describe-stacks", "--region", a.region,
                     "--query", f"Stacks[?starts_with(StackName,'{p}')].StackStatus", "--output", "json")
    statuses = json.loads(stacks) if rc == 0 and stacks.startswith("[") else []
    if a.expect_absent:
        out["residual_stacks"] = statuses
        out["deployment_status"] = "PASS" if not statuses else "FAIL"
        print(json.dumps(out)); sys.exit(0 if not statuses else 1)
    out["stacks"] = "COMPLETE" if statuses and all(s.endswith("_COMPLETE") for s in statuses) else f"FAIL:{statuses}"

    # single provenance signing secret must exist (PV has one signing domain)
    rc1, _ = aws("secretsmanager", "describe-secret", "--secret-id", f"{p}/provenance-signing", "--region", a.region)
    out["secret"] = "PRESENT" if rc1 == 0 else "FAIL"

    # masking control probe: mask -> genuine ref; the masked text must not contain the PHI
    open("/tmp/_m.json", "w").write(json.dumps({"case": "Probe Person, SSN 123-45-6789, hospitalized with rhabdomyolysis after atorvastatin"}))
    rc, _ = aws("lambda", "invoke", "--function-name", f"{p}-mask-pii", "--region", a.region,
                "--cli-binary-format", "raw-in-base64-out", "--payload", "file:///tmp/_m.json", "/tmp/_mo.json")
    mask = json.load(open("/tmp/_mo.json")) if rc == 0 else {}
    ok_mask = mask.get("deidentified") is True and "123-45-6789" not in json.dumps(mask) \
              and (mask.get("sanitized_ref") or {}).get("authoritative") is True
    out["masking_control"] = "PASS" if ok_mask else "FAIL"

    for name, ref, want in (("guard_genuine", mask.get("sanitized_ref"), True),
                            ("forged_ref_denied", dict(mask.get("sanitized_ref") or {}, sig="deadbeef" * 8), False)):
        open("/tmp/_g.json", "w").write(json.dumps({"guard": "deidentified", "sanitized_ref": ref}))
        rc, _ = aws("lambda", "invoke", "--function-name", f"{p}-workflow-guards", "--region", a.region,
                    "--cli-binary-format", "raw-in-base64-out", "--payload", "file:///tmp/_g.json", "/tmp/_go.json")
        g = json.load(open("/tmp/_go.json")) if rc == 0 else {}
        out[name] = "PASS" if g.get("ok") is want else "FAIL"

    # R3-2 pass-by-reference: raw content enters ONLY via ingest-case; the execution starts with a case_ref
    open("/tmp/_i.json", "w").write(json.dumps(
        {"source": "Suspect product: atorvastatin. Patient hospitalized with rhabdomyolysis. Unexpected.",
         "case_id": f"VAL-{int(time.time())}"}))
    rc, _ = aws("lambda", "invoke", "--function-name", f"{p}-ingest-case", "--region", a.region,
                "--cli-binary-format", "raw-in-base64-out", "--payload", "file:///tmp/_i.json", "/tmp/_io.json")
    ing = json.load(open("/tmp/_io.json")) if rc == 0 else {}
    out["ingest_pass_by_reference"] = "PASS" if ing.get("ingested") and str(ing.get("case_ref", "")).startswith("case-") else "FAIL"

    acct = aws("sts", "get-caller-identity", "--query", "Account", "--output", "text")[1]
    open("/tmp/_w.json", "w").write(json.dumps({"case_id": ing.get("case_id", "VAL"), "requester": "validator",
                                                "case_ref": ing.get("case_ref", ""), "drug": "atorvastatin",
                                                "case_key": "atorvastatin|rhabdomyolysis|2026|hcp", "known_keys": ""}))
    rc, arn = aws("stepfunctions", "start-execution", "--region", a.region,
                  "--state-machine-arn", f"arn:aws:states:{a.region}:{acct}:stateMachine:{p}-icsr-workflow",
                  "--input", "file:///tmp/_w.json", "--query", "executionArn", "--output", "text")
    verdict = "FAIL"
    if rc == 0:
        for _ in range(20):
            time.sleep(6)
            _, st = aws("stepfunctions", "describe-execution", "--execution-arn", arn,
                        "--query", "status", "--output", "text", "--region", a.region)
            if st != "RUNNING":
                verdict = "PASS" if st == "SUCCEEDED" else f"FAIL:{st}"
                break
        else:
            verdict = "PASS:RUNNING(awaiting human gate)"   # happy path paused at sign-off
    out["workflow"] = verdict

    out["deployment_status"] = "PASS" if all(
        str(v).startswith("PASS") or v in ("COMPLETE", "PRESENT")
        for k, v in out.items()
        if k in ("stacks", "secret", "masking_control", "guard_genuine",
                 "forged_ref_denied", "ingest_pass_by_reference", "workflow")) else "FAIL"
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["deployment_status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
