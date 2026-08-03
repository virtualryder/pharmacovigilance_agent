# 21 CFR Part 11 — element-by-element mapping

**Stance.** This accelerator provides technical controls that **support** a sponsor's Part 11 and
computer-system-validation activities. It does not make a system Part 11 compliant, and it is not a
validated system. Compliance and validated status are properties of *your* system inside *your*
quality management system, reached through your risk assessment, IQ/OQ/PQ, SOPs, training, change
control and authorization. Nothing below changes that.

This document exists because the honest answer to "how do you meet Part 11?" is element by element,
and some elements are **not addressed today**. Those are listed as plainly as the ones that are.

Every row cites the implementing code or test so a reviewer can verify rather than trust.

**Legend** — ✅ implemented and covered by a named test · ◑ partially implemented · ❌ not addressed
in this accelerator · ▣ sponsor-owned by design

---

## §11.10 — Controls for closed systems

| Element | Status | Implementation and evidence |
|---|:--:|---|
| **(a)** Validation of systems to ensure accuracy, reliability, consistent intended performance, and the ability to discern invalid or altered records | ▣ | The sponsor performs validation. This accelerator supplies inputs to it: pinned CDK versions (`cdk/requirements.txt`), a 128-test offline suite including 23 CDK stack-synthesis security assertions (`tests/test_cdk_stacks.py`), a deterministic rules engine with a stamped basis (`agents/pharmacovigilance/tools/assess_seriousness.py`), and captured clean-account validation-run evidence (`evidence/EP1-VALIDATION.md`). These are engineering artifacts, **not** a validation package. |
| **(b)** Ability to generate accurate and complete copies in human-readable and electronic form | ◑ | The audit ledger and WORM vault are exportable, and the ledger is human-readable JSON. **Gap:** the approved clinical content itself is short-lived — the case store TTLs at 7 days (`lib/controls/case_store.py`) and sanitized content at 1 day (`lib/controls/sanitized.py`). See the §11.70 note below; this is the most consequential open item. |
| **(c)** Protection of records to enable accurate and ready retrieval throughout the retention period | ◑ | S3 Object Lock with configurable retention profiles (`docs/RETENTION-PROFILES.md`); the audit writer is explicitly DENIED `s3:BypassGovernanceRetention`. **Gaps:** (i) the same deny is not yet applied to the `request_signoff` and `finalize` roles; (ii) the `production-reference` profile defaults to 7 years, which is **shorter than the EU GVP obligation** of product lifetime + 10 years — see the retention note below; (iii) COMPLIANCE-mode Object Lock is a one-way decision. |
| **(d)** Limiting system access to authorized individuals | ✅ | Cedar deny-by-default authorization in ENFORCE mode (`policies/*.cedar`), identity taken from a JWKS-verified token and never from the request body (`docs/Signoff-Identity-Baseline.md`), MFA-enforced pilot identity pool (`cdk/pv_stacks/identity_stack.py`). |
| **(e)** Secure, computer-generated, time-stamped audit trails that record operator entries and actions, **without obscuring previously recorded information**, retained for at least as long as the record | ✅ | The strongest element here. Server-generated, append-only, hash-chained with a server-read head and an atomic compare-and-swap so concurrent writers cannot fork the chain; PutItem-only so the append-only IAM Deny holds; idempotent on exact replay (`lib/controls/evidence.py`). Prior entries cannot be updated or deleted. Tests: `tests/test_audit_chain.py`. **Minor gap:** timestamps are epoch seconds with no timezone and no trusted time source. |
| **(f)** Operational system checks to enforce permitted sequencing of steps and events | ✅ | A deterministic Step Functions controller with a guard on every transition and fail-closed `Choice` defaults, so the model cannot skip masking or seriousness assessment (`cdk/pv_stacks/workflow_stack.py`). The exact state sequence is asserted by `tests/test_workflow_guards.py`. |
| **(g)** Authority checks — only authorized individuals may use the system, sign a record, access the operation, or perform the operation at hand | ✅ | Cedar `pv_reviewer_permit`, `no_self_submit`, `no_self_causality_commit`; separation of duties enforced on verified usernames with a single-use compare-and-swap token. |
| **(h)** Device checks to determine validity of the source of data input | ❌ | Not addressed. There is no device or terminal validation, and no intake trigger is modelled in IaC — `ingest_case` is invoked out of band. Sponsor-owned, but it is a genuine gap in the trust boundary at the front of the system. |
| **(i)** Persons who develop, maintain, or use the system have the education, training, and experience to perform their assigned tasks | ▣ | Sponsor-owned. |
| **(j)** Written policies holding individuals accountable for actions initiated under their electronic signatures | ▣ | Sponsor-owned. |
| **(k)** Appropriate controls over systems documentation — distribution, access, revision control, change control | ◑ | Git history, tagged releases, a release manifest, and machine-enforced consistency gates (`tests/test_release_consistency.py`, `tests/test_doc_counts.py`). Formal documentation change control under a QMS is sponsor-owned. |

