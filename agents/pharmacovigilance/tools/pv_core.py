import json
import os
import boto3
from botocore.exceptions import BotoCoreError, ClientError

# PV core tools behind the `pv-core` Gateway target:
#   - draft_narrative      -> REAL Bedrock (Converse) CIOMS/ICSR narrative from a de-identified case
#   - finalize_submission  -> deny-only stub (the human sign-off gate owns real submission)
#   - commit_causality     -> deny-only stub (a senior-safety-physician decision)
# Branch on the input shape (finalize carries icsr_id; commit_causality carries causality_id; draft carries case/deidentified).

DRAFT_MODEL_ID = os.environ.get("DRAFT_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT")

_SYSTEM = (
    "You are a pharmacovigilance medical writer drafting an ICSR (Individual Case Safety Report) "
    "narrative in CIOMS style. You are given an ALREADY DE-IDENTIFIED adverse-event case. "
    "Write a single, concise clinical narrative (roughly 150-350 words). Rules: "
    "(1) Preserve every [REDACTED:...] placeholder verbatim; never guess or reconstruct redacted values. "
    "(2) Never invent patient identifiers, dates, or facts not present in the case. "
    "(3) Cover, when available: reporter/source, de-identified patient descriptors, suspect product and dosing, "
    "adverse event(s) with onset and timeline, clinical course, outcome, and seriousness/causality as reported. "
    "(4) Output the narrative text only - no preamble, headings, or JSON."
)


def _coerce(event):
    e = event or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {"_raw": e}
    return e


def _draft(e):
    # P0-1: draft ONLY from content proven de-identified by a mask_pii-signed sanitized_ref, and only
    # if the case text binds to the signed digest (the model cannot substitute unmasked content).
    import sanitized
    ref = e.get("sanitized_ref")
    if not sanitized.verify_ref(ref):
        return {"error": "refused: de-identification not proven - a valid sanitized_ref signed by mask_pii is required (a boolean is not proof)",
                "drafted_by": None, "deidentified_input": e.get("deidentified")}
    raw_case = e.get("case", "")
    if not isinstance(raw_case, str):
        raw_case = json.dumps(raw_case, ensure_ascii=False)
    case = sanitized.load_text(ref, candidate_text=raw_case)
    if case is None:
        return {"error": "refused: case content does not match the signed sanitized artifact",
                "drafted_by": None, "sanitized_ref_verified": True, "content_bound": False}
    kwargs = dict(
        modelId=DRAFT_MODEL_ID,
        system=[{"text": _SYSTEM}],
        messages=[{"role": "user", "content": [{"text": "De-identified case:\n" + case}]}],
        inferenceConfig={"maxTokens": 900, "temperature": 0.2},
    )
    if GUARDRAIL_ID:
        kwargs["guardrailConfig"] = {"guardrailIdentifier": GUARDRAIL_ID, "guardrailVersion": GUARDRAIL_VERSION}
    try:
        br = boto3.client("bedrock-runtime")
        resp = br.converse(**kwargs)
        narrative = resp["output"]["message"]["content"][0]["text"].strip()
        if resp.get("stopReason") == "guardrail_intervened" and not narrative:
            return {"error": "output guardrail blocked the draft (fail-closed)", "drafted_by": None, "guardrail": "BLOCKED"}
        return {"drafted_by": DRAFT_MODEL_ID, "chars": len(narrative),
                "guardrail_applied": bool(GUARDRAIL_ID), "deidentified_input": True, "narrative": narrative}
    except (BotoCoreError, ClientError, KeyError, IndexError) as exc:
        return {"error": "draft failed: " + type(exc).__name__ + ": " + str(exc), "drafted_by": None}


def handler(event, context):
    e = _coerce(event)
    if "causality_id" in e:
        # commit_causality is a consequential, HUMAN-ONLY discretionary action. The agent can never
        # commit a causality/reportability determination; a senior safety physician does, through the
        # human gate. Forbidden to the agent by Cedar (no_self_causality_commit); refused here too.
        return {"error": "refused: committing a causality/reportability determination is a senior-safety-physician decision; the agent cannot commit",
                "causality_id": e.get("causality_id"), "committed": False}
    if "icsr_id" in e and "case" not in e:
        # finalize_submission is never a real inline call — the human sign-off gate owns it.
        return {"error": "refused: finalize_submission must go through the human sign-off gate",
                "icsr_id": e.get("icsr_id"), "submitted": False}
    if "case" in e or "deidentified" in e or "sanitized_ref" in e:
        return _draft(e)
    return {"ok": True, "received": e, "note": "pv core tool"}
