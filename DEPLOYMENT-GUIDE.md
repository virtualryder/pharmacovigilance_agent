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

> **Gate-B note:** PV is not yet pass-by-reference — the raw `source` transits Step Functions state
> until masking. The strict PII canary will flag pre-mask content; add an ingest/case-store step to
> reach zero-PII telemetry before a real-data pilot (`PV-PILOT-READINESS-PLAN.md`).

## 3. The EP1 validation (what cuts the release)

On a clean account, deploy all switches, then capture: a happy-path SUCCEEDED run, a DuplicateHold
run, the strict PHI canary (0 hits across Logs/X-Ray/DLQ/SFN history — expected to flag pre-mask
content until the case-store follow-on), a load run, and an exactly-once replay storm. Then tear down
and confirm zero residual. Record the results in `VALIDATED_RELEASE.md` and cut `v0.1.0-pilot-rc1`.

## 4. Teardown

```bash
cdk destroy --all -c env=pilot
```

The audit ledger + WORM vault + customer-managed CMK are **RETAIN'd** by design (the CMK alias deletes
with the stack — find the retained key by tag and schedule deletion). VPC-attached Lambda stacks take
~15–30 min to delete (Hyperplane ENI release).

## 5. Offline verification (no AWS)

```bash
python -m pytest tests/ -q                    # 95/95: control-plane + CDK synthesis
python -m pytest tests/test_cdk_stacks.py -q  # 22 CDK assertions (synthesizes all 7 stacks)
```
