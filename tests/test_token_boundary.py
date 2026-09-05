"""P0-3 — trusted runtime credential boundary + telemetry redaction (ported from the financial-aid agent).

Proves: (a) no tool schema in the manifest declares a credential field, so the model can never be asked
to supply one; (b) model-supplied credential-shaped args are scrubbed from EVERY tool call and the
runtime-held token is injected out-of-band into the sign-off call only; (c) the sign-off audit payload
and tool result never contain the bearer token (redaction). Pure logic, no AWS.

NOTE: wiring `wrap_mcp_client` into the live agent loop belongs to the runtime agent (pv-runtime), out of
this repo; this suite proves the boundary module + schema removal + redaction that live here."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib" / "runtime"))
sys.path.insert(0, str(ROOT / "lib" / "controls"))

import token_boundary  # noqa: E402

MANIFEST = (ROOT / "agents" / "pharmacovigilance" / "manifest.yaml").read_text(encoding="utf-8")
TOKEN = "eyJr.fake-live-bearer-token.sig"


# ── (a) no credential fields in any model-visible tool schema ────────────────

def test_no_credential_field_in_any_tool_schema():
    import re
    keys = re.findall(r"^\s{6,}(\w[\w-]*):\s*\{", MANIFEST, flags=re.M)
    bad = [k for k in keys if k.replace("-", "_").lower() in token_boundary.CREDENTIAL_FIELDS]
    assert bad == [], f"credential-shaped tool inputs must not exist (P0-3): {bad}"
    assert "access_token: { type: string" not in MANIFEST


# ── (b) scrub + inject at the boundary ───────────────────────────────────────

def test_scrub_drops_model_supplied_credentials_everywhere():
    args, dropped = token_boundary.scrub_args(
        {"case": "x", "access_token": "stolen", "Api-Key": "k", "password": "p", "n": 3})
    assert args == {"case": "x", "n": 3}
    assert sorted(dropped) == ["Api-Key", "access_token", "password"]


def test_inject_only_for_signoff_and_overrides_model_token():
    a = token_boundary.prepare_args("assess-seriousness___assess_seriousness",
                                    {"case": "x", "access_token": "stolen"}, TOKEN)
    assert "access_token" not in a
    a = token_boundary.prepare_args("request-signoff___request_signoff",
                                    {"icsr_id": "ICSR-1", "access_token": "stolen"}, TOKEN)
    assert a["access_token"] == TOKEN and a["icsr_id"] == "ICSR-1"


def test_wrap_intercepts_positional_and_keyword_calls():
    calls = []

    class _FakeClient:
        def call_tool_sync(self, tool_use_id, name, arguments=None):
            calls.append((name, arguments))
            return {"ok": True}

    c = token_boundary.wrap_mcp_client(_FakeClient(), TOKEN)
    c.call_tool_sync("t1", "request-signoff___request_signoff", {"icsr_id": "A"})
    c.call_tool_sync("t2", name="pv-core___draft_narrative", arguments={"case": "x", "token": "stolen"})
    assert calls[0][1]["access_token"] == TOKEN
    assert "token" not in calls[1][1] and "access_token" not in calls[1][1]


# ── (c) redaction: token never in audit payload or tool result ───────────────

def test_signoff_audit_payload_contains_no_token(monkeypatch):
    from toolkit import load
    rs = load("request_signoff")
    recorded = {}
    monkeypatch.setattr(rs.evidence, "record_event",
                        lambda ev, ctx, source=None: recorded.update(ev) or {"stored": True, "worm": True})
    monkeypatch.setattr(rs.identity, "verify_access_token",
                        lambda tok, require_group=True: ({"username": "pv_reviewer", "sub": "u-1"}, None))

    class _Ctx:
        invoked_function_arn = "arn:aws:lambda:us-east-1:111122223333:function:f"

    class _FakeSfn:
        def start_execution(self, **kw):
            assert TOKEN not in kw["input"], "token must not enter the state-machine input"
            return {"executionArn": "arn:exec"}

    import boto3
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: _FakeSfn())
    out = rs.handler({"icsr_id": "ICSR-1", "access_token": TOKEN}, _Ctx())
    assert out["requested"] is True
    assert TOKEN not in json.dumps(recorded), "bearer token leaked into the audit payload"
    assert TOKEN not in json.dumps(out), "bearer token leaked into the tool result"


# ── P0-7 companion: no role-lookup-by-name-prefix in deploy scripts ──────────

def test_no_role_lookup_by_name_prefix_in_deploy_paths():
    bad = []
    for d in ("lib/engine", "lib/runtime", "lib/connector"):
        dp = ROOT / d
        if not dp.is_dir():
            continue
        for p in dp.glob("*.sh"):
            if "starts_with(RoleName" in p.read_text(encoding="utf-8", errors="ignore"):
                bad.append(str(p.relative_to(ROOT)))
    assert bad == [], f"role lookup by name prefix is forbidden (P0-7): {bad}"
