# Incident Response — Pharmacovigilance ICSR Assistant

*EDU-parallel IR procedure for the pilot, adapted to drug-safety. PHI is HIPAA-covered; the platform
supports regulated safety reporting (ICH E2B / 21 CFR 314.80 / GVP) and, where applicable, 21 CFR Part
11 electronic records. This defines who does what when something goes wrong, mapped to the detecting
control.*

---

## Roles

| Role | Responsibility |
|---|---|
| **Incident lead** | PV/QPPV office lead — owns the incident, decides notification |
| **Technical responder** | IT/security — contains, rotates keys, pulls logs |
| **Privacy officer** | HIPAA breach determination + notification content |
| **QPPV / safety physician** | Assesses any impact on regulatory reporting obligations/timelines |
| **Builder/SA** | Supports diagnosis of the assistant/pipeline (best-effort, pilot) |

## Detection sources (built)

- **PHI-telemetry canary** — strict 0-hit assertion across Logs / X-Ray / DLQ / Step Functions history
  (R3-2 pass-by-reference keeps raw + masked content out of state).
- **Guard-failure metric** (`Pharmacovigilance/Governance :: GuardFailed`) — forged/tampered evidence.
- **WORM audit ledger** — tamper-evident hash chain (`verify_chain`).
- **CloudWatch alarms** on the above (ObservabilityStack).

## Runbooks

### R1 — Suspected PHI in telemetry / stores
Contain the affected Lambda/version; assess what fields, whose data, how many records; rotate KMS-encrypted
secrets/keys if credentials/signing material may be exposed; purge offending telemetry per policy; the
privacy officer makes the **HIPAA breach determination** and, if reportable, drives notification per the
institution's HIPAA procedure and any state law; add the failing case to the canary/redaction suite.

### R2 — A wrong seriousness/narrative reached a downstream process
Retract/correct; the assistant's outputs are drafts/estimates gated by a human, so trace the approval;
the **QPPV assesses any impact on expedited-reporting timelines** (a mis-assessed serious/unexpected case
is a reporting-clock risk); root-cause (stale thresholds, an unverified background, a duplicate that
should have held); log the correction; hold the cohort if systemic.

### R3 — Forged/tampered evidence (guard spike)
Investigate attack vs bug; confirm the pipeline **failed closed** to ManualReview (no case advanced on
unverified state); verify the audit chain; rotate signing keys if compromise is suspected.

### R4 — Unauthorized access / identity compromise
Disable the affected pilot identity (MFA-enforced Cognito); access review; check the audit ledger for
actions under the identity; rotate credentials.

## Breach determination & notification

The privacy officer determines whether an incident is a reportable **HIPAA** breach and drives
notification per the institution's procedure and timelines; the QPPV separately assesses whether the
incident affects a **regulatory safety-reporting obligation** (and any late-report remediation). This
document makes no categorical promises about who is/isn't notified — that is the privacy officer's and
QPPV's determination under the applicable rule and facts.

## Before real (PHI) data: tabletop

Run a tabletop of R1 and R2 with the PV/QPPV office, IT/security, and privacy officer before the
assistant touches real cases. Record the date and participants in the change log.
