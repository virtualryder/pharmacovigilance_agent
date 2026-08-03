# EP2 — Live proof of the two concurrency controls (`pv-val3`, 2026-08-03)

**Environment:** `pv-val3` · **Region:** us-east-1 · **Account:** `111122223333` (redacted) ·
**Core:** `governed-core 1.4.0` (pinned wheel, hash-verified) ·
**Switches:** `network_mode=private kms=customer-managed identity_mode=pilot
tenant=pv-example-sponsor retention_profile=sandbox-demo` (all Gate-B switches ON).

## Why this run happened

Two controls were shipped, covered by offline tests, and **never exercised against deployed AWS
resources**:

1. **Exactly-once finalization** (`FINAL#` conditional put). `evidence/EP1-VALIDATION.md` states
   explicitly that the EP1 run *predates* this control.
2. **Duplicate-submission protection** (`signoff_register` conditional put). PV and benefits only
   received it via the `governed-core 1.4.0` bump on 2026-08-03; it had existed in the financial-aid
   and housing agents and in neither the package nor these two agents.

The offline tests for both are real — each was verified to fail when the control is disabled — but
they run against a **fake DynamoDB**. A conditional put is precisely the control where "passes
against a fake" and "holds against the real service" can diverge: the condition expression, the
reserved-word handling, the attribute names, and the IAM policy all have to be right simultaneously.
Until this run, "exactly-once" was an offline claim.

## Deployment

All 7 CDK stacks reached `CREATE_COMPLETE` on a clean environment. AgentCore Gateway + Cedar in
**ENFORCE** mode attached as IaC (no post-deploy shell step). Elapsed **18:18 → 18:38 UTC (~20 min)**;
AWS Network Firewall provisioning is the long pole (~8 min of that).

The staged Lambda bundle was stamped `CORE_VERSION = 1.4.0`, confirming the deployed control code
came from the **pinned wheel** and not from a copy in this repo.

## 1. Exactly-once finalization — **PASS**

Case `PVLIVE-EO-CDD54CF176`. Three sequential invocations of the deployed `pv-val3-finalize`:

| Invocation | `committed` | `idempotent` | `submission_id` |
|---|---|---|---|
| First | `true` | *(absent)* | `SUB-25CBF1F11611` |
| Replay, **same** approver | `true` | **`true`** | `SUB-25CBF1F11611` |
| Replay, **different** approver | `true` | **`true`** | `SUB-25CBF1F11611` |

Corroborated by reading the ledger, not just by trusting the return value:

- `COMMITTED` records for the case: **1**
- `FINAL#<case>` marker item present: **yes**
- All three submission ids identical: **yes**

The different-approver case matters most: a second, genuinely distinct approval path is refused a
second COMMITTED record and receives the **original** submission id. That is the ICSR
double-reporting scenario, and it fails closed.

## 2. Exactly-once under real concurrency — **PASS**

Sequential replay proves idempotence; it does not prove the **race**. Case
`PVLIVE-RACE-CDD54CF176`, **8 simultaneous** invocations from a thread pool:

| Measure | Result |
|---|---|
| Parallel invocations | 8 |
| Non-idempotent commits | **1** |
| Idempotent returns | **7** |
| `COMMITTED` records for the case | **1** |
| Distinct submission ids returned | **1** |

Eight concurrent finalizes, one commit. This is the claim that could not be made before this run.

## 3. Duplicate-submission protection — **PASS**

Case `PVLIVE-DUP-CDD54CF176`, against the deployed `pv-val3-signoff-register`:

| Step | Result |
|---|---|
| First registration | `registered: true` |
| Second registration, same case still `PENDING` | **refused** — Lambda `Unhandled` error (the fail-closed `RuntimeError`) |
| Stored `task_token` | the **FIRST** token — the original was **not** overwritten |
| Stored `status` | `PENDING` |
| `content_hash` bound to the record | yes |

The stored-token assertion is the one that matters. Overwriting would strand the first execution's
Step Functions task token — a workflow that waits forever with no operator signal.

## Turnkey validator — `scripts/validate_deployment.py --env val3` → **PASS**

```json
{
 "release": "dev", "env": "val3",
 "stacks": "COMPLETE", "secret": "PRESENT",
 "masking_control": "PASS",
 "guard_genuine": "PASS",
 "forged_ref_denied": "PASS",
 "ingest_pass_by_reference": "PASS",
 "workflow": "PASS:RUNNING(awaiting human gate)",
 "deployment_status": "PASS"
}
```

Confirms the 1.4.0 core did not regress the masking boundary, the proof-of-masking signature check
(a forged `sanitized_ref` is still refused), or R3-2 pass-by-reference. The workflow terminating at
the human gate is the expected happy-path end state for an assistant that never self-submits.

## Reproducing this

```bash
python scripts/prove_concurrency_live.py --env <env> --region us-east-1   # expect overall: PASS
```

The script asserts behaviour **and** reads the audit ledger and pending-approvals table to confirm
the record counts, so a control that returns the right JSON while writing the wrong rows still fails.

## Teardown — **zero residual**

`cdk destroy --all` removed all 7 stacks. Executions parked at the human sign-off gate were stopped
first; otherwise the workflow stack delete blocks. Two slow steps are expected and are not faults:
**VPC Lambda ENI detachment (~22 min)** and **Network Firewall deletion**. Total teardown 18:43 →
19:14 UTC (~31 min).

The retained-by-design resources then required explicit removal — `cdk destroy` alone does **not**
reach zero:

| Retained resource | Action |
|---|---|
| `pv-val3-audit-ledger` (WORM ledger) | `delete-table` |
| WORM S3 vault (Object Lock GOVERNANCE) | versioned delete with `BypassGovernanceRetention`, then `delete-bucket` |
| `pv-val3-identity` Cognito pool | `delete-user-pool` |
| 2 custom-resource **provider** log groups (gateway attachment, network AwsCustomResource) | `delete-log-group` |

Final sweep:

```
pv-val3 CloudFormation stacks : 0
pv-val3 Lambda functions      : 0
pv-val3 DynamoDB tables       : 0
pv-val3 S3 buckets            : 0
pv-val3 Cognito user pools    : 0
pv-val3 Step Functions        : 0
pv-val3 VPCs                  : 0
pv-val3 NAT gateways          : 0
pv-val3 Network firewalls     : 0
pv-val3 KMS aliases / secrets : 0
pv-val3 log groups            : 0
```

**Zero residual.** No account IDs appear in this record (redacted to `111122223333`).

## Scope and limits — what this run does NOT establish

- Synthetic data only. No real adverse-event or patient data was processed.
- Author-produced evidence. **No independent audit or penetration test.**
- 8-way concurrency is a correctness proof of the conditional put, **not** a prod-scale load test.
  Sustained throughput and a full replay-storm remain customer-side Gate-B exit items.
- This validates controls that **support** validation activities. It is not a CSV/CSA validation
  package, and it confers no GxP or Part 11 compliance — that remains the sponsor's.
