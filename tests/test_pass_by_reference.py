"""R3-2 — pass-by-reference orchestration. Proves the runtime flow keeps raw + masked content OUT of the
values that would travel through Step Functions state: ingest stores raw text and returns an opaque
case_ref; intake + mask fetch by ref; mask (by ref) returns a sanitized_ref but NO masked_case; the
masked text is reachable only server-side via the sanitized-artifact store. Pure logic, no AWS
(Comprehend in mask_pii is monkeypatched; the stores are in-memory)."""
import sanitized
import case_store
from toolkit import call, load


def test_ingest_returns_opaque_ref_not_content():
    r = call("ingest_case", {"source": "Patient John Doe, DOB 1980-01-01, rash after amoxicillin", "case_id": "ICSR-1"})
    assert r["ingested"] is True
    ref = r["case_ref"]
    assert ref.startswith("case-") and "John Doe" not in str(r)          # content never echoed
    assert case_store.get_case(ref).startswith("Patient John Doe")       # fetchable server-side only


def test_intake_reads_by_ref():
    ref = case_store.put_case("Suspect product: atorvastatin. Patient hospitalized with rhabdomyolysis.")
    r = call("intake_icsr", {"case_ref": ref})
    assert r["fields"]["suspect_product"]                                 # extracted from the fetched text
    assert r["fields"]["seriousness_flags"]["hospitalization"] is True


def test_mask_by_ref_does_not_echo_masked_content(monkeypatch):
    """mask_pii called with a case_ref must return a sanitized_ref but NOT masked_case (so no masked
    content crosses state); the masked text is stored server-side and load_text retrieves it by ref."""
    mp = load("mask_pii")

    class _FakeComprehend:
        def detect_pii_entities(self, Text, LanguageCode):
            # pretend "John Doe" (offsets 0-8) is a NAME entity
            return {"Entities": [{"BeginOffset": 0, "EndOffset": 8, "Type": "NAME"}]}

    monkeypatch.setattr(mp.boto3, "client", lambda *_a, **_k: _FakeComprehend())
    st = sanitized.MemoryStore()
    monkeypatch.setattr(sanitized, "default_store", lambda: st)           # server-side artifact store
    ref = case_store.put_case("John Doe had a headache after the vaccine")
    out = mp.handler({"case_ref": ref}, None)
    assert out["deidentified"] is True
    assert "masked_case" not in out                                      # R3-2: no masked content in the response
    sref = out["sanitized_ref"]
    assert sanitized.verify_ref(sref)
    masked = sanitized.load_text(sref, store=st)                         # reachable ONLY server-side
    assert masked is not None and "[REDACTED:NAME]" in masked and "John Doe" not in masked
