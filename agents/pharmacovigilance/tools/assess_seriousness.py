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


def _coerce_flags(flags):
    """The manifest declares `flags` as a STRING ("Optional JSON of explicit seriousness booleans"), so
    the agent path hands us '{"hospitalization": true}' while the offline harness hands us a dict.
    Accept both; anything that is not a JSON object (malformed string, list, number, "null") means
    "no explicit flags" and the text scan decides. Found live 2026-09-03 (pv-mt e2e sweep): the
    Runtime agent's calls crashed here with AttributeError('str'.get) and Cedar/gateway surfaced them
    as isError rows - a fail-closed outcome, but a wrong one."""
    if isinstance(flags, str):
        try:
            flags = json.loads(flags) if flags.strip() else {}
        except Exception:
            return {}
    return flags if isinstance(flags, dict) else {}


def _detect(e, text):
    """Return the ordered list of seriousness criteria met. Explicit flags override text scan."""
    flags = _coerce_flags(e.get("flags"))
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


import tenancy  # noqa: E402  (phase 107: interceptor-injected, HMAC-signed tenant)
import telemetry  # noqa: E402  (phase 110: correlation keys -> one aegis.call log line per invocation)


@telemetry.instrument('assess_seriousness')
def handler(event, context):
    # Phase 107 (hybrid multi-tenant): bind the gateway-interceptor-injected, HMAC-SIGNED tenant for
    # per-tenant store routing. Unsigned/forged values are refused; multi-tenant mode fails closed.
    tenancy.bind_tenant_from_args(event)
    e = _coerce(event)

    # Fail-closed (P0-1): refuse unless masking is PROVEN by a mask_pii-signed sanitized_ref. Cedar's
    # mask_before_assess forbid remains as a coarse gateway gate; the authoritative control here is the
    # verified reference — a `deidentified: true` boolean is never accepted as proof.
    import sanitized
    ref = e.get("sanitized_ref")
    if not sanitized.verify_ref(ref):
        return {"assessed": False,
                "error": "refused: de-identification not proven (a valid sanitized_ref signed by mask_pii is required; a boolean is not proof)",
                "deidentified_input": e.get("deidentified")}

    # Content binding: only scan the case text if it hashes to the signed masked artifact. If it does
    # not bind (substituted/unmasked content), scan nothing — explicit seriousness flags still apply.
    raw = _case_text(e)
    text = sanitized.load_text(ref, candidate_text=raw) or ""
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
