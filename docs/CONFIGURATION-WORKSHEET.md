# Configuration Worksheet — Pharmacovigilance ICSR Assistant

*Every sponsor/market-controlled value the assistant uses, with owner, where it is set, its authoritative
source, and a sign-off line. The seriousness thresholds and reporting clocks are **illustrative defaults**
— a QPPV/regulatory owner must confirm them per market and product before a pilot.*

---

## How configuration works

Two kinds of settings:

1. **Deploy-time switches** (CDK context) — posture: `env`, `retention_profile`, `kms=customer-managed`,
   `network_mode=private`, `identity_mode=pilot`, `tenant`. Owned by IT/security; on the `cdk deploy`
   command; documented in `DEPLOYMENT-GUIDE.md`.
2. **Regulatory constants** — the values below (in the rules engine). Each has a single home in code.

No configuration value is accepted from a request body at runtime.

## Sponsor / market-controlled values

| Value | Default | Owner | Where set | Authoritative source | Approved by |
|---|---|---|---|---|---|
| ICH E2B seriousness criteria | death · life-threatening · hospitalization · disability · congenital anomaly · other medically important | Safety/QPPV | `assess_seriousness.py::_CRITERIA` | ICH E2B(R3) / 21 CFR 314.80 | ☐ |
| Expedited reporting clock | 15 calendar days (serious + unexpected, postmarket) | Safety/QPPV | `assess_seriousness.py` | 21 CFR 314.80 / GVP (per market) | ☐ |
| Expectedness default | unknown → treated as unlisted (conservative → expedited) | Safety/QPPV | `assess_seriousness.py` | Product RSI / CCDS | ☐ |
| Duplicate-key fields | product \| event \| onset \| reporter | Safety/QPPV | `detect_duplicate.py` | Sponsor case-handling SOP | ☐ |
| Causality documentation | conclusion + case-specific rationale (required) | Safety/QPPV | `record_causality.py` (prepare-only) | GVP / 21 CFR | ☐ |
| Draft narrative style | CIOMS, ≤ 350 words, preserve `[REDACTED:…]` | Safety/QPPV | `pv_core.py::_SYSTEM` | Sponsor narrative SOP | ☐ |
| Retention profile | (deploy choice) | IT/security | CDK `-c retention_profile=…`; `docs/RETENTION-PROFILES.md` | Sponsor record-retention schedule | ☐ |
| Draft model id | Claude Sonnet (cross-region inference profile) | IT/security | `DRAFT_MODEL_ID` env | — | ☐ |

**openFDA/FAERS is reference context only** and never a case/causality source — not a configurable
determination input.

## Change procedure

Any change is a change-managed event: update the code constant, run the suite (`pytest tests/`), record
the approver, deploy through a tagged release. **Follow-on:** a machine-readable config file +
`test_config_schema.py` drift gate (as the financial-aid agent has) — noted in the readiness plan.

## Sign-off

We, the safety/QPPV office, confirm the values above reflect our SOPs and the applicable market
regulation for the pilot product(s).

QPPV / safety (name / title / date): __________________________
IT / security (name / title / date): __________________________
