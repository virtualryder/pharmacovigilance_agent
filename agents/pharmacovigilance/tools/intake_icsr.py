import json
import re

import negation

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


import tenancy  # noqa: E402  (phase 107: interceptor-injected, HMAC-signed tenant)
import telemetry  # noqa: E402  (phase 110: correlation keys -> one aegis.call log line per invocation)


@telemetry.instrument('intake_icsr')
def handler(event, context):
    # Phase 107 (hybrid multi-tenant): bind the gateway-interceptor-injected, HMAC-SIGNED tenant for
    # per-tenant store routing. Unsigned/forged values are refused; multi-tenant mode fails closed.
    tenancy.bind_tenant_from_args(event)
    e = _coerce(event)
    # R3-2 pass-by-reference: accept an opaque case_ref and fetch the raw source server-side, so raw
    # content never travels through Step Functions state (extraction yields only non-PHI decision fields).
    text = e.get("source", "")
    if not text and e.get("case_ref"):
        import case_store
        text = case_store.get_case(e["case_ref"]) or ""
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
    # L20b (the L20 class, found on benefits 2026-09-06): these were NEGATION-BLIND. "no
    # hospitalization required" matched `hospitalization` and flagged the case as serious, and
    # "not life-threatening" flagged life_threatening.
    #
    # The DIRECTION matters and is called out deliberately: for pharmacovigilance, over-flagging
    # seriousness is the SAFE error - it escalates a case to a human and, at worst, files an
    # expedited report that was not required. Under-flagging would miss a reportable death. So this
    # was never a patient-safety bug, but it is still wrong data: a case narrative that rules a
    # criterion OUT must not be recorded as ruling it IN, or the ICSR misstates the reporter's own
    # account and the seriousness determination cannot be defended to an inspector.
    #
    # negation.asserted() is fail-closed for ASSERTIONS, so it will not invent seriousness; a
    # genuinely ambiguous narrative still reaches a qualified reviewer through the normal path.
    flags = negation.asserted_flags(low, _SERIOUS)
    expectedness = e.get("expectedness")
    if not expectedness:
        # "not unexpected" must not read as unlisted, and "not listed" must not read as listed.
        if negation.asserted(low, r"\b(unlisted|unexpected)\b"):
            expectedness = "unlisted"
        elif negation.asserted(low, r"\b(listed|expected)\b"):
            expectedness = "listed"
        else:
            expectedness = "unknown"

    fields = {"suspect_product": drug, "event_terms": event_terms,
              "seriousness_flags": flags, "expectedness": expectedness}
    missing = [k for k in ("suspect_product",) if not fields.get(k)]
    return {"structured": True, "fields": fields, "missing_required": missing,
            "note": "non-PHI decision fields; PHI is redacted separately by mask_pii"}
