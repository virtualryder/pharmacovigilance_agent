# EP1 — Live Clean-Account Validation (Pharmacovigilance ICSR Assistant)

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

Concurrency and exactly-once replay-storm behavior (idempotent finalize, single FINAL# marker) are proven
by the offline suite (`tests/`) — **109/109 passing at the time of this run** (up from 107: +2 for the draft pass-by-reference
regression guard). A live prod-scale load test is a customer-side Gate-B exit item.

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
