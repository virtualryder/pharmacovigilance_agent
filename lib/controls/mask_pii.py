import json
import boto3
from botocore.exceptions import BotoCoreError, ClientError

import sanitized  # server-issued sanitized-artifact references (P0-1; bundled beside this handler)

# mask_pii — fail-closed PHI/PII de-identification via Amazon Comprehend DetectPiiEntities
# (name, SSN, address, DOB, phone, email, bank/routing, etc.). FAIL-CLOSED: if detection cannot run,
# NO masked text is returned and deidentified=false — nothing downstream may proceed.
#
# P0-1: on success this control MINTS a SIGNED `sanitized_ref` over the exact masked content
# (see lib/controls/sanitized.py). Downstream tools authorize on the VERIFIED reference — the
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

def handler(event, context):
    e = _coerce(event)
    # R3-2 pass-by-reference: prefer an opaque case_ref (server-side fetch; raw content never travels
    # through Step Functions state). When input arrived by ref, the masked text is NOT echoed in the
    # response either — consumers load it server-side from the sanitized-artifact store via the ref.
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
        cm = boto3.client("comprehend")
        ents = cm.detect_pii_entities(Text=case[:99000], LanguageCode="en").get("Entities", [])
    except (BotoCoreError, ClientError) as exc:
        # Fail-closed: never emit unmasked text if detection fails.
        return {"deidentified": False, "masked_case": None,
                "error": "pii detection failed: %s" % type(exc).__name__}
    # redact spans back-to-front so offsets stay valid
    spans = sorted(ents, key=lambda x: x.get("BeginOffset", 0), reverse=True)
    masked = case
    for ent in spans:
        b, end = ent.get("BeginOffset"), ent.get("EndOffset")
        t = ent.get("Type", "PII")
        if b is None or end is None:
            continue
        masked = masked[:b] + ("[REDACTED:%s]" % t) + masked[end:]
    # P0-1: mint a SIGNED reference over the exact masked content; downstream tools verify this ref,
    # never the boolean. When SANITIZED_TABLE is configured the masked payload is stored server-side.
    ref = sanitized.mint_ref(masked, engine="comprehend:DetectPiiEntities", entities_masked=len(ents))
    out = {"deidentified": True, "entities_masked": len(ents),
           "masked_by": "comprehend:DetectPiiEntities", "sanitized_ref": ref,
           "note": ("pass sanitized_ref (JSON) to assess_seriousness/record_causality/draft_narrative — "
                    "it is the server-signed proof of masking; the deidentified boolean alone is not accepted")}
    # R3-2: in pass-by-reference mode NO masked content returns into Step Functions state — consumers
    # load the masked text server-side from the sanitized-artifact store via the signed ref.
    if not by_ref:
        out["masked_case"] = masked
    return out
