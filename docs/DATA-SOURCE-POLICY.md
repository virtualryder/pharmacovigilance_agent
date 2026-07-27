# Data-Source Policy — Pharmacovigilance ICSR Assistant

*What external data the assistant uses, what it means, and the anti-fabrication rule. One page.*

---

## The one external dependency: openFDA / FAERS

The assistant's only sanctioned external destination (Gate-B egress allowlist = `.api.fda.gov` ONLY) is
the public **openFDA drug-event API** (FAERS aggregate data).

- **What it is:** aggregate, non-PHI **background/reference** — a report count and top MedDRA reaction
  terms for a suspect product from spontaneous FAERS reports.
- **What it is NOT:** it is **not authoritative** for a specific case, a causality determination, or an
  incidence/denominator. FAERS is spontaneous-report data with known reporting bias. It is reviewer
  **context only**, and it **never feeds the seriousness determination** (which `assess_seriousness`
  derives solely from the de-identified ICSR).

## Anti-fabrication rule (P0-4)

On a source failure or an empty result, `openfda_lookup` returns `found:false, authoritative:false` with
**no invented figures** — it never substitutes a canned aggregate. The deterministic `background` guard
fails closed if a lookup claims `authoritative` but carries no aggregate terms. A downstream consumer
must treat missing background as unavailable, never mistake a fabricated number for real FAERS data.

## PHI / licensed data (out of scope here)

- **PHI** in the case is de-identified by `mask_pii` (Comprehend) and proven by a signed `sanitized_ref`
  before any assessment, draft, or audit. Raw PHI never reaches the model or the audit.
- **MedDRA / WHODrug** licensed dictionaries are **stubbed** — term/drug coding is adopter work.
- **E2B(R3) XML** generation and gateway submission (FAERS / EudraVigilance ESG) are adopter work.

## Before real (PHI) data

Confirm which exact case fields enter the platform, whether de-identification is sufficient for the use,
retention, who can access raw vs masked content, and the HIPAA / 21 CFR Part 11 obligations. **R3-2
pass-by-reference is implemented** — raw `source` never enters Step Functions state (ingest → opaque
`case_ref`; masked text reached server-side via the signed `sanitized_ref`), so the strict PHI canary
can PASS. (B5 tenant-scoped fetch on the case store remains a follow-on.)
