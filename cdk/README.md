# CDK — the supported deployment path (Pharmacovigilance ICSR Assistant)

Reviewable, parameterized infrastructure-as-code that replaces the legacy shell engine for customer
deployments. Seven stacks (prefix `pv-<env>`), explicit least-privilege IAM, exact-ARN outputs (P0-7),
the deterministic Step Functions controller (P0-2), the sanitized-artifacts store (P0-1), and the full
Gate-B posture as switches.

| Stack | What it creates | Controls |
|---|---|---|
| `pv-<env>-data` | append-only audit ledger (PITR, RETAIN), sanitized-artifacts store (TTL), pending-approvals table, WORM vault (Object Lock, retention **profile**), optional customer-managed KMS | P0-1 · P0-12 (`-c retention_profile=sandbox-demo\|pilot\|production-reference`) · Gate-B B2 |
| `pv-<env>-network` *(optional, `-c network_mode=private`)* | 2-AZ VPC, governed Lambdas in ISOLATED subnets, **Network Firewall deny-by-default egress allowlist = `.api.fda.gov` ONLY** (openFDA), S3/DDB gateway + interface endpoints, 443-only SG | Gate-B B1 |
| `pv-<env>-compute` | one Lambda per governed tool, explicit least-privilege IAM, tamper **Deny** on the audit writer, exact-ARN outputs, **single-key provenance signing secret** (Secrets Manager, no plaintext), CMK env+logs under `kms=customer-managed`, `TENANT_ID` pinning | P0-5 · P0-7 · Gate-B B5 |
| `pv-<env>-workflow` | the deterministic controller state machine: Extract → LookupBackground → Mask → AssessSeriousness → **DetectDuplicate → DuplicateHold** (terminal work queue) → DraftNarrative → AuditIntent → **HumanSignoff** (`waitForTaskToken`) → Finalize; every stage gated by `workflow_guards`, fail-closed to ManualReview | P0-2 |
| `pv-<env>-identity` | Cognito pool (**zero users, no passwords**), `pv_reviewer` group; `-c identity_mode=pilot` → MFA ON (software token), threat protection ENFORCED, admin-create-only; OIDC IdP federation as IaC | P0-6 · Gate-B B3 |
| `pv-<env>-observability` | CloudWatch dashboard, guard-failure + Lambda-error + workflow alarms, CMK-encrypted SNS ops topic | R3-3 |
| `pv-<env>-gateway` | AgentCore/Gateway/Cedar attachment as IaC (custom resource): policy engine → MCP gateway (CUSTOM_JWT) → one target per tool Lambda (exact ARNs) → every Cedar policy → **ENFORCE** | GA-1 |

## Deploy

Supported release tag: `v0.1.0-pilot-rc1` (cut after live EP1; deploy tags, never `main`).

```bash
git checkout v0.1.0-pilot-rc1           # a validated release tag, never main
cd cdk && pip install -r requirements.txt
cdk bootstrap aws://<acct>/us-east-1    # once per account
# full Gate-B posture:
cdk deploy --all -c env=pilot -c retention_profile=pilot -c kms=customer-managed \
  -c network_mode=private -c identity_mode=pilot -c tenant=<sponsor-id>
```

The openFDA lookup needs no API key (public). Tear down with `cdk destroy --all` (the audit ledger +
WORM vault + CMK are RETAIN'd by design).

## Offline verification (no AWS)

`python -m pytest tests/test_cdk_stacks.py -q` synthesizes all seven stacks with `aws_cdk.assertions`
and asserts the controls (retention profiles, tamper-Deny, exact-ARN outputs, no-plaintext signing
secret, the DuplicateHold controller shape, no users/passwords, locked egress `.api.fda.gov`, tenant
pinning, MFA identity, CMK coverage, observability alarms, gateway ENFORCE + the PV tool/policy set).

## Notes / follow-ons

- **Single-key provenance** (not the financial-aid agent's GA-2 domain split) — a follow-on hardening.
- **Not yet pass-by-reference**: the raw `source` transits Step Functions state until masking; add an
  ingest/case-store step to reach zero-PII telemetry before a real-data pilot (see PV-PILOT-READINESS-PLAN).
- **Live EP1** clean-account validation is the remaining step to a captured-evidence release.
