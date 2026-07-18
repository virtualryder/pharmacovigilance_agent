import json

# record_causality — PREPARE a documented causality / reportability determination. Under GVP and
# 21 CFR, the assessment of whether an adverse event is causally related to the suspect product, and
# whether it is expedited-reportable, is the highest-risk discretionary act in safety intake: it must
# be DOCUMENTED and it is a QUALIFIED-HUMAN decision (a senior safety physician). This tool only
# PREPARES the recommendation — it requires a written clinical rationale and returns a record that a
# DIFFERENT senior physician must approve. It never commits; committing a causality determination is
# forbidden to the agent (Cedar no_self_causality_commit) and goes through the human gate.
#
# Fail-closed: refuses non-de-identified input, and refuses without a documented rationale.


def _coerce(e):
    e = e or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {"_raw": e}
    return e


def handler(event, context):
    e = _coerce(event)
    if e.get("deidentified") is not True:
        return {"prepared": False, "error": "refused: case is not de-identified (deidentified must be true)",
                "deidentified_input": e.get("deidentified")}

    assessment = str(e.get("assessment", "")).strip()
    rationale = str(e.get("rationale", "")).strip()

    # Causality/reportability MUST be documented. No rationale -> refuse (documentation is required).
    if len(rationale) < 10:
        return {"prepared": False,
                "error": "refused: a causality/reportability determination requires a documented, case-specific clinical rationale",
                "requires": "rationale (>= 10 chars)"}
    if not assessment:
        return {"prepared": False, "error": "refused: a causality/reportability conclusion must be stated"}

    # Short proof fields FIRST (MCP client truncates ~200 chars).
    return {
        "prepared": True,
        "status": "PREPARED",                        # PREPARED (awaiting senior approval); never COMMITTED here
        "requires_senior_approval": True,            # a DIFFERENT senior safety physician must approve
        "committed": False,
        "deidentified_input": True,
        "assessment": assessment[:80],
        "rationale_recorded": True,
        "note": ("causality/reportability determination documented and PREPARED. A DIFFERENT senior safety "
                 "physician must approve; the agent cannot commit a causality determination (forbidden by policy)."),
    }
