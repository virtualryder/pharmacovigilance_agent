# Release Manifest — single authoritative record

*This is the ONE place that states the release, the counts, and the validation status. Every other
document should reference this file rather than restating numbers. If a number anywhere disagrees with
this table, this table is correct and the other file is a bug.*

---

## Authoritative record

| Field | Value |
|---|---|
| **Product** | Pharmacovigilance ICSR Intake **Assistant** (never an autonomous submitter or causality-committer) |
| **Target pilot tag** | `v0.1.0-pilot-rc1` (RELEASE file) — **not yet cut**: awaiting the live EP1 validation |
| **Offline test suite** | **95 / 95** passing (control-plane 73 + **22 CDK stack-synthesis** assertions) |
| **Deployment IaC** | AWS CDK, 7 stacks (`cdk/pv_stacks`, prefix `pv-`) — synthesizes to valid CloudFormation (in-suite `aws_cdk.assertions`) |
| **Gate-B posture** | private networking + egress allowlist `.api.fda.gov` ONLY · customer-managed KMS · MFA-enforced pilot identity · tenant pin — **as CDK switches** |
| **Live EP1 validation** | **NOT YET RUN** — the remaining step to captured evidence + a cut release (the customer/SA runs the 7-stack clean-account deploy + teardown; `DEPLOYMENT-GUIDE.md`) |
| **Control plane** | signed `sanitized_ref` masking proof (P0-1) · token boundary (P0-3) · deterministic Step Functions controller + guards (P0-2) · no-fabrication openFDA (P0-4) · WORM hash-chained audit · human sign-off |
| **Evidence source** | author-produced, synthetic data only — not independently audited or pen-tested |

## Count glossary

- **95 offline tests** — the CI suite (73 control-plane + 22 CDK synthesis). Authoritative offline number.
- **32-check legacy demo** — the shell-engine governance demo; internal reference only, not pilot evidence.

## Known limitations (explicit)

- **Live EP1 not yet captured** — the CDK synthesizes and the controls are unit-proven, but a real
  clean-account deploy + teardown with captured evidence (happy path, DuplicateHold, PHI canary, load +
  exactly-once replay) has not been run. That run cuts `v0.1.0-pilot-rc1`.
- **Pass-by-reference (R3-2) implemented** — raw `source` never enters Step Functions state (ingest →
  opaque `case_ref`; masked content reached server-side via the signed `sanitized_ref`), so the strict
  PHI canary can PASS. Proven at synth (no raw/masked content in the state machine) + runtime
  (`tests/test_pass_by_reference.py`); confirmed on the live EP1 run. (B5 tenant-scoped fetch on the
  case store remains a follow-on.)
- **Single-key provenance** — not the financial-aid agent's GA-2 domain split (follow-on hardening).
- Evidence is author-produced on synthetic data; **no independent audit / pen test** and **no
  credentialed drug-safety (QPPV) SME sign-off** on the seriousness rules + narrative language yet.
- No MedDRA/WHODrug coding, no E2B(R3) XML/gateway submission, no Argus/ArisG integration, no
  21 CFR Part 11 CSV — adopter/out-of-scope (`PILOT-SCOPE.md`).

## Provenance

Author: David Ryder (AWS HCLS SA). Built by porting the financial-aid/housing governed-agent pattern.
Readiness roadmap + gates: `PV-PILOT-READINESS-PLAN.md`. Threat model: `docs/THREAT-MODEL.md`.
