"""P0-3 — openFDA background must never fabricate on source failure. On any egress error or an empty
result, openfda_lookup returns found:false / authoritative:false with NO invented FAERS aggregate (the
tool used to substitute reports_found:3 + a canned reaction list). Pure logic; the HTTP call is
monkeypatched."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "agents" / "pharmacovigilance" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _load():
    spec = importlib.util.spec_from_file_location("openfda_ut", TOOLS / "openfda_lookup.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_live_success_is_authoritative(monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "_live_lookup", lambda drug: ([{"term": "rhabdomyolysis", "count": 42},
                                                          {"term": "acute kidney injury", "count": 17}], 512))
    r = m.handler({"drug": "atorvastatin"}, None)
    assert r["found"] is True and r["authoritative"] is True
    assert "rhabdomyolysis" in r["top_reactions"] and r["reports_found"] == 512


def test_source_down_does_not_fabricate(monkeypatch):
    m = _load()
    def _boom(drug):
        raise TimeoutError("openFDA down")
    monkeypatch.setattr(m, "_live_lookup", _boom)
    r = m.handler({"drug": "atorvastatin"}, None)
    assert r["found"] is False and r["authoritative"] is False
    assert "top_reactions" not in r                      # no invented reaction list
    assert r.get("reports_found") in (None,)             # no invented count


def test_empty_result_does_not_fabricate(monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "_live_lookup", lambda drug: ([], None))
    r = m.handler({"drug": "nonexistent-drug"}, None)
    assert r["found"] is False and r["authoritative"] is False
    assert "top_reactions" not in r
    # the old canned fallback terms must never appear
    import json
    assert "rhabdomyolysis" not in json.dumps(r)
