# START HERE — Pharmacovigilance ICSR Intake Assistant

*One page. What this is, what's proven, how to evaluate it, and what a pilot looks like. Target
validated release: **[`v0.1.0-pilot-rc1`](https://github.com/virtualryder/pharmacovigilance_agent/releases/tag/v0.1.0-pilot-rc1)**
(cut after the live EP1 validation; deploy tags, never `main`). Supported deployment path: **AWS CDK**
(`cdk/`); the shell engine is legacy/internal.*

> **Evaluating for a pilot?** Read [`PV-PILOT-READINESS-PLAN.md`](PV-PILOT-READINESS-PLAN.md) and
> [`RELEASE-MANIFEST.md`](RELEASE-MANIFEST.md) (authoritative counts + limitations). It states plainly
> what is **not yet true**: no live EP1 evidence yet, no independent audit, no drug-safety SME sign-off,
> synthetic data only.

## What this is (and is not)

A **governed assistant** for pharmacovigilance intake: it extracts non-PHI decision fields from an
adverse-event source, pulls **aggregate FAERS background (reference context only)**, de-identifies PHI,
runs a deterministic **ICH E2B(R3) / 21 CFR 314.80 seriousness + reporting-clock** assessment, detects
duplicate ICSRs (holding them so they are never double-reported), **prepares** a causality/reportability
determination, and drafts a CIOMS narrative — every consequential action human-approved, exactly once,
with a tamper-evident audit trail.

It is **NOT an autonomous submitter**: no ICSR submission, no causality commit, no E2B gateway write —
Cedar-forbidden, tool-refused, human-gated ([`PILOT-SCOPE.md`](PILOT-SCOPE.md)). openFDA/FAERS is
reference context, never a case-level or causality source.

## Evidence provenance — read this honestly

The control plane is ported from the proven financial-aid/housing pattern (signed sanitized-ref masking,
token boundary, deterministic Step Functions controller). **What's proven today: the 95-test offline
suite** — control-plane behavior + full CDK stack synthesis. **What's NOT proven yet: a live EP1
clean-account run** with captured evidence; that run cuts `v0.1.0-pilot-rc1`. See `RELEASE-MANIFEST.md`.

## Reading order by role

| You are | Read, in order |
|---|---|
| **Solution Architect** | [`DEPLOYMENT-GUIDE.md`](DEPLOYMENT-GUIDE.md) → [`cdk/README.md`](cdk/README.md) → [`PV-PILOT-READINESS-PLAN.md`](PV-PILOT-READINESS-PLAN.md) |
| **CISO / security** | [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md) → [`docs/GATE-B-CHECKLIST.md`](docs/GATE-B-CHECKLIST.md) → [`docs/DATA-SOURCE-POLICY.md`](docs/DATA-SOURCE-POLICY.md) |
| **Safety / QPPV leadership** | [`PILOT-SCOPE.md`](PILOT-SCOPE.md) → README §controls → the pilot offer below |
| **Auditor / compliance** | [`RELEASE-MANIFEST.md`](RELEASE-MANIFEST.md) → [`VALIDATED_RELEASE.md`](VALIDATED_RELEASE.md) |

**Regulatory frame:** ICH E2B(R3) · 21 CFR 314.80 (postmarket expedited/periodic reporting) · GVP ·
HIPAA (PHI) · 21 CFR Part 11 (electronic records/signatures — adopter CSV). Adopter work: MedDRA/WHODrug
coding, E2B(R3) XML + FAERS/EudraVigilance gateway, Argus/ArisG integration.

## Status in one line

Control-plane hardened + full CDK/Gate-B IaC, **live EP1-validated** (2026-07-27, `pv-val1`),
**109/109 offline tests (incl. 22 CDK synthesis)**, tag `v0.1.0-pilot-rc1`. Evidence:
`evidence/EP1-VALIDATION.md` (validate PASS, controller to the human gate, DuplicateHold, **strict PHI
canary 0 leaks**). Next: a credentialed drug-safety (QPPV) SME sign-off, enterprise IdP round-trip, and
independent security testing before real data.
