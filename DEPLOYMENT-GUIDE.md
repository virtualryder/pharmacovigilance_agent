# Deployment Guide — Pharmacovigilance ICSR Assistant (AWS CDK)

*The authoritative step-by-step for the supported deployment path. CDK, at the validated release tag,
never `main`. The shell engine (`lib/engine/`) is a legacy internal reference and must not be used for
customer deployments.*

---

## 0. Supported path

```bash
git checkout v0.3.0-pilot-rc1                 # a validated release tag, never main
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
| `guardrail_id=<id>` `guardrail_version=<v>` | Arms the platform Bedrock guardrail on the drafter (`draft_narrative`). Every generation is guardrail-assessed; an intervention fails closed (no `narrative_ref`) and the case routes to `ManualReview`. Omit → unguarded (sandbox only). |
| `approvals_client_id=<cognito-client-id>` | Client id the `approve-signoff` Lambda verifies reviewer access tokens against (identity pool + `pv_reviewer` group wired from the identity stack). |

### 1b. Hybrid multi-tenant + full transparency switches (governed-core ≥ 1.7.1; live-gated on PV 2026-09-03)

| Switch | Effect |
|---|---|
| `tenants=<a>,<b>,…` | **Hybrid multi-tenant**: one shared control plane (identity, compute, workflow, gateway + Cedar engine) and ONE physically separate data stack per tenant (`pv-<env>-<tenant>-data`: tenant-scoped case store, sanitized store, ledger, approvals + the tenant's own Object-Lock vault `<prefix>-<tenant>-worm-<account>`). Creates a `tenant_<id>` Cognito group per tenant, deploys the gateway REQUEST interceptor (`tenant-interceptor`), attaches `require_tenant` (Cedar, `policies/require_tenant.cedar`), sets `MULTITENANT=1` + `WORM_BUCKET_TEMPLATE` on the governed Lambdas, threads the signed tenant pair through the workflow, and mirrors least-privilege grants onto `<prefix>-*-<logical>`. Mutually exclusive in spirit with `tenant=` (silo). |
| `model_logging=1` | **Bedrock model-invocation logging** for the account+region (an account-level singleton — it REPLACES any existing configuration, hence opt-in; record the previous configuration first and restore it at teardown): CloudWatch group `/aws/bedrock/modelinvocations/<prefix>` + S3 large-data bucket + the `bedrock.amazonaws.com` role. Also delivers the AgentCore gateway's vended request logs to `/aws/vendedlogs/bedrock-agentcore/gateway/<prefix>`. |
| `runtime_role=<toolkit-created role name>` | names the AgentCore Runtime execution role (created by `lib/runtime/_launch.sh`) so the observability stack can grant it the budget meter + metrics and the AWS Budgets action can deny it Bedrock at the USD ceiling. Deploy the observability stack a second time with it after the Runtime launch. |

Multi-tenant contracts: the tenant is **derived, never requested** (verified identity → interceptor →
HMAC-signed `__aegis_tenant` / `__aegis_tenant_sig` → every Lambda verifies before routing);
`ingest-case` (direct IAM invocation) derives it from a verified reviewer access token (`access_token` in
the payload) and returns `tenant_binding`, which the workflow starter MUST splat into the execution input
(`{case_id, requester, case_ref, drug, case_key, known_keys, **tenant_binding}`) — an execution without
it fails at `Extract`. Proofs: `scripts/mt_two_tenant_proof.py` (cross-tenant deny, per-tenant routing,
audit / WORM / approvals routing on both hops) and `scripts/obs_two_tenant_proof.py` +
`scripts/trace_case.py` (one per-case timeline across Runtime spans, gateway rows, Lambda `aegis.call`
lines, model-invocation rows and the WORM record; `masked_before_model` per model call). Consolidated
gate: `scripts/gate_111.py` (both proofs + the strict PII canary, one JSON). Evidence:
`evidence/AGENTCORE-111-GATE-2026-09-03.md`.

### 1c. Kill Switch — one-command containment (governed-core ≥ 1.8.0; live-gated on PV 2026-09-03)

Every deployment gets ONE SSM Parameter Store flag, `/pv-<env>-pharmacovigilance/kill-switch`, that every
component on the agent path reads **first** — before tenancy, before Cedar, before masking, before the
human sign-off gate. Containment precedes evaluation. When engaged: the gateway REQUEST interceptor answers
`tools/list` + `tools/call` with a 403 JSON-RPC error and the target Lambda is never invoked (a `DENIED
kill_switch.deny` record + WORM object land in the acting tenant's ledger / vault); every governed tool
Lambda raises `KillSwitchEngaged` before its handler runs, so a Step Functions execution FAILS at its
next state; the AgentCore Runtime refuses a new invocation before the tenant is derived and stops a running
session at its next model call (`stopped: mid-session`, `guardrail_action: KILL_SWITCH`). Fail-closed (an
unreadable or malformed parameter counts as engaged); 15 s TTL cache per execution environment;
`-c global_kill_switch=/aegis/kill-switch` makes the pack honour the platform-wide flag too.

**Engage / disengage** are two Lambda function URLs with `AuthType: AWS_IAM` (stack outputs
`KillSwitchEngageUrl` / `KillSwitchDisengageUrl`), one managed policy each (`pv-<env>-killswitch-engage` /
`-disengage`) — assign them to **different** roles. The recorded actor is the IAM-verified caller, never a
body field, and the engaging identity cannot release its own engagement (the refusal is itself a `DENIED`
record). Nothing else in the app holds `ssm:PutParameter` on the flag.

```bash
awscurl --service lambda --region us-east-1 -X POST -d '{"reason":"SEV-1: runaway agent"}' "$KILL_SWITCH_ENGAGE_URL"
awscurl --service lambda --region us-east-1 "$KILL_SWITCH_ENGAGE_URL"                      # status
awscurl --service lambda --region us-east-1 -X POST -d '{"reason":"safety lead sign-off"}' "$KILL_SWITCH_DISENGAGE_URL"   # a DIFFERENT identity
```

Live gate: `scripts/kill_switch_proof.py` (29 checks) — `evidence/AGENTCORE-KILL-SWITCH-2026-09-03.md`
(10 s to effect at the gateway). Runbook: platform `docs/ops/KILL-SWITCH.md`.

### 1d. Per-tenant token budget + USD ceiling (governed-core ≥ 1.9.0; live-gated on PV 2026-09-03)

One DynamoDB meter per deployment (`pv-<env>-budgets`, key `<tenant>#<YYYY-MM>`). Before **every** model
call the Runtime makes one conditional reservation against the tenant's cap and after it commits the real
Converse `usage`; the gateway interceptor refuses a tenant at/over its cap on every `tools/call` (403 +
DENIED WORM record); the drafter's (`draft_narrative`) own Bedrock call is metered the same way — a refusal
routes the workflow to `ManualReview` and lands a DENIED record joined by the execution ARN, and its
`Converse` is tagged with `requestMetadata` {tenant, component, trace / execution ids; never a case id}
so the model log reconciles per tenant. Hard caps fail closed (an unreadable meter denies).

