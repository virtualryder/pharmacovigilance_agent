# Release Manifest — single authoritative record

*This is the ONE place that states the release, the counts, and the validation status. Every other
document should reference this file rather than restating numbers. If a number anywhere disagrees with
this table, this table is correct and the other file is a bug.*

---

## Authoritative record

| Field | Value |
|---|---|
| **Product** | Pharmacovigilance ICSR Intake **Assistant** (never an autonomous submitter or causality-committer) |
| **Pilot tag** | `v0.1.1-pilot-rc1` (RELEASE file) — **cut after the live EP1 validation (2026-07-27)** |
| **Offline test suite** | **128 / 128** passing (control-plane + **23 CDK stack-synthesis** assertions) |
| **Deployment IaC** | AWS CDK, 7 stacks (`cdk/pv_stacks`, prefix `pv-`) — synthesizes to valid CloudFormation (in-suite `aws_cdk.assertions`) |
| **Gate-B posture** | private networking + egress allowlist `.api.fda.gov` ONLY · customer-managed KMS · MFA-enforced pilot identity · tenant pin — **as CDK switches, live EP1-validated** |
| **Live EP1 validation** | **DONE (2026-07-27, env `pv-val1`, us-east-1)** — 7/7 stacks incl. AgentCore ENFORCE; `validate_deployment.py` PASS; controller to the human gate; DuplicateHold terminal; **strict PHI canary PASS (0 leaks)**; MFA pool ON, 0 users; torn down + residual-swept. Record: `evidence/EP1-VALIDATION.md` |
| **Control plane** | signed `sanitized_ref` masking proof (P0-1) · token boundary (P0-3) · deterministic Step Functions controller + guards (P0-2) · no-fabrication openFDA (P0-4) · R3-2 pass-by-reference **both directions** (case + narrative) · WORM hash-chained audit · human sign-off |
| **Evidence source** | author-produced, synthetic data only — not independently audited or pen-tested |

## Count glossary

- **128 offline tests** — the CI suite (control-plane + 23 CDK synthesis + 3 CI-completeness gates). Authoritative offline number. Locally you see `127 passed, 1 skipped`: one gate asserts the CDK libs are installed and runs only inside CI.
- **32-check legacy demo** — the shell-engine governance demo; internal reference only, not pilot evidence.

## Known limitations (explicit)

- **Live EP1 captured on a disposable sandbox only** — the clean-account deploy + teardown with evidence
  (validate PASS, controller to the human gate, DuplicateHold, strict PHI canary 0 leaks) ran on synthetic
  data and was torn down; it is not a production ATO and used no real PHI. Record: `evidence/EP1-VALIDATION.md`.
- **Pass-by-reference (R3-2) — both directions** — raw `source` never enters Step Functions state (ingest
  → opaque `case_ref`; masked content reached server-side via the signed `sanitized_ref`), AND the drafted
  CIOMS narrative is stored server-side under a signed ref (never in execution state). The live strict PHI
  canary PASSED with 0 leaks. Proven at synth + runtime (`tests/test_pass_by_reference.py`,
  `tests/test_draft_pass_by_reference.py`). The narrative-in-state gap was found by the live canary during
  EP1 and fixed before the tag was cut. (B5 tenant-scoped fetch on the case store remains a follow-on.)
- **One signing domain** — only `mask_pii` signs (the sanitized_ref); openFDA background is unsigned
  (authoritative-flag only). GA-2 domain-split is N/A (no second signer); signing the openFDA background
  is the relevant future option (`docs/KEY-MANAGEMENT.md`).
- Evidence is author-produced on synthetic data; **no independent audit / pen test** and **no
  credentialed drug-safety (QPPV) SME sign-off** on the seriousness rules + narrative language yet.
- No MedDRA/WHODrug coding, no E2B(R3) XML/gateway submission, no Argus/ArisG integration, no
  21 CFR Part 11 CSV — adopter/out-of-scope (`PILOT-SCOPE.md`).

## Provenance

Author: David Ryder (AWS HCLS SA). Built by porting the financial-aid/housing governed-agent pattern.
Readiness roadmap + gates: `PV-PILOT-READINESS-PLAN.md`. Threat model: `docs/THREAT-MODEL.md`.
