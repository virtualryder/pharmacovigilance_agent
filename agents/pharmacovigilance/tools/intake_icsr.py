import json
import re

# intake_icsr — extract the decision-relevant, NON-PHI fields from a raw adverse-event source
# (E2B/CIOMS free text or JSON): suspect product, adverse-event term(s), ICH E2B seriousness flags,
# expectedness. Deterministic and fail-soft. PHI (patient name, DOB, address, identifiers, contact
# info) is NOT needed downstream for the seriousness/reporting determination and is redacted
# separately by mask_pii before assessment, drafting, and audit.

_SERIOUS = {
    "death": r"\b(death|died|deceased|fatal|fatality)\b",
    "life_threatening": r"\blife[- ]threatening\b",
    "hospitalization": r"\b(hospitali[sz]ed|hospitali[sz]ation|inpatient|icu|intensive care)\b",
    "disability": r"\b(disabilit|incapacit|permanent (?:impairment|damage))\b",
    "congenital_anomaly": r"\b(congenital anomaly|birth defect|teratogen)\b",
    "medically_important": r"\b(medically important|required intervention)\b",
}


def _coerce(e):
    e = e or {}
    if isinstance(e, str):
        try:
            return json.loads(e)
        except Exception:
            return {"source": e}
    return e


def handler(event, context):
    e = _coerce(event)
    text = e.get("source", "")
    if not isinstance(text, str):
        text = json.dumps(text)
    low = text.lower()

    drug = e.get("drug") or e.get("suspect_product")
    if not drug:
        m = re.search(r"(?:suspect(?:\s+product|\s+drug)?|drug|medicinal product|product)[^A-Za-z0-9]{0,6}([A-Za-z][A-Za-z0-9\- ]{2,40})", low)
        drug = m.group(1).strip() if m else None
    event_terms = e.get("event_terms")
    if not event_terms:
        m = re.search(r"(?:adverse event|reaction|ae|event)[^A-Za-z]{0,6}([A-Za-z][A-Za-z0-9\-, ]{2,60})", low)
        event_terms = m.group(1).strip() if m else None
    flags = {k: bool(re.search(pat, low)) for k, pat in _SERIOUS.items()}
    expectedness = e.get("expectedness")
    if not expectedness:
        if re.search(r"\b(unlisted|unexpected)\b", low):
            expectedness = "unlisted"
        elif re.search(r"\b(listed|expected)\b", low):
            expectedness = "listed"
        else:
            expectedness = "unknown"

    fields = {"suspect_product": drug, "event_terms": event_terms,
              "seriousness_flags": flags, "expectedness": expectedness}
    missing = [k for k in ("suspect_product",) if not fields.get(k)]
    return {"structured": True, "fields": fields, "missing_required": missing,
            "note": "non-PHI decision fields; PHI is redacted separately by mask_pii"}
