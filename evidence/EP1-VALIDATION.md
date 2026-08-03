# EP1 — Live Clean-Account Validation

> ## Re-validation — `pv-val2`, 2026-07-28 (supersedes the EP1 run below)
>
> `DEPLOYMENT-GUIDE.md` was re-walked end to end as a Solution Architect would, from the hardened code
> (post `5833f98`), on a clean account. **Every gate passed.**
>
> | Check | Result |
> |---|---|
> | 7/7 CDK stacks (all Gate-B switches) | `CREATE_COMPLETE`, **1138s (~19 min)** |
> | AgentCore Gateway + Cedar **ENFORCE** | attached as IaC, no post-deploy shell step |
> | `validate_deployment.py --env val2` | **PASS** — `masking_control`, `guard_genuine`, `forged_ref_denied`, `ingest_pass_by_reference` all PASS |
> | Happy path | ran the guarded controller and paused at the human sign-off gate |
> | Strict PHI canary | **PASS**, `leaks: {}` (marker `CANARY-D17DA3CDAAAF-…`) |
> | Identity | MFA `ON`, **0 users**, admin-create-only `True` |
> | Egress posture | **1 Network Firewall · 2 NAT gateways · 11 VPC endpoints** — the `.api.fda.gov` allowlist, measured |
>
> **Two EP1 defects confirmed fixed on a clean deploy:**
> 1. **`guard_genuine` returned PASS**, not the false FAIL seen in EP1. That failure was a Windows
>    CP1252 read corrupting the em-dash in the signed `source` label before re-passing it to the guard;
>    the UTF-8 read/write helpers hold.
> 2. **The strict PHI canary found 0 leaks.** EP1 caught a *real* defect here — the CIOMS narrative text
>    crossed Step Functions state from `DraftNarrative` onward. The pass-by-reference fix
>    (`narrative_ref`) is now verified end to end on a fresh deployment, not just by regression test.
>
> **Runbook defects found and fixed in this pass:** `npx aws-cdk@2` hangs on an install prompt without
> `--yes`; §3 didn't give the validation deploy switches, so an SA inherited `retention_profile=pilot`
> (90-day Object Lock) on a throwaway environment; teardown didn't say to stop executions parked at the
> human gate first; `cdk destroy` alone does not reach zero residual and no commands were given for the
> retained resources; and nothing warned that the validator and canary buffer output for minutes.
>
> Account IDs redacted to `111122223333`. Torn down with a full residual sweep.

---
 (Pharmacovigilance ICSR Assistant)

**Environment:** `pv-val1` · **Region:** us-east-1 · **Account:** `111122223333` (redacted) ·
**Date:** 2026-07-27 · **Switches:** `network_mode=private kms=customer-managed identity_mode=pilot
tenant=pv-example-sponsor retention_profile=sandbox-demo` (all Gate-B switches ON).

This is the live EP1 run behind `v0.1.0-pilot-rc1`. All seven CDK stacks deployed to a clean account,
evidence was captured, and the environment was **torn down** with a residual sweep (below). Account IDs
in ARNs are redacted to `111122223333`.

## Deployment

All 7 stacks reached `CREATE_COMPLETE`, including the private network (Network Firewall egress allowlist
= `.api.fda.gov` only), customer-managed KMS across data/secrets/logs, the MFA-enforced identity pool,
and the **AgentCore/Gateway/Cedar ENFORCE attachment** (custom resource) — the highest-risk step —
completed cleanly.

```
pv-val1-identity        CREATE_COMPLETE
pv-val1-network         CREATE_COMPLETE   (EgressFirewall READY; app subnets routed via firewall endpoints)
pv-val1-data            CREATE_COMPLETE
pv-val1-compute         CREATE_COMPLETE
pv-val1-gateway         CREATE_COMPLETE   (AgentCoreAttachment/Default custom resource = ENFORCE)
pv-val1-workflow        CREATE_COMPLETE
pv-val1-observability   CREATE_COMPLETE   (DashboardName = pv-val1-operations)
```

## Turnkey validator — `scripts/validate_deployment.py --env val1` → **PASS**

```json
{
 "release": "dev", "env": "val1",
 "stacks": "COMPLETE",
 "secret": "PRESENT",
 "masking_control": "PASS",
 "guard_genuine": "PASS",
 "forged_ref_denied": "PASS",
 "ingest_pass_by_reference": "PASS",
 "workflow": "PASS:RUNNING(awaiting human gate)",
 "deployment_status": "PASS"
}
```

- **masking_control** — `mask-pii` masks a probe SSN and mints an authoritative signed `sanitized_ref`.
- **guard_genuine** — the deployed `workflow-guards` VERIFIES a genuine mask-signed ref (`ok:true`).
- **forged_ref_denied** — a ref with a tampered signature is REFUSED (`ok:false`) — proof-of-masking holds.
- **ingest_pass_by_reference** — raw content enters only via `ingest-case`; the execution starts with an
  opaque `case-…` ref (R3-2).
- **workflow** — a full pass-by-reference execution ran every deterministic guard and **paused at the
  human sign-off gate** (expected happy-path terminal for an assistant that never self-submits).

## Deterministic controller — live executions

**Happy path** (unique case, no known duplicate) ran the full guarded controller and paused at the human
gate:

```
Extract → GuardExtracted → LookupBackground → GuardBackground → MaskPii → GuardDeidentified →
AssessSeriousness → GuardRulesExecuted → DetectDuplicate → GuardDuplicate → DraftNarrative →
AuditIntent → HumanSignoff   [status: RUNNING — awaiting a qualified human at waitForTaskToken]
```

