# Deployment Guide — Pharmacovigilance ICSR Assistant (AWS CDK)

*The authoritative step-by-step for the supported deployment path. CDK, at the validated release tag,
never `main`. The shell engine (`lib/engine/`) is a legacy internal reference and must not be used for
customer deployments.*

---

## 0. Supported path

```bash
git checkout v0.1.1-pilot-rc1                 # a validated release tag, never main
cd cdk && pip install -r requirements.txt     # PINNED: aws-cdk-lib==2.262.1, constructs==10.7.1
npx --yes aws-cdk@2 bootstrap aws://<account>/us-east-1     # once per account
```

> **Use `npx --yes`** (or install the CDK CLI globally). Without `--yes`, `npx aws-cdk@2` stops at an
> interactive "Ok to proceed?" install prompt — in a hidden or CI shell that simply hangs with no
> output and no error. Verified on a clean machine, 2026-07-28.

## 1. Deploy (full Gate-B posture)

```bash
npx --yes aws-cdk@2 deploy --all --require-approval never \
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
reviewer approves at the `waitForTaskToken` gate → finalize.

> **Known gap — exactly-once finalization is NOT implemented in this agent.**
> `lib/controls/finalize_signoff.py` performs no conditional-put commit gate. The sibling
> `edu_financial_aid_agent` and `Housing_eligibility_agent` implement an exactly-once `FINAL#` marker;
> that control was never ported here. Until it is, a retried Lambda, a replayed execution, or a second
> approval path can write a second COMMITTED record. For an ICSR workflow that is a double-reporting
> risk. See [`docs/MULTI-AGENT-COMPOSITION.md`](docs/MULTI-AGENT-COMPOSITION.md) for the analysis.

> **Zero-PHI note (R3-2):** call the **ingest-case** Lambda FIRST — it stores the raw `source` in the
> encrypted case store and returns the opaque `case_ref` you pass to the workflow. Raw content never
> enters Step Functions state, and the masked text is reached only server-side via the signed
> `sanitized_ref`, so the strict PHI canary can PASS.

## 3. The EP1 validation (what cuts the release)

On a clean account, deploy all switches, then capture: a happy-path run to the human gate, a
DuplicateHold run, and the strict PHI canary (0 hits across Logs/X-Ray/DLQ/SFN history — R3-2
pass-by-reference keeps raw + masked content out of state, so this should PASS). Then tear down and
confirm zero residual.

> **Not part of this capture set.** A prod-scale load run and an exactly-once replay storm are **not**
> performed here and no harness for them ships in this repository. Both are customer-side Gate-B exit
> items — see `docs/GATE-B-CHECKLIST.md` B6. Do not describe the release as load-tested.

Deploy the validation environment with **`retention_profile=sandbox-demo`**, *not* the `pilot` profile
shown in §1:

```bash
npx --yes aws-cdk@2 deploy --all --require-approval never \
  -c env=<env> -c retention_profile=sandbox-demo \
  -c kms=customer-managed -c network_mode=private -c identity_mode=pilot -c tenant=<sponsor-id>

python scripts/validate_deployment.py --env <env> --region us-east-1  # expect deployment_status: PASS
python scripts/pii_canary.py --prefix pv-<env> --execute --strict     # expect verdict: PASS, leaks: {}
```

> **Why `sandbox-demo` for a validation run.** `retention_profile=pilot` applies **90-day GOVERNANCE**
> Object Lock to the WORM vault — correct for a real pilot, but on a throwaway environment you intend to
> destroy the same day it leaves locked objects you cannot clear (the audit writer is deliberately DENIED
> `s3:BypassGovernanceRetention`). `sandbox-demo` is GOVERNANCE / 1 day.

> **Both scripts run for minutes and print nothing until they finish — that is not a hang.** The
> validator polls the Step Functions execution (~2–3 min); the canary waits 120s for telemetry to settle
> before sweeping (~3 min). Redirected to a file, Python buffers, so the log stays 0 bytes until exit.

> **Deploy time.** ~19 min for all 7 stacks (measured, `pv-val2`, 2026-07-28). PV is slower than the
> sibling agents because `network_mode=private` provisions **AWS Network Firewall** for the
> `.api.fda.gov` egress allowlist — PV genuinely reaches an external API, so it cannot use the
> zero-egress design.

## 4. Teardown

```bash
# Stop executions parked at the human sign-off gate FIRST — a RUNNING execution blocks
# deletion of the state machine and the destroy stalls.
aws stepfunctions list-executions --state-machine-arn <arn> --status-filter RUNNING \
  --query "executions[].executionArn" --output text | xargs -n1 -I{} \
  aws stepfunctions stop-execution --execution-arn {} --cause "teardown"

npx --yes aws-cdk@2 destroy --all --force -c env=pilot -c retention_profile=pilot
```

The audit ledger + WORM vault + customer-managed CMK are **RETAIN'd** by design (the CMK alias deletes
with the stack — find the retained key by tag and schedule deletion). VPC-attached Lambda stacks take
~15–30 min to delete (Hyperplane ENI release).

### Completing a zero-residual teardown (validation environments only)

`cdk destroy` alone does **not** reach zero — it retains the evidence resources on purpose. On a
throwaway validation environment, clear them explicitly (verified on `pv-val2`, 2026-07-28):

```bash
E=val2
aws dynamodb    delete-table     --table-name pv-$E-audit-ledger
aws cognito-idp delete-user-pool --user-pool-id "$(aws cognito-idp list-user-pools --max-results 50 \
                   --query "UserPools[?contains(Name,'pv-$E')].Id" --output text)"
# PV leaves TWO provider log groups, not one: the AgentCore attachment provider AND the
# network stack's custom-resource provider (the firewall-endpoint lookup). Delete both.
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/pv-$E-" \
  --query 'logGroups[].logGroupName' --output text | tr '\t' '\n' \
  | xargs -n1 -I{} aws logs delete-log-group --log-group-name {}
aws s3api       delete-bucket    --bucket "$(aws s3api list-buckets \
                   --query "Buckets[?contains(Name,'pv-$E-data-wormvault')].Name" --output text)"
```

Then sweep every resource type, not just stacks — `describe-stacks` returning empty is **not** proof of
zero residual.

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
python -m pytest tests/ -q                    # 111 pass locally (+1 CI-only gate = 112): control-plane + CDK synthesis + pass-by-ref + canary logic
python -m pytest tests/test_cdk_stacks.py -q  # 23 CDK assertions (synthesizes all 7 stacks)
```
