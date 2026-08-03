# PV Pilot Readiness Plan

**Product:** Pharmacovigilance ICSR Intake **Assistant** (never an autonomous submitter or
causality-committer). **Repo:** `github.com/virtualryder/pharmacovigilance_agent`. **Target tag:**
`v0.1.1-pilot-rc1` (cut after live EP1 — **done 2026-07-27**). **Build state:** control-plane hardened +
full CDK/Gate-B IaC, **live EP1-validated**; **128 offline tests** (control-plane + 23 CDK synthesis + 3 CI-completeness gates).
**Owner:** David Ryder (AWS HCLS SA).

---

## 0. Honesty guardrails (carried into every claim)

- The agent is an **assistant**: it prepares ICSR intake, seriousness assessments, duplicate holds,
  causality **preparation**, and draft narratives. It never submits an ICSR, commits a causality
  determination, or writes to an E2B gateway — Cedar-forbidden, tool-refused, human-gated.
- **openFDA/FAERS is reference context**, not authoritative for a case, causality, or incidence, and it
  never feeds the seriousness determination.
- **Evidence is author-produced and synthetic-only.** The CDK synthesizes, the controls are unit-proven,
  and the **live EP1 run is captured** (`evidence/EP1-VALIDATION.md`, 2026-07-27) — but on a disposable
  sandbox with synthetic data; no independent audit and no real PHI yet.

## 1. What is done

| Area | Status |
|---|---|
| Control plane (signed sanitized_ref P0-1, token boundary P0-3, deterministic guards P0-2, no-fabrication openFDA P0-4) | ✅ |
| AWS CDK 7-stack set (`cdk/pv_stacks`) + Gate-B switches | ✅ synth-validated (23 CDK assertions) |
| Deterministic Step Functions controller w/ DuplicateHold terminal | ✅ (in CDK) |
| Release discipline (`RELEASE` + manifest + `VALIDATED_RELEASE` + consistency gate) | ✅ |
| START-HERE, DEPLOYMENT-GUIDE, PILOT-SCOPE, threat model, data-source policy, Gate-B checklist | ✅ |

## 2. Gates to pilot depth

**Gate A — code + synth (done).** 128 offline; CDK synthesizes to valid CloudFormation; release
scaffolding + core docs in place.

**Gate B — live EP1 validation (DONE, 2026-07-27, env `pv-val1`, us-east-1).** All Gate-B switches
deployed to a clean account; captured `validate_deployment.py` PASS, a happy-path run to the human gate, a
DuplicateHold terminal, and the **strict PHI canary PASS (0 leaks)**; torn down + residual-swept; recorded
in `VALIDATED_RELEASE.md` + `evidence/EP1-VALIDATION.md`; tag cut. The strict canary caught a real R3-2
gap (the CIOMS narrative crossed execution state) — fixed (narrative now server-side under a ref) and
re-validated. Prod-scale live load remains a customer-side exit item.

**Gate C — before real (PHI) data.**
- ~~Pass-by-reference (R3-2)~~ — **done**: ingest/case-store keeps raw + masked content out of execution
  history (synth + runtime proven); the strict canary is expected to PASS on the EP1 run.
- **Drug-safety SME (QPPV) sign-off** on the seriousness rules, reporting clocks, duplicate logic, the
  causality prepare-only boundary, and the CIOMS narrative language.
- **Enterprise IdP** federation round-trip; HIPAA/21 CFR Part 11 data-handling assessment. (The
  operating-model doc bundle — KEY-MANAGEMENT, RETENTION-PROFILES, INCIDENT-RESPONSE, AUDIT-READINESS,
  MCP-GATEWAY, CONFIGURATION-WORKSHEET, SME-REVIEW-PACKET — is now complete in `docs/`.)

**Gate D — before production.** Independent security testing / pen test; multi-account separation
(workload vs evidence); asymmetric-KMS signing evaluation; optionally sign the openFDA background (would
add a second signing domain); MedDRA/WHODrug
coding + E2B(R3) XML gateway + Argus/ArisG integration; measured pilot metrics; production ATO/CSV.

## 3. Explicit not-yet-true (say these out loud)

- Live EP1 evidence is captured (2026-07-27) but on a disposable sandbox with synthetic data only.
- Pass-by-reference (R3-2) **done in both directions** — raw, masked, AND the drafted narrative stay out
  of SFN state (strict canary PASS, 0 leaks). B5 tenant-scoped fetch is a follow-on.
- One signing domain (mask_pii); openFDA background is unsigned (authoritative-flag). GA-2 split N/A.
- No independent audit/pen test; no QPPV SME sign-off; no enterprise IdP round-trip; no prod-scale load.
- MedDRA/WHODrug coding, E2B(R3) XML/gateway submission, Argus/ArisG, 21 CFR Part 11 CSV are adopter.

## 4. Recommended pilot shape

One product / one market · synthetic + de-identified retrospective cases first, then shadow mode ·
read-only everything · every output human-reviewed · no submission, no causality commit, no gateway
writes · measured against handling time, duplicate-catch accuracy, seriousness/clock agreement with a
reviewer, and narrative edit rate.
