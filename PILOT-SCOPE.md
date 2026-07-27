# Pilot Scope — Pharmacovigilance ICSR Intake Assistant

*What a pilot of this assistant is, what it is explicitly NOT, and what the adopter owns. One page.*

---

## What it is

A **governed assistant** for pharmacovigilance intake: it extracts non-PHI decision fields from an
adverse-event source, pulls **aggregate FAERS background (reference context only)**, de-identifies PHI,
runs a **deterministic ICH E2B(R3) / 21 CFR 314.80 seriousness + reporting-clock** assessment, detects
duplicate ICSRs, **prepares** a documented causality/reportability determination, and drafts a CIOMS
narrative — then **pauses at a human sign-off gate**. Every consequential action is made and committed by
a qualified human.

## What it will NOT do (do not claim these)

- **No autonomous submission.** The assistant never submits an ICSR to a regulator; a qualified person
  commits the submission at the human gate (Cedar `no_self_submit`, tool-refused).
- **No autonomous causality commit.** Causality/reportability is **prepared only**; a *different* senior
  safety physician commits it (Cedar `no_self_causality_commit`, tool-refused).
- **No authoritative case data from openFDA.** FAERS background is aggregate, spontaneous-report context
  — not authoritative for a case, causality, or incidence, and it never feeds the seriousness result.
- **No MedDRA / WHODrug coding.** Licensed-dictionary term/drug coding is stubbed and is adopter work.
- **No E2B(R3) XML generation or gateway submission** (FDA FAERS / EMA EudraVigilance ESG) — adopter.
- **No safety-system integration** (Argus / ArisG) — adopter.
- **No PHI leaves masked.** Downstream tools refuse anything not proven de-identified by a signed
  `sanitized_ref` (P0-1); a `deidentified: true` boolean is never accepted.

## Adopter / out-of-scope (state in every customer conversation)

Licensed **MedDRA/WHODrug** dictionaries · **E2B(R3) XML** + FAERS/EudraVigilance gateway submission ·
**Argus/ArisG** safety-system integration · **21 CFR Part 11** electronic-records/signature validation ·
**GVP** module obligations (signal management, PSUR/PBRER, literature) · case **follow-up/versioning** as
new information arrives · enterprise **IdP** federation · the authoritative, market-specific **seriousness
thresholds and reporting clocks** and their regulatory review · production **authorization to operate**.

## Maturity (honest, code-accurate)

**Present + hardened this cycle:** signed-`sanitized_ref` de-identification (P0-1), token boundary (P0-3),
deterministic guard set (P0-2), no-fabrication openFDA (P0-4), Cedar deny-by-default, WORM hash-chained
audit, human separation-of-duties sign-off, supply-chain + governance-core integrity. **Plus the full
7-stack AWS CDK set + Gate-B posture as switches** (private net + `.api.fda.gov` egress allowlist + CMK +
MFA identity + tenant pin), **release discipline** (RELEASE + manifest + consistency gate), START-HERE,
DEPLOYMENT-GUIDE, threat model, data-source policy, Gate-B checklist, pilot-readiness plan. Suite: **95
offline tests** (73 control-plane + 22 CDK synthesis).

**Not yet (to a captured-evidence pilot):** the **live EP1** clean-account validation (the SA/customer
runs it → cuts `v0.1.0-pilot-rc1`). The full operating-model doc bundle is now in `docs/` (KEY-MANAGEMENT,
RETENTION-PROFILES, INCIDENT-RESPONSE, AUDIT-READINESS, MCP-GATEWAY, CONFIGURATION-WORKSHEET,
SME-REVIEW-PACKET). Evidence to date is author-produced; independent security testing and a QPPV /
drug-safety SME sign-off on the rules + narrative language are pre-production gates.

## Recommended pilot shape

One product / one market · synthetic and de-identified retrospective cases first, then shadow mode ·
read-only everything · every output human-reviewed · no submission, no causality commit, no gateway
writes · measured against handling time, duplicate-catch accuracy, seriousness/clock agreement with a
reviewer, and narrative edit rate.
