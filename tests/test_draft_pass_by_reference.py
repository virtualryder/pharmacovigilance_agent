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
