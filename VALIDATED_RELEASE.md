# Release Validation-Run Record (engineering evidence; not a CSV validation package)

*Single source of truth for the current release tag is the repo-root `RELEASE` file, enforced by
`tests/test_release_consistency.py`. Authoritative counts + limitations: `RELEASE-MANIFEST.md`.*

## Current release — `v0.4.0-pilot-rc1` (2026-09-05)

| Field | Value |
|---|---|
| Tag | `v0.4.0-pilot-rc1` — single source of truth: `RELEASE`. Cut from main on 2026-09-05. `v0.3.0-pilot-rc1` (2026-09-03) is the tree the consolidated 111 gate, the kill-switch gate, the budget gate and a 0-unexpected regression sweep passed on live (env `pv-mt`, 2 tenants, real AgentCore Runtime). **`v0.4.0-pilot-rc1` supersedes it**: the same tree with `lib/` relocked at the pinned **governed-core 1.10.1** (fault-semantics batch — durable-evidence gating, un-strandable approvals, authoritative Cedar context — plus deepened PII/PHI detection); **offline-gated** (all pass locally except one CI-only gate; 275 collected). The last LIVE gate on this pack ran on governed-core 1.9.0 (2026-09-03, env `pv-mt`, two tenants, real AgentCore Runtime); a live re-gate on 1.10.1 is the next milestone for this pack (third external review, REL-2). |
| Commit SHA | `git rev-list -n1 v0.4.0-pilot-rc1` |
| Test count at the tag | **207** offline tests <!-- count-gate:historical --> at `v0.4.0-pilot-rc1` (31 CDK <!-- count-gate:historical --> assertions) — a record of that tag, not the current tree. Current main (PAR-1 port of the 2026-09-05 controls, 2026-09-06): **270** collected (263 pass + 1 CI-only skip locally; 48 CDK assertions) |
| Governance core | `governed-core` **1.10.1**, pinned by URL + sha256 (`requirements-core.txt`, `--require-hashes`); `lib/core.lock` locked at 1.10.1 (`lib/verify_core.py` OK) — 1.9.0 is the last core this pack was LIVE-gated on; `lib/runtime/` byte-identical with the benefits pack (shared runtime) |
| What this tag adds over `v0.2.0-pilot-rc1` (core 1.5.0) | the benefits pack's 1.5.0 → 1.9.0 deltas, live-gated on PV: **hybrid multi-tenant routing** (per-tenant sanitized store, ledger, WORM vault; HMAC-signed tenant pair on every hop; `ingest_case` as the token-verified ingestion boundary) — `evidence/AGENTCORE-111-GATE-2026-09-03.md` step 1, **12/12**; **one correlation set through every hop** (Runtime spans ↔ gateway rows ↔ Lambda `aegis.call` ↔ WORM ↔ model-invocation log, masked-before-model True) — step 2, **13/13 per tenant**; **strict PII telemetry canary** — step 3, 0 hits; **kill switch on the AgentCore path** — `evidence/AGENTCORE-KILL-SWITCH-2026-09-03.md`, **29/29**, 10 s to effect; **per-tenant token + USD budget** — `evidence/AGENTCORE-BUDGET-2026-09-03.md`, **24/24**; **0-unexpected regression sweep** — `evidence/AGENTCORE-111-GATE-2026-09-03-regression.json`. Product fix found by the sweep: `assess_seriousness` crashed on the agent path because the manifest types `flags` as a JSON string (see the gate record's run history). |
| Carried unchanged from `v0.2.0-pilot-rc1` | the control plane EP1/EP2 proved (signed `sanitized_ref`, pass-by-reference both directions, deterministic controller + guards, DuplicateHold, exactly-once finalize, duplicate-submission protection) — every one of those states ran again inside the 2026-09-03 workflow-hop proofs. |
| Not re-run on this tree | the EP1 Gate-B posture walk (zero-egress private networking, CMK, MFA identity) — last captured 2026-07-27/28 on `v0.1.1-pilot-rc1`; the 2026-09-03 environment used the `sandbox-demo` retention profile with default networking. Scheduled with the platform's GAP-5 re-walk. Controls wired on main after the tag by the PAR-1 port (2026-09-06) and **offline-gated only on this pack** (CDK synthesis assertions + a full-switch `cdk synth`; live-proven so far only on the benefits pack): account capture trail + lineage (`-c capture_all=1`), Bedrock enforcement perimeter (bypass alarm, VPC-endpoint policy, pinned drafter role), restore-aware model-invocation logging, regulated-data invocation-log store (`-c model_log_lock_days`), ssm/monitoring/cognito-idp private endpoints, CMK log-group families, the output guardrail as IaC (PROMPT_ATTACK + PII ANONYMIZE + contextual grounding, pinned version) and the grounded narrative drafter (case + deterministic seriousness assessment as the grounding source; fail-closed on ANY intervention). Their live re-gate on this pack is REL-5. |

## EP1 record — `v0.1.1-pilot-rc1` (2026-07-27)

| Field | Value |
|---|---|
| Tag | `v0.1.1-pilot-rc1` — cut after the live EP1 validation below (historical record; the current tag is above). |
| Commit SHA | the commit carrying tag `v0.1.1-pilot-rc1` (`git rev-list -n1 v0.1.1-pilot-rc1`) |
| Test count at that tag | **112** offline (control-plane + CDK synthesis + CI-completeness gates). Authoritative matrix: [`RELEASE-MANIFEST.md`](RELEASE-MANIFEST.md) |
| Validation date | **2026-07-27** (live EP1, env `pv-val1`, us-east-1) |
| Region | us-east-1 |
| Deployment | AWS CDK `deploy --all`, all Gate-B switches: `network_mode=private kms=customer-managed identity_mode=pilot tenant=pv-example-sponsor retention_profile=sandbox-demo` |
| Evidence | **captured — [`evidence/EP1-VALIDATION.md`](evidence/EP1-VALIDATION.md)**: 7/7 stacks CREATE_COMPLETE incl. AgentCore ENFORCE attachment; `validate_deployment.py` → PASS; happy-path ran the full guarded controller to the human sign-off gate; DuplicateHold terminal; **strict PHI canary PASS (0 leaks across Logs / X-Ray / DLQ / Step Functions history)**; MFA pool ON with 0 users. Then torn down (`destroy --all`) with a residual sweep. Account IDs redacted to `111122223333`. |

## What EP1 proved (live)

The deployed control plane behaves as designed on a clean account with every Gate-B switch on: the
deterministic Step Functions controller runs each guard in order and **cannot** advance a case on
unverified state; de-identification is proven by a mask-signed `sanitized_ref` (a forged ref is refused);
raw content enters only via `ingest-case` and **only opaque refs — including the drafted narrative — cross
Step Functions state** (strict PHI canary PASS); a duplicate holds instead of being re-reported; and every
consequential action pauses at a human sign-off gate.

Two issues were found and fixed during the run (a Windows-only harness encoding false-FAIL, and a real
R3-2 gap where the CIOMS narrative text crossed execution state — now stored server-side under a ref).
Both are detailed in `evidence/EP1-VALIDATION.md`; the strict canary passes only because the second was
fixed and re-validated.

## Still not live-validated (say these out loud)

Enterprise IdP federation round-trip; QPPV / drug-safety SME sign-off on the seriousness rules, reporting
clocks, and CIOMS language; independent security testing / pen test; prod-scale load. These are Gate-C/D
items — see `PV-PILOT-READINESS-PLAN.md`. Evidence to date is author-produced on synthetic data.
