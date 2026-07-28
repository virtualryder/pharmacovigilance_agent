# Validated Release Record

*Single source of truth for the current release tag is the repo-root `RELEASE` file, enforced by
`tests/test_release_consistency.py`. Authoritative counts + limitations: `RELEASE-MANIFEST.md`.*

| Field | Value |
|---|---|
| Tag | `v0.1.1-pilot-rc1` — cut after the live EP1 validation below. Single source of truth: `RELEASE`. |
| Commit SHA | the commit carrying tag `v0.1.1-pilot-rc1` (`git rev-list -n1 v0.1.1-pilot-rc1`) |
| Test count | **112** offline (control-plane + CDK synthesis + CI-completeness gates). Authoritative matrix: [`RELEASE-MANIFEST.md`](RELEASE-MANIFEST.md) |
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