## §11.30 — Controls for open systems

| Element | Status | Notes |
|---|:--:|---|
| Additional measures for open systems (encryption, digital signature standards) | ▣ | The reference deployment is a closed system inside the sponsor's AWS account: TLS in transit, KMS at rest, private subnets with the data tier holding no route to the internet. If the sponsor exposes it as an open system, §11.30 measures are theirs to add. |

## §11.50 — Signature manifestations

| Element | Status | Notes |
|---|:--:|---|
| Signed record shall contain the **printed name** of the signer | ❌ | Not implemented. The ledger records a verified username (`lib/controls/approve_signoff.py`), not a printed name for display. |
| Signed record shall contain the **date and time** of signing | ◑ | Recorded as epoch seconds (`lib/controls/evidence.py`). No timezone, no trusted time source. |
| Signed record shall contain the **meaning** of the signature (review, approval, responsibility, authorship) | ❌ | Not implemented. The record carries `action: "approve"` but no signature-meaning field. |
| These items shall be subject to the same controls as the record, and included in any human-readable form | ◑ | The ledger entries are under the same WORM controls; the display requirement is unaddressed because the items above are not captured. |

> **This section is the largest single Part 11 gap.** Closing it is a small, well-defined change:
> add printed-name, ISO-8601/UTC timestamp, and a signature-meaning field to the finalize payload.

## §11.70 — Signature/record linking

| Element | Status | Notes |
|---|:--:|---|
| Electronic signatures shall be **linked to their respective electronic records** to ensure they cannot be excised, copied, or otherwise transferred to falsify a record | ❌ | **Not met, and this is the most important open item in the mapping.** `lib/controls/finalize_signoff.py` writes a payload of `{requester, approver, submission_id}` and `lib/controls/evidence.py` hashes *that*. The hash therefore covers the approval metadata — **not** the ICSR, the seriousness assessment, or the CIOMS narrative that was approved. Nothing today lets an inspector prove *which* narrative version the approver saw. Compounding it, the approved content TTLs out (7 days / 1 day), so after a week the ledger proves an approval occurred and by whom, but the approved record itself is gone and was never hashed into the chain. |

> **Why this happened, stated plainly.** The R3-2 pass-by-reference redesign was a genuine PHI-hygiene
> win — it keeps raw and masked content out of Step Functions state and is why the strict PHI canary
> passes with zero leaks. But it traded away record reconstructability, and no document acknowledged
> that trade until now. The fix is to hash the approved artifact (not just the approval metadata) into
> the finalize payload, and to retain that artifact for the record-retention period rather than the
> working-storage TTL.

## §11.100 — Electronic signatures, general requirements

| Element | Status | Notes |
|---|:--:|---|
| **(a)** Each electronic signature shall be unique to one individual and not reused by, or reassigned to, anyone else | ◑ | Identity comes from a verified IdP token; uniqueness depends on the sponsor's IdP lifecycle. Deprovisioning guidance is in `docs/Signoff-Identity-Baseline.md`. |
| **(b)** The organization shall verify the identity of the individual before establishing/certifying an electronic signature | ▣ | Sponsor-owned (IdP identity proofing). |
| **(c)** Certification to FDA that electronic signatures are intended to be the legally binding equivalent of handwritten signatures | ▣ | Sponsor-owned. Not mentioned anywhere in this accelerator, and correctly so — but the sponsor must not overlook it. |

## §11.200 — Electronic signature components and controls

