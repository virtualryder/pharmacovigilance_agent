import json
from botocore.exceptions import BotoCoreError, ClientError

import sanitized  # server-issued sanitized-artifact references (P0-1; bundled beside this handler)
import pii_detect  # SHARED detector promoted to governed-core 1.10.0 (byte-window chunking + regex backstop)

# mask_pii - fail-closed PHI/PII de-identification. The DETECTION logic lives in the shared
# governed-core `pii_detect` module (Amazon Comprehend DetectPiiEntities as the primary detector, plus
# a deterministic regex backstop for structured identifiers, with UTF-8 byte-window chunking so text
# beyond Comprehend's synchronous size limit is never returned unmasked). Keeping detection in ONE
# place is deliberate: this repo used to carry its own inline Comprehend call, which is exactly how a
# masking fix could land in one vertical and never reach the others. FAIL-CLOSED: if detection cannot
# run, NO masked text is returned and deidentified=false - nothing downstream may proceed.
#
# P0-1: on success this control MINTS a SIGNED `sanitized_ref` over the exact masked content
# (see lib/controls/sanitized.py). Downstream tools authorize on the VERIFIED reference - the
# `deidentified` boolean is retained only as the coarse Cedar gateway gate and is never accepted as
# proof by any tool.

def _coerce(e):
    e = e or {}
    if isinstance(e, str):
        try:
            return json.loads(e)
        except Exception:
            return {"case": e}
    return e

import tenancy  # noqa: E402  (phase 107: interceptor-injected, HMAC-signed tenant)
import telemetry  # noqa: E402  (phase 110: correlation keys -> one aegis.call log line per invocation)


@telemetry.instrument('mask_pii')
def handler(event, context):
    # Phase 107 (hybrid multi-tenant): bind the gateway-interceptor-injected, HMAC-SIGNED tenant for
    # per-tenant store routing. Unsigned/forged values are refused; multi-tenant mode fails closed.
    tenancy.bind_tenant_from_args(event)
    e = _coerce(event)
    # R3-2 pass-by-reference: prefer an opaque case_ref (server-side fetch; raw content never travels
    # through Step Functions state). When input arrived by ref, the masked text is NOT echoed in the
    # response either - consumers load it server-side from the sanitized-artifact store via the ref.
    by_ref = False
    case = e.get("case", e.get("source", ""))
    if not case and e.get("case_ref"):
        import case_store
        case = case_store.get_case(e["case_ref"]) or ""
        by_ref = True
        if not case:
            return {"deidentified": False, "masked_case": None,
                    "error": "case_ref unresolved (unknown ref) - fail-closed"}
    if not isinstance(case, str):
        case = json.dumps(case, ensure_ascii=False)
    if not case.strip():
        return {"deidentified": False, "masked_case": None, "error": "empty input"}
    try:
        # SHARED detector: every UTF-8 byte-window is masked (no unmasked tail past Comprehend's size
        # limit) and a regex backstop covers SSN/EMAIL/PHONE/IP/CARD. redact() RAISES the Comprehend
        # error, so a detector failure fails closed right here.
        masked, meta = pii_detect.redact(case)
    except (BotoCoreError, ClientError) as exc:
        # Fail-closed: never emit unmasked text if detection fails.
        return {"deidentified": False, "masked_case": None,
                "error": "pii detection failed: %s" % type(exc).__name__}
    # P0-1: mint a SIGNED reference over the exact masked content; downstream tools verify this ref,
    # never the boolean. When SANITIZED_TABLE is configured the masked payload is stored server-side.
    ref = sanitized.mint_ref(masked, engine=meta["masked_by"], entities_masked=meta["entities_masked"])
    out = {"deidentified": True, "entities_masked": meta["entities_masked"],
           "comprehend_entities": meta["comprehend_entities"], "regex_backstop": meta["regex_backstop"],
           "masked_by": meta["masked_by"], "sanitized_ref": ref,
           "note": ("pass sanitized_ref (JSON) to assess_seriousness/record_causality/draft_narrative - "
                    "it is the server-signed proof of masking; the deidentified boolean alone is not accepted")}
    # R3-2: in pass-by-reference mode NO masked content returns into Step Functions state - consumers
    # load the masked text server-side from the sanitized-artifact store via the signed ref.
    if not by_ref:
        out["masked_case"] = masked
    return out
