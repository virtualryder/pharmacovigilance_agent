# SME Review Packet — Drug-Safety (QPPV) Sign-off

*Gate-C blocker. Formatted for a **credentialed pharmacovigilance professional** (a QPPV, a
drug-safety physician, or a senior safety scientist) to red-line. The domain correctness of the
seriousness rules, the reporting clock, the duplicate logic, and the narrative language is currently
**asserted by the builder, not attested by an SME**. No real case data is processed until Section 6 is
signed.*

---

## 1. What the assistant does (and does not do)

A **Pharmacovigilance ICSR Intake Assistant**: it extracts non-PHI decision fields, pulls aggregate
FAERS **reference context**, de-identifies PHI, runs a deterministic seriousness + reporting-clock
assessment, detects duplicate ICSRs (holding them), **prepares** a causality/reportability determination,
and drafts a CIOMS narrative. It **never** submits an ICSR, commits a causality determination, or writes
to an E2B gateway — those are human-only (Cedar-forbidden, tool-refused).

## 2. Seriousness assessment (plain English)

Serious per ICH E2B(R3) / 21 CFR 314.80 if any criterion is met: **death, life-threatening,
hospitalization (initial/prolonged), persistent/significant disability, congenital anomaly, or other
medically important condition.** Reporting clock (postmarket default): **serious + unexpected → EXPEDITED
15-day**; serious + listed → PERIODIC; non-serious → ROUTINE. Expectedness unknown is treated as unlisted
(conservative → expedited) and flagged.

**SME question:** Are the criteria, the expedited 15-day default, and the unknown-expectedness handling
correct for your market(s) and product(s)? Note any deviation (e.g. IND 7-day, EU/EMA specifics).

## 3. Duplicate detection

Deterministic key on **product | event | onset | reporter**; a match returns DUPLICATE with a HOLD so the
case is not double-reported. **SME question:** Is this key and the hold behavior consistent with your
case-handling SOP?

## 4. Causality (human-only, prepare-only)

The assistant **prepares** a causality/reportability determination with a required case-specific rationale
and returns a record a **different senior safety physician** must approve; it never commits (GVP / 21 CFR).
**SME question:** Is the prepare-only boundary and the required-rationale rule appropriate?

## 5. Sample outputs to review

Generate one example per branch (from synthetic cases): a serious/expedited assessment, a
non-serious/routine, a DuplicateHold, a causality preparation, and a CIOMS **draft narrative**. Review the
narrative language specifically for accuracy, preservation of `[REDACTED:…]` placeholders, and that it
never invents identifiers/dates. **SME question:** Are the narratives accurate and appropriately
caveated? Mark any language you would not put into an ICSR.

## 6. SME sign-off

I am a credentialed pharmacovigilance professional and I have reviewed the seriousness rules (§2), the
duplicate logic (§3), the causality boundary (§4), and the sample narratives (§5). My corrections are
recorded and (where accepted) reflected in the rules/config.

Name / title / organization: __________________________
QPPV / credential reference: __________________________
Signature / date: __________________________

*Until this section is signed, the assistant remains synthetic-data-only and is not run against real
adverse-event cases.*