| Element | Status | Notes |
|---|:--:|---|
| **(a)(1)** Non-biometric signatures shall employ **at least two distinct identification components** (e.g. user ID and password) | ❌ | Not implemented as a signing act. Approval is authorized by a **bearer access token**. Possession of a valid token is sufficient — the approver does not re-authenticate at the moment of signing. This is a materially weaker non-repudiation property than a two-component signature and should be described that way to a CISO. `docs/Signoff-Identity-Baseline.md` concedes that a step-up `auth_time`/ACR check is "the natural place to add" this once the sponsor's IdP emits it. |
| **(a)(2)** Used only by their genuine owners | ▣ | Sponsor-owned (credential hygiene, session policy). |
| **(a)(3)** Administered and executed to ensure attempted use by anyone other than the genuine owner requires collaboration of two or more individuals | ◑ | Separation of duties means two *different* verified identities are required across request and approval, which is a related but distinct control. |
| **(b)** Biometric signatures shall ensure they cannot be used by anyone other than their genuine owner | n/a | Not used. |

## §11.300 — Controls for identification codes / passwords

| Element | Status | Notes |
|---|:--:|---|
| Uniqueness, periodic revision, loss management, transaction safeguards, device testing | ▣ | Entirely delegated to the sponsor's IdP. The accelerator creates **zero users and zero passwords** (`cdk/pv_stacks/identity_stack.py`), enforces MFA in the pilot identity mode, and explicitly disables SMS MFA as not phishing-resistant. |

---

## Summary for a quality reviewer

**Strong:** §11.10(e) audit trail, §11.10(f) sequencing, §11.10(d)/(g) access and authority checks.
These are implemented, test-covered, and live-validated.

**Open gaps that are ours, not the sponsor's:** §11.70 signature-to-record binding, §11.50 signature
manifestation, §11.200(a)(1) two-component signing, §11.10(b)/(c) retention of the approved artifact.
None is architecturally hard; all are currently unimplemented.

**Correctly sponsor-owned:** validation (§11.10(a)), training (§11.10(i)), accountability policies
(§11.10(j)), identity proofing and FDA certification (§11.100(b)(c)), credential controls (§11.300).

---

## Sources

Primary regulation and guidance:

- 21 CFR Part 11 — Electronic Records; Electronic Signatures.
  https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11
- FDA, *Guidance for Industry: Part 11, Electronic Records; Electronic Signatures — Scope and
  Application* (2003). https://www.fda.gov/regulatory-information/search-fda-guidance-documents/part-11-electronic-records-electronic-signatures-scope-and-application
- FDA, *Computer Software Assurance for Production and Quality System Software* (final guidance,
  2025). https://www.fda.gov/regulatory-information/search-fda-guidance-documents/computer-software-assurance-production-and-quality-system-software
- EudraLex Volume 4, *Annex 11: Computerised Systems* — the EU counterpart to Part 11.
  https://health.ec.europa.eu/document/download/e1a2ee4c-a9dd-4b7b-9c8b-9e4a5e6d1b0f_en

Adverse-event reporting obligations referenced by the seriousness/clock engine:

- 21 CFR 314.80 — Postmarketing reporting of adverse drug experiences.
  https://www.ecfr.gov/current/title-21/chapter-I/subchapter-D/part-314/subpart-B/section-314.80
- 21 CFR 312.32 — IND safety reporting (note the 7-day fatal/life-threatening clock).
  https://www.ecfr.gov/current/title-21/chapter-I/subchapter-D/part-312/subpart-B/section-312.32
- ICH E2B(R3) — Electronic transmission of individual case safety reports.
  https://www.ich.org/page/efficacy-guidelines
- EMA, *Good Pharmacovigilance Practices (GVP) Module VI* — Collection, management and submission of
  reports of suspected adverse reactions. https://www.ema.europa.eu/en/human-regulatory-overview/post-authorisation/pharmacovigilance-post-authorisation/good-pharmacovigilance-practices-gvp

Retention:

- GVP Module I requires pharmacovigilance data be retained for the **lifetime of the product plus 10
  years** after the marketing authorisation ceases. The `production-reference` profile's 7-year
  default is **shorter than this** and must be reconfigured by any sponsor with EU obligations —
  see `docs/RETENTION-PROFILES.md`.

AWS platform references:

- AWS, *GxP Systems on AWS* (whitepaper).
  https://d1.awsstatic.com/whitepapers/compliance/Using_AWS_in_GxP_Systems.pdf
- AWS, *21 CFR Part 11 / EU Annex 11 compliance*.
  https://aws.amazon.com/compliance/gxp-part-11-annex-11/
- AWS, *HIPAA Eligible Services Reference*. https://aws.amazon.com/compliance/hipaa-eligible-services-reference/
- Amazon S3 Object Lock. https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html

> URLs were correct at the time of writing (2026-07-29) and regulatory sources are updated by their
> publishers; verify against the current text before relying on any citation.
