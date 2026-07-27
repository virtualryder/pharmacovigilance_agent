# Deployment Guide — Pharmacovigilance ICSR Assistant (AWS CDK)

*The authoritative step-by-step for the supported deployment path. CDK, at the validated release tag,
never `main`. The shell engine (`lib/engine/`) is a legacy internal reference and must not be used for
customer deployments.*

---

## 0. Supported path

```bash
git checkout v0.1.0-pilot-rc1                 # a validated release tag, never main
cd cdk && pip install -r requirements.txt
cdk bootstrap aws://<account>/us-east-1       # once per account
```

## 1. Deploy (full Gate-B posture)

```bash
cdk deploy --all \
  -c env=pilot \
  -c retention_profile=pilot \
  -c kms=customer-managed \
  -c network_mode=private \
  -c identity_mode=pilot \
  -c tenant=<sponsor-id>
```

Seven stacks deploy: `pv-pilot-{data,network,compute,workflow,identity,observability,gateway}`,
including the AgentCore Gateway + Cedar policies as IaC (no post-deploy shell steps). The openFDA
lookup needs **no API key** (public). Switches:

| Switch | Effect |
|---|---|
| `retention_profile=sandbox-demo\|pilot\|production-reference` | WORM Object-Lock mode + days (GOVERNANCE/1d sandbox → COMPLIANCE/7y prod) |
| `kms=customer-managed` | one CMK over tables, secrets, Lambda env, log groups, SNS |
| `network_mode=private` | VPC + Network Firewall egress allowlist = `.api.fda.gov` ONLY; tools in isolated subnets |
| `identity_mode=pilot` | MFA ON (software token), threat protection ENFORCED, admin-create-only, zero users |
| `tenant=<sponsor-id>` | HMAC-signed into sanitized artifacts (Gate-B B5) |

## 2. Run a case (execution-input contract)

Start the controller (`pv-pilot-icsr-workflow`) with:
`{case_id, requester, source, drug, case_key, known_keys}` — `source` is the raw adverse-event text,
`drug` the suspect product. The pipeline: extract → openFDA background → mask → seriousness → duplicate
check → (DuplicateHold if a duplicate) → draft narrative → INTENT audit → a **different** qualified
reviewer approves at the `waitForTaskToken` gate → finalize (exactly-once `FINAL#` marker).

> **Zero-PHI note (R3-2):** call the **ingest-case** Lambda FIRST — it stores the raw `source` in the
> encrypted case store and returns the opaque `case_ref` you pass to the workflow. Raw content never
> enters Step Functions state, and the masked text is reached only server-side via the signed
> `sanitized_ref`, so the strict PHI canary can PASS.

## 3. The EP1 validation (what cuts the release)

On a clean account, deploy all switches, then capture: a happy-path SUCCEEDED run, a DuplicateHold
run, the strict PHI canary (0 hits across Logs/X-Ray/DLQ/SFN history — R3-2 pass-by-reference keeps raw
+ masked content out of state, so this should PASS), a load run, and an exactly-once replay storm. Then tear down
and confirm zero residual. Record the results in `VALIDATED_RELEASE.md` and cut `v0.1.0-pilot-rc1`.

## 4. Teardown

```bash
cdk destroy --all -c env=pilot
```

The audit ledger + WORM vault + customer-managed CMK are **RETAIN'd** by design (the CMK alias deletes
with the stack — find the retained key by tag and schedule deletion). VPC-attached Lambda stacks take
~15–30 min to delete (Hyperplane ENI release).

## 4b. EP1 harness (turnkey)

Two scripts make the EP1 run turnkey:

```bash
python scripts/validate_deployment.py --env pilot --region us-east-1   # machine PASS/FAIL verdict
python scripts/pii_canary.py --prefix pv-pilot --execute --strict      # PHI telemetry canary (0 hits)
python scripts/validate_deployment.py --env pilot --expect-absent      # after teardown: 0 residual stacks
```

`validate_deployment` probes stacks/secret/masking-control/guards/ingest-pass-by-reference/workflow and
prints a JSON verdict (exit 0 = PASS). `pii_canary --strict` seeds a marked case through ingest → the
workflow and sweeps CloudWatch Logs, X-Ray, DLQs, and Step Functions history for the marker — with R3-2
pass-by-reference it should report **PASS** (0 hits everywhere).

## 5. Offline verification (no AWS)

```bash
python -m pytest tests/ -q                    # 109/109: control-plane + CDK synthesis + pass-by-ref + canary logic
python -m pytest tests/test_cdk_stacks.py -q  # 22 CDK assertions (synthesizes all 7 stacks)
```