| Switch / knob | Effect |
|---|---|
| manifest `budget: monthly_token_cap / cap_behavior` | the deployment default cap (one place to set the number); read by the CDK and the Runtime launch |
| `-c budget_usd=<dollars>` | per-tenant USD cap (from the pinned price table) **and** an AWS Budgets monthly ceiling on Amazon Bedrock with an `APPLY_IAM_POLICY` action (deny `bedrock:InvokeModel*` on the drafter + `-c runtime_role=` roles) whose notification subscriber (`pv-<env>-budget-breach`) engages the kill switch |
| `-c budget_behavior=soft` | flag-only for the whole deployment |
| `PutItem <tenant>#<YYYY-MM> {cap_tokens \| cap_usd_micro \| behavior}` | per-tenant override with no redeploy; `cap_tokens 0` switches a tenant off |
| `lib/model_prices.json` | the pinned price table; its `price_version` is recorded on every commit — confirm against the Bedrock pricing page per region before production |

Alarms `Aegis/Budget` `TokensUsedPct` / `UsdUsedPct` per tenant at 60 / 85 / 100 %. AWS Budgets is **not**
real-time (updated up to three times a day) — it is the backstop; the meter is the real-time guard. Live
gate: `scripts/budget_proof.py` (24 checks) — `evidence/AGENTCORE-BUDGET-2026-09-03.md`. Design + status:
platform `docs/TOKEN-BUDGETS-AND-COST-CEILINGS.md`.

### After any gate: the regression sweep

`scripts/e2e_regression.py --env <env> --since-minutes <n> --runtime-log-group <group>` sweeps every
`/aws/lambda/pv-<env>-*` group, the Step Functions log, the gateway's vended log and the Runtime log for
error-shaped lines, the Lambda `Errors` metric, DLQs, alarms and every execution's terminal state (scoped to
the same window), and exits non-zero on anything it cannot classify as a deliberate refusal. It is what found
the one product bug of the 2026-09-03 gate (a tool crashing on the agent path's call shape while every
proof was green) — run it after every green gate.

### Observability & governance evidence (parity with the benefits baseline, governed-core ≥ 1.5.0)

IaC on every deploy — no post-deploy instrumentation: **X-Ray** `Tracing.ACTIVE` on every governed
tool + the gateway; **Step Functions** execution logging (`ALL`, `includeExecutionData=false`) into a
1-year CMK-when-present group at `/aws/states/<prefix>-icsr-workflow`; unconditional 1-year Lambda log
retention; a **data-only CloudTrail** on the WORM vault (`<prefix>-worm-data-events`) alongside the
platform evidence trail; and the **`AUDIT_BUCKET`** alias the pinned evidence writer needs (without it
the WORM mirror silently no-ops). Approvals go through **`approve-signoff`** (Cognito access-token,
SoD, single-use); `finalize` verifies the approval path and refuses a token released around it
(fail-closed to `ManualReview`, recorded `DENIED`). Account-level **Bedrock model-invocation logging**
(a platform runbook step) captures de-identified prompts/responses. One run → four independent
captures of each action.

## 2. Run a case (execution-input contract)

Start the controller (`pv-pilot-icsr-workflow`) with:
`{case_id, requester, source, drug, case_key, known_keys}` — `source` is the raw adverse-event text,
`drug` the suspect product. The pipeline: extract → openFDA background → mask → seriousness → duplicate
check → (DuplicateHold if a duplicate) → draft narrative → INTENT audit → a **different** qualified
reviewer approves at the `waitForTaskToken` gate → finalize (exactly-once `FINAL#` marker).

> **Exactly-once finalization.** `lib/controls/finalize_signoff.py` writes a conditional-put `FINAL#`
> marker as the single commit gate. A retried Lambda, a replayed execution, or a second approval path
> finds the marker and returns the ORIGINAL submission id, writing no second COMMITTED record — so the
> same case cannot be reported to a regulator twice. Covered by `tests/test_exactly_once_finalize.py`.
> Ported from the EDU/Housing agents on 2026-08-03 after a parity check found it missing here; see
> [`docs/MULTI-AGENT-COMPOSITION.md`](docs/MULTI-AGENT-COMPOSITION.md).

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
python -m pytest tests/test_cdk_stacks.py -q  # 28 CDK assertions (synthesizes all 7 stacks)
```
