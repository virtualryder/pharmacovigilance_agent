import json
import re

# assess_seriousness — deterministic ICH E2B(R3) / 21 CFR 314.80 seriousness assessment
# and regulatory reporting-clock determination for a de-identified adverse-event case.
#
# NO licensed data and NO model call: this is a rules engine over the ICH E2B(R3) seriousness
# criteria plus a configurable expedited/periodic reporting clock. It runs AFTER mask_phi
# (fail-closed: refuses un-masked input, mirroring draft_narrative), so it never sees raw PHI.
#
# Seriousness criteria (ICH E2B(R3) / 21 CFR 314.80 "serious adverse event"):
#   death, life-threatening, hospitalization (initial or prolonged), persistent/significant
#   disability or incapacity, congenital anomaly/birth defect, other medically important condition.
#
# Reporting clock (postmarket default, 21 CFR 314.80 / GVP). The intended-market thresholds and
# clocks are a customer configuration item (see the Regulatory-Adherence Guide) — this returns the
# widely-used default and flags the assumptions it made.

# keyword -> E2B seriousness criterion. Matches on the masked case text as a backstop; callers may
# also pass explicit boolean flags (which take precedence and avoid any text scan).
_CRITERIA = [
    ("death",             r"\b(death|died|deceased|fatal|fatality)\b"),
    ("life_threatening",  r"\blife[- ]threatening\b"),
    ("hospitalization",   r"\b(hospitali[sz]ed|hospitali[sz]ation|admitted to hospital|inpatient|icu|intensive care)\b"),
    ("disability",        r"\b(disabilit|incapacit|permanent (?:impairment|damage))\b"),
    ("congenital_anomaly", r"\b(congenital anomaly|birth defect|teratogen)\b"),
    ("medically_important", r"\b(medically important|required intervention|prevent permanent)\b"),
]

_CRITERION_LABEL = {
    "death": "Results in death",
    "life_threatening": "Life-threatening",
    "hospitalization": "Requires/prolongs inpatient hospitalization",
    "disability": "Persistent or significant disability/incapacity",
    "congenital_anomaly": "Congenital anomaly/birth defect",
    "medically_important": "Other medically important condition",
}


def _coerce(event):
    e = event or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {"_raw": e}
    return e


def _case_text(e):
    case = e.get("case", "")
    if not isinstance(case, str):
        case = json.dumps(case, ensure_ascii=False)
    return case


def _detect(e, text):
    """Return the ordered list of seriousness criteria met. Explicit flags override text scan."""
    flags = e.get("flags") or {}
    met = []
    low = text.lower()
    for key, pat in _CRITERIA:
        val = flags.get(key)
        if val is True:
            met.append(key)
        elif val is False:
            continue  # caller explicitly says this criterion is not met
        elif re.search(pat, low):
            met.append(key)
    return met


def handler(event, context):
    e = _coerce(event)

    # Fail-closed: like draft_narrative, refuse to operate on non-de-identified input. Cedar's
    # mask_before_assess forbid already blocks this at the gateway; the body refuses too (defense
    # in depth) so PHI is never assessed even if policy were misconfigured.
    if e.get("deidentified") is not True:
        return {"assessed": False,
                "error": "refused: case is not de-identified (deidentified must be true)",
                "deidentified_input": e.get("deidentified")}

    text = _case_text(e)
    met = _detect(e, text)
    serious = len(met) > 0

    # Expectedness / listedness of the reaction for the suspect product. Unknown -> treat as
    # unlisted (conservative: err toward expedited) and say so.
    expectedness = str(e.get("expectedness", "unknown")).strip().lower()
    unlisted = expectedness in ("unlisted", "unexpected", "unknown", "")
    assumed_unlisted = expectedness in ("unknown", "")

    if serious and unlisted:
        category = "EXPEDITED"
        clock_days = 15  # 21 CFR 314.80 postmarket default for serious + unexpected
    elif serious and not unlisted:
        category = "PERIODIC"          # serious but listed/expected -> aggregate (PSUR/PBRER)
        clock_days = None
    else:
        category = "ROUTINE"           # non-serious -> routine/periodic collection
        clock_days = None

    notes = []
    if assumed_unlisted:
        notes.append("expectedness unknown -> treated as unlisted (expedited); confirm listedness against the product's reference safety information")
    if serious and ("death" in met or "life_threatening" in met):
        notes.append("fatal/life-threatening + unexpected is a 7-day report under IND safety reporting (21 CFR 312.32); this returns the marketed-drug 15-day default")
    if category == "EXPEDITED":
        notes.append("clock runs in calendar days from first receipt of a valid ICSR (day 0)")

    # Short proof fields FIRST (the MCP client truncates long results ~200 chars); rationale LAST.
    return {
        "assessed": True,
        "serious": serious,
        "reporting_category": category,        # EXPEDITED | PERIODIC | ROUTINE
        "clock_days": clock_days,              # 15 for expedited; null otherwise
        "criteria_count": len(met),
        "deidentified_input": True,
        "expectedness": expectedness,
        "assessed_by": "rules:ICH-E2B(R3)/21CFR314.80",
        "criteria_met": [_CRITERION_LABEL[k] for k in met],
        "basis": ("serious per ICH E2B(R3): " + ", ".join(_CRITERION_LABEL[k] for k in met)
                  if serious else "no ICH E2B(R3) seriousness criterion met"),
        "notes": notes,
    }