**DuplicateHold** (case_key present in known_keys) branched at the duplicate guard to the terminal hold —
it never reached draft or sign-off (no double-reporting to the regulator):

```
Extract → … → DetectDuplicate → GuardDuplicate → DuplicateHold   [status: SUCCEEDED — terminal hold]
```

## Strict PHI-telemetry canary — `scripts/pii_canary.py --prefix pv-val1 --execute --strict` → **PASS**

A globally-unique fake-PHI marker was run through the deployed pipeline, then every telemetry destination
was swept. **Zero hits** where the marker must not appear:

```json
{ "verdict": "PASS", "leaks": {}, "marker": "CANARY-…-TELEMETRYPROBE", "prefix": "pv-val1" }
```

Swept clean: CloudWatch Logs (`/aws/lambda/pv-val1-*`), X-Ray traces, SQS DLQs, **and Step Functions
execution history** — with R3-2 pass-by-reference in both directions the execution carries only opaque
refs, so even a redaction gap does not surface content in telemetry. (See "Findings" — this strict PASS
required a fix uncovered by this very run.)

## Identity posture (Gate-B)

Cognito user pool `MfaConfiguration = ON`, software-token MFA enabled, **0 users** (admin-create-only; no
default/self-signup identities). OIDC IdP federation is present as IaC; an enterprise IdP round-trip is a
customer-side Gate-C item.

## Load / exactly-once

**Not covered by this run.** Concurrency and replay-storm behaviour **under load** were not exercised
and no load harness ships in this repository. That remains a customer-side Gate-B exit item.

**Note on exactly-once finalization.** At the time of this validation run the `FINAL#` conditional-put
commit gate was **not implemented in this agent**, despite the IaC comment, deployment guide and this
file describing it as present. A control-plane parity check on 2026-08-03 found it, and the control was
ported from `edu_financial_aid_agent` and covered by `tests/test_exactly_once_finalize.py` (4 tests,
verified to fail when the gate is disabled). **This run therefore predates the control.** See
[`../docs/MULTI-AGENT-COMPOSITION.md`](../docs/MULTI-AGENT-COMPOSITION.md).

> **RESOLVED 2026-08-03 — the re-run this note asked for has happened.** Exactly-once finalization is
> now proven **live** on a clean deployment (`pv-val3`), including under real concurrency: 8
> simultaneous finalizes produced **1** commit and **1** COMMITTED record. The duplicate-submission
> control in `signoff_register` was proven in the same run. See
> [`EP2-CONCURRENCY-LIVE.md`](EP2-CONCURRENCY-LIVE.md). The counts in *this* file still describe the
> earlier run and are correct for it.

<!-- superseded claim, retained for traceability:
Concurrency and exactly-once replay-storm behavior (idempotent finalize, single FINAL# marker) are proven
by the offline suite (`tests/`) — **128/128 passing at the time of this run** (up from 107: +2 for the draft pass-by-reference
regression guard). A live prod-scale load test is a customer-side Gate-B exit item.
-->

The offline suite was **128/128 passing at the time of this run**. A live prod-scale load test and a
concurrency/replay-storm harness remain customer-side Gate-B exit items.

## Findings fixed during this EP1 run

Two issues surfaced and were fixed before cutting the tag — the run did its job:

1. **Validator false-FAIL on Windows (harness).** `guard_genuine` initially failed because
   `validate_deployment.py` read the `aws lambda invoke` output files with the platform default encoding
   (CP1252 on Windows), corrupting the em-dash (U+2014) in the signed `source` label before re-passing it
   to the guard. Ground-truth HMAC recomputation confirmed the deployed control plane was correct
   (signature matched the proper em-dash source). Fix: the harness now reads/writes all invoke payloads as
   UTF-8. In production the `sanitized_ref` crosses Step Functions state and the gateway as UTF-8 JSON, so
   this was never a runtime control gap. Re-run: `guard_genuine: PASS`.

2. **Real R3-2 gap — narrative in execution state (product).** The strict canary caught the CIOMS
   narrative TEXT crossing Step Functions state (the `DraftNarrative` task output flowed into
   `AuditIntent` / `HumanSignoff` history). Pass-by-reference had been applied to the ingest/mask side but
   not to the **draft output**. Fix: `draft_narrative` now stores the narrative server-side under a
   mask-signed `sanitized_ref` and returns only the opaque ref (+ metadata); the workflow keeps only the
   ref in state and hands it to the sign-off record; the drafter was granted `PutItem` on the sanitized
   store. Regression guard added (`tests/test_draft_pass_by_reference.py`). Re-deployed; re-run strict
   canary: **PASS (0 leaks)**.

## Teardown + residual sweep (done)

`cdk destroy --all -c env=val1` removed all 7 stacks. Two CloudFormation custom-resource **provider**
log groups (gateway AgentCore attachment, network AwsCustomResource) and the disposable Cognito pool were
then removed explicitly; the WORM audit ledger table and WORM S3 vault (sandbox-demo retention, synthetic
data only) were emptied and deleted. Final residual sweep:

```
validate_deployment.py --env val1 --expect-absent  →  {"residual_stacks": [], "deployment_status": "PASS"}
pv-val1 CloudFormation stacks    : 0
pv-val1 Cognito user pools       : 0
pv-val1 DynamoDB tables          : 0   (audit ledger deleted)
pv-val1 S3 buckets (WORM vault)  : 0
pv-val1 Step Functions machines  : 0
/aws/lambda/pv-val1 log groups   : 0
KMS aliases / secrets (pv-val1)  : none residual
```

**Zero residual.** No account IDs appear in this record (redacted to `111122223333`).
