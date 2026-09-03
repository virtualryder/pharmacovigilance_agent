# Phase 111 — consolidated post-SaaS validation gate on the pharmacovigilance pack at governed-core **1.9.0** (2026-09-03) — **PASS**

**What this is.** ONE from-zero multi-tenant deployment of the PV pack after its re-pin from governed-core
1.5.0 to **1.9.0** (GAP-1 of the 2026-09-03 platform review): env `pv-mt` — 8 CDK stacks
(`-c env=mt -c retention_profile=sandbox-demo -c tenants=sp-a,sp-b -c model_logging=1 -c budget_usd=5`),
two tenants `sp-a` / `sp-b`, Bedrock model-invocation logging on, the AgentCore Runtime
(`pv_runtime_agent`, Strands, `MULTITENANT=1`) launched from the toolkit — on which the three 111 proofs
ran back to back, followed by the kill-switch gate, the budget gate and an end-to-end regression sweep for
unexpected errors; then torn down. Product tree: commit `8db172f` (offline port, "governed-core 1.9.0
parity with benefits") plus the fixes listed under *Run history* below, committed together as the tag
`v0.3.0-pilot-rc1`. The proofs are the benefits pack's harnesses ported verbatim with the PV workflow's
shape (14 Lambda-backed states, `DraftNarrative`, `pv_reviewer` group, `/pv-mt-pharmacovigilance/` SSM root).

## Verdict (run 4, 22:40–22:49 UTC)

| gate step | result | detail |
|---|---|---|
| 1. Isolation + per-tenant audit routing (`scripts/mt_two_tenant_proof.py`) | **PASS 12/12** in 89.3 s | cw-a / cw-b allowed (9 tools listed, `mask_pii` executed) and routed only to their own sanitized store, ledger and WORM vault (base 0 writes); cw-none 0 tools + 403; `ingest_case` refuses without a verified token; the workflow hop with the signed pair reached `HumanSignoff` writing INTENT evidence + a pending approval to sp-a only; the same execution without the pair FAILED at `Extract` |
| 2. Full transparency through the real AgentCore Runtime (`scripts/obs_two_tenant_proof.py`) | **PASS 13/13 per tenant** in 260.5 s | sp-a: 1 agent / 14 model / 14 tool spans, 38 gateway rows, 8 Lambda `aegis.call` lines (7 joined to the WORM record), 8 model invocations (all tenant-tagged, 7 joined to spans, masked-before-model True); sp-b: 1 / 10 / 14 spans, 38 gateway rows, 8 calls, 6 model invocations (masked True); other tenant's ledger empty for both; 0 cross-tenant WORM rows |
| 3. Strict PII telemetry canary, workflow path (`scripts/pii_canary.py --strict`) | **PASS** in 164.2 s | marker `CANARY-53211DA1E54F-TELEMETRYPROBE`: 0 hits in CloudWatch Logs (all `/aws/lambda/pv-mt-*` + the gateway request log + `/aws/states/pv-mt-icsr-workflow`), X-Ray, DLQs and Step Functions history; the model-invocation log is swept and reported (0 hits this run) but not gated — see run 2 |
| 4. Kill switch on the AgentCore path (`scripts/kill_switch_proof.py`) | **PASS 29/29**, time-to-effect 10 s | re-run on the fixed tree after step 2; record `AGENTCORE-KILL-SWITCH-2026-09-03.md` |
| 5. Per-tenant token + USD budget (`scripts/budget_proof.py`) | **PASS 24/24** | re-run on the fixed tree after step 4; record `AGENTCORE-BUDGET-2026-09-03.md` |
| 6. End-to-end regression sweep (`scripts/e2e_regression.py`) | **PASS — 0 unexpected** | window = everything since the fixed `pv-mt-compute` deploy; 24 log groups swept for ERROR / Traceback / timeout / exception shapes; Lambda `Errors` metric 0 on every function except the 2 deliberate fail-closed `TenantError` refusals on `intake-icsr` provoked by the kill-switch proof (classified); DLQs empty; no alarm in ALARM except the deliberate budget 60/85 alarms; every execution's terminal state explained. Record `AGENTCORE-111-GATE-2026-09-03-regression.json` |

Raw records: `AGENTCORE-111-GATE-2026-09-03.json` (the gate driver: commands, exit codes, seconds, git state),
`AGENTCORE-111-GATE-2026-09-03-sp-a.md` / `-sp-b.md` (the per-tenant correlated timelines from
`trace_case`). Account ids redacted to `111122223333`.

## Run history — what the gate found

| Run (UTC) | Result | What it found |
|---|---|---|
| 1 (21:40) | mt 9/12, obs PASS, canary PASS | **Harness.** The ported MT proof started the workflow with the benefits execution input; the PV controller's `DetectDuplicate` state reads `$.case_key` / `$.known_keys` and failed on the missing path (`JSONPath '$.case_key' … could not be found`). The proof, the kill-switch proof and the budget proof now start PV executions with `case_key` + `known_keys`. Product unchanged. |
| 2 (21:51) | mt 12/12, obs PASS, canary **FAIL** (1 hit) | **Harness / measurement.** The strict canary had the Bedrock model-invocation log in its gated set (a benefits-era addition). The synthetic marker token reached the model on this run un-masked: Comprehend `DetectPiiEntities` does not reliably classify `CANARY-…-TELEMETRYPROBE` as a NAME (it did on run 1 and did not on run 2). That is a property of the probe, not a pass-by-reference leak — the *model-path* control is `masked_before_model` in `trace_case`, measured with realistic PII, and it was True for every model invocation of every run. `pii_canary.py` now takes `--info-log-group`: the model-invocation log is swept and **reported**, never gated (`informational_model_path` in the canary JSON). Product unchanged. |
| 3 (22:05) | **PASS** 12/12 + 13/13×2 + canary | First green 111 gate. Kill switch 29/29 (22:16) and budget 24/24 (22:29) followed. The regression sweep that followed *them* found the real product bug below. |
| — sweep after run 3 | 36 → 49 unexpected | **Product bug (agent path only).** `assess_seriousness` crashed with `AttributeError: 'str' object has no attribute 'get'` at `_detect(e, text)` on every call from the Runtime agent, and the gateway surfaced each as an `isError` row. Cause: the manifest declares `flags` as a **string** ("Optional JSON of explicit seriousness booleans") — the agent obeys the schema and sends `'{"hospitalization": true}'` — while the tool assumed a dict (the offline harness passes a dict, so the unit tests were green). The workflow path never hit it (the controller passes no `flags`), which is why runs 1–3 and the EP1/EP2 validations passed: the failure was invisible everywhere except the agent path, and there the agent tolerated it and carried on. Fail-closed, but wrong. Fix: `_coerce_flags()` — a JSON string is parsed, anything that is not a JSON object means "no explicit flags" and the text scan decides; regression test `test_assess_flags_as_json_string_matches_manifest_schema` (string flags, explicit-false override, six malformed shapes). Redeployed `pv-mt-compute --exclusively` at 22:39. |
| 4 (22:40) | **PASS** — this record | Same 12/12 + 13/13×2 + canary on the fixed tree; the sp-a / sp-b timelines show `assess_seriousness -> ok` on the agent path with 0 `isError` gateway rows. Kill switch (29/29) and budget (24/24) re-run on the fixed tree; final sweep 0 unexpected. |

Two lessons worth keeping. First, the **e2e sweep is not optional after a green gate** — three green
proofs and two green EP validations had never exercised the agent-path call shape of one tool; only the
"zero error-shaped log lines" sweep did. Second, the sweep now scopes Step Functions executions to the same
`--since-minutes` window as the log groups (an execution that started before a fix was deployed is reported
as `outside_window` and not counted), so the verdict is about the code that is deployed now.

## Teardown

Runtime `pv_runtime_agent-xZccfX957W` deleted; `cdk destroy --all` for `mt` (all 8 stacks); retained
ledgers / vaults swept by `scripts/cleanup_retained.py --prefix pv-mt`; CloudTrail bucket, log groups,
alarms, the AWS Budgets ceiling and the toolkit-created runtime role's inline `agent-runtime-ssm` policy
removed; the account's previous model-invocation logging configuration re-applied and verified. Residual
by design: none (sandbox-demo retention profile).
