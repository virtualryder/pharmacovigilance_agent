# Validated Release Record

*Single source of truth for the current release tag is the repo-root `RELEASE` file, enforced by
`tests/test_release_consistency.py`. Authoritative counts + limitations: `RELEASE-MANIFEST.md`.*

| Field | Value |
|---|---|
| Tag | `v0.1.0-pilot-rc1` — the target pilot tag. **Cut AFTER the live EP1 validation captures its evidence** (not yet run). Single source of truth: `RELEASE`. |
| Commit SHA | the commit carrying tag `v0.1.0-pilot-rc1` once cut (`git rev-list -n1 v0.1.0-pilot-rc1`) |
| Test count | **95/95** offline (control-plane + CDK synthesis). Authoritative matrix: [`RELEASE-MANIFEST.md`](RELEASE-MANIFEST.md) |
| Validation date | ☐ pending EP1 |
| Region | us-east-1 (target) |
| Deployment | AWS CDK `--all`; EP1 target: `retention_profile=pilot kms=customer-managed network_mode=private identity_mode=pilot tenant=<sponsor-id>` |
| Evidence | ☐ to be captured on the EP1 clean-account run (happy path, DuplicateHold, PHI canary, load + exactly-once replay), then torn down |

Until the EP1 fields are captured, this repo is **code + synth-validated** (the CDK synthesizes and the
controls are unit-proven) but **not yet live-validated**. See `DEPLOYMENT-GUIDE.md` for the run.
