"""R3-2 (draft output): the CIOMS narrative must NEVER be returned as text by the drafter — only an
opaque, verifiable sanitized_ref. This is the control the live strict PHI canary exercises: even if a
redaction gap leaves PHI in the drafted text, that text does not enter Step Functions state, the pending
record, or any telemetry, because the tool returns a reference and stores the text server-side.

Regression guard for the leak the pv-val1 EP1 canary caught (narrative text in DraftNarrative's
TaskSucceeded output flowing into AuditIntent / HumanSignoff execution history)."""
import json

from toolkit import load, make_sanitized_ref

CASE = "[REDACTED:NAME] hospitalized with rhabdomyolysis after atorvastatin"
# a marker that would survive masking (as a synthetic token can) — it must NOT appear in the tool output
NARR = "A report was received concerning [REDACTED:NAME] CANARY_LEAKMARKER_XYZ; clinical course narrative."


class _FakeBedrock:
    def converse(self, **kw):
        return {"output": {"message": {"content": [{"text": NARR}]}}, "stopReason": "end_turn"}


def test_draft_returns_ref_never_text(monkeypatch):
    pv = load("pv_core")
    monkeypatch.setattr(pv.boto3, "client", lambda *a, **k: _FakeBedrock())
    r = pv.handler({"sanitized_ref": make_sanitized_ref(CASE), "case": CASE, "deidentified": True}, None)

    # the drafted narrative text is returned NOWHERE in the tool response
    assert "narrative" not in r, "raw narrative text must not be a response field"
    assert "CANARY_LEAKMARKER_XYZ" not in json.dumps(r), "narrative text leaked into the tool response"

    # instead: an opaque, verifiable ref (proof-of-masking signature over the exact drafted text)
    import sanitized
    ref = r["narrative_ref"]
    assert sanitized.verify_ref(ref), "narrative_ref must be a genuine mask_pii-signed reference"

    # hash binding: the ref binds to EXACTLY the drafted narrative — retrievable server-side by ref+content,
    # and a substituted text cannot satisfy the signed digest
    assert sanitized.load_text(ref, candidate_text=NARR) == NARR
    assert sanitized.load_text(ref, candidate_text="tampered narrative") is None


def test_draft_still_fail_closed_without_proof():
    pv = load("pv_core")
    r = pv.handler({"case": "unmasked PHI here", "deidentified": True}, None)  # no valid sanitized_ref
    assert r.get("drafted_by") is None
    assert "narrative_ref" not in r


ASSESSMENT = {"assessed": True, "serious": True, "reporting_category": "EXPEDITED", "clock_days": 15,
              "criteria_count": 1, "criteria": ["hospitalization"], "assessed_by": "rules:ICH-E2A",
              "notes": ["SMUGGLED free text must not reach the grounding source"]}


def test_draft_grounds_on_case_plus_assessment_when_guardrail_bound(monkeypatch):
    """#190 / L14 (PAR-1 port): with a guardrail bound, the narrative is generated as a grounded restatement
    - the de-identified case PLUS the deterministic seriousness assessment (allowlisted fields only) is the
    grounding_source and a query is present, so contextual grounding scores every clinical claim."""
    pv = load("pv_core")
    seen = {}

    class _Spy(_FakeBedrock):
        def converse(self, **kw):
            seen.update(kw)
            return super().converse(**kw)
    monkeypatch.setattr(pv.boto3, "client", lambda *a, **k: _Spy())
    monkeypatch.setattr(pv, "GUARDRAIL_ID", "gr-pv123")
    monkeypatch.setattr(pv, "GUARDRAIL_VERSION", "1")
    r = pv.handler({"sanitized_ref": make_sanitized_ref(CASE), "case": CASE, "deidentified": True,
                    "assessment": ASSESSMENT}, None)
    assert seen.get("guardrailConfig") == {"guardrailIdentifier": "gr-pv123", "guardrailVersion": "1"}
    assert seen["system"] == [{"text": pv._SYSTEM_GROUNDED}]
    blocks = seen["messages"][0]["content"]
    quals = [q for b in blocks for q in b.get("guardContent", {}).get("text", {}).get("qualifiers", [])]
    assert "grounding_source" in quals and "query" in quals
    src = [b["guardContent"]["text"]["text"] for b in blocks
           if "grounding_source" in b.get("guardContent", {}).get("text", {}).get("qualifiers", [])][0]
    assert CASE in src and "serious=True" in src and "reporting_category=EXPEDITED" in src and "clock_days=15" in src
    assert "SMUGGLED" not in src
    assert r.get("guardrail_applied") is True and "narrative_ref" in r


def test_draft_fail_closed_on_any_guardrail_intervention(monkeypatch):
    """ANY intervention is fail-closed - including the guardrail's substituted non-empty blocked message
    (the old `and not narrative` condition would have minted a ref for it)."""
    pv = load("pv_core")

    class _Blocked(_FakeBedrock):
        def converse(self, **kw):
            return {"output": {"message": {"content": [{"text": "[Output withheld by the Aegis pharmacovigilance guardrail.]"}]}},
                    "stopReason": "guardrail_intervened"}
    monkeypatch.setattr(pv.boto3, "client", lambda *a, **k: _Blocked())
    monkeypatch.setattr(pv, "GUARDRAIL_ID", "gr-pv123")
    monkeypatch.setattr(pv, "GUARDRAIL_VERSION", "1")
    r = pv.handler({"sanitized_ref": make_sanitized_ref(CASE), "case": CASE, "deidentified": True}, None)
    assert r.get("guardrail") == "BLOCKED" and r.get("drafted_by") is None and "narrative_ref" not in r
    # the workflow passes the assessment output with the signed ref (the production path)
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parents[1] / "cdk" / "pv_stacks" / "workflow_stack.py").read_text(encoding="utf-8")
    assert '"assessment.$": "$.assessment.out"' in wf
