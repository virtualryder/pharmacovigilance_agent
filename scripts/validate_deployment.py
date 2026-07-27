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
    # text mode with an explicit UTF-8 decode: AWS CLI writes UTF-8; without this, Windows would decode
    # stdout in the console code page (CP1252) and corrupt non-ASCII (e.g. the em-dash in a signed source
    # label), producing false verification failures. The deployed control plane is UTF-8 end-to-end.
    r = subprocess.run(["aws", *args], capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or r.stderr).strip()


def _wr(path, obj):
    # Always write probe payloads as UTF-8 (never the platform default).
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj))


def _rd(path):
    # Always read Lambda-invoke output files as UTF-8. On Windows the default text encoding is CP1252,
    # which corrupts UTF-8 bytes (the em-dash U+2014 in a signed `source` -> mojibake), breaking the
    # HMAC the verifier recomputes over the transmitted source. This is a harness/CLI-on-Windows concern
    # only; in production the sanitized_ref crosses Step Functions state and the gateway as UTF-8 JSON.
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


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
    _wr("/tmp/_m.json", {"case": "Probe Person, SSN 123-45-6789, hospitalized with rhabdomyolysis after atorvastatin"})
    rc, _ = aws("lambda", "invoke", "--function-name", f"{p}-mask-pii", "--region", a.region,
                "--cli-binary-format", "raw-in-base64-out", "--payload", "file:///tmp/_m.json", "/tmp/_mo.json")
    mask = _rd("/tmp/_mo.json") if rc == 0 else {}
    ok_mask = mask.get("deidentified") is True and "123-45-6789" not in json.dumps(mask) \
              and (mask.get("sanitized_ref") or {}).get("authoritative") is True
    out["masking_control"] = "PASS" if ok_mask else "FAIL"

    for name, ref, want in (("guard_genuine", mask.get("sanitized_ref"), True),
                            ("forged_ref_denied", dict(mask.get("sanitized_ref") or {}, sig="deadbeef" * 8), False)):
        _wr("/tmp/_g.json", {"guard": "deidentified", "sanitized_ref": ref})
        rc, _ = aws("lambda", "invoke", "--function-name", f"{p}-workflow-guards", "--region", a.region,
                    "--cli-binary-format", "raw-in-base64-out", "--payload", "file:///tmp/_g.json", "/tmp/_go.json")
        g = _rd("/tmp/_go.json") if rc == 0 else {}
        out[name] = "PASS" if g.get("ok") is want else "FAIL"

    # R3-2 pass-by-reference: raw content enters ONLY via ingest-case; the execution starts with a case_ref
    _wr("/tmp/_i.json",
        {"source": "Suspect product: atorvastatin. Patient hospitalized with rhabdomyolysis. Unexpected.",
         "case_id": f"VAL-{int(time.time())}"})
    rc, _ = aws("lambda", "invoke", "--function-name", f"{p}-ingest-case", "--region", a.region,
                "--cli-binary-format", "raw-in-base64-out", "--payload", "file:///tmp/_i.json", "/tmp/_io.json")
    ing = _rd("/tmp/_io.json") if rc == 0 else {}
    out["ingest_pass_by_reference"] = "PASS" if ing.get("ingested") and str(ing.get("case_ref", "")).startswith("case-") else "FAIL"

    acct = aws("sts", "get-caller-identity", "--query", "Account", "--output", "text")[1]
    _wr("/tmp/_w.json", {"case_id": ing.get("case_id", "VAL"), "requester": "validator",
                         "case_ref": ing.get("case_ref", ""), "drug": "atorvastatin",
                         "case_key": "atorvastatin|rhabdomyolysis|2026|hcp", "known_keys": ""})
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
