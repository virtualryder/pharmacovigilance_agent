"""Phase 107 routing correction — the gateway REQUEST interceptor derives the tenant from the VALIDATED
JWT and injects it as an HMAC-signed reserved pair; the target trusts it only if the signature verifies.

Proves: (a) tools/call with a tenanted identity gets a signed injection the target can verify;
(b) a caller/model-supplied tenant (and a forged signature) is OVERWRITTEN; (c) multi-tenant + no
tenant on the identity -> 403 deny and the target is never reached; (d) tools/list and silo mode pass
through unchanged; (e) verify/bind refuse a bad signature and bind the routing context on success.
Pure stdlib, no AWS. AgentCore interceptor contract v1.0."""
import base64
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
import governed_core  # noqa: E402,F401  (tenancy ships in governed-core >= 1.6.0: flat-import path)
sys.path.insert(0, str(ROOT / "lib" / "controls"))
import tenancy  # noqa: E402
import tenant_interceptor as ti  # noqa: E402

SECRET = "unit-provenance-secret"


def _jwt(claims):
    h = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
    b = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{h}.{b}.sig"


def _event(method="tools/call", args=None, token=None, rid=7):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if method == "tools/call":
        body["params"] = {"name": "pv-core___draft_narrative", "arguments": dict(args or {})}
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return {"interceptorInputVersion": "1.0",
            "mcp": {"gatewayRequest": {"path": "/mcp", "httpMethod": "POST",
                                       "headers": headers, "body": body}}}


def test_tools_call_injects_signed_tenant_target_can_verify():
    tok = _jwt({"sub": "u1", "custom:tenant": "pha-oakland"})
    out = ti.build_output(_event(args={"case": "x", "deidentified": True}, token=tok), SECRET, True)
    assert "transformedGatewayResponse" not in out["mcp"]          # not denied
    args = out["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]
    assert args["case"] == "x" and args["deidentified"] is True    # original args intact
    assert args[tenancy.TENANT_FIELD] == "pha-oakland"
    assert tenancy.verified_tenant_from_args(args, SECRET) == "pha-oakland"


def test_caller_supplied_tenant_and_forged_sig_are_overwritten():
    tok = _jwt({"custom:tenant": "pha-real"})
    forged = {tenancy.TENANT_FIELD: "pha-evil",
              tenancy.TENANT_SIG_FIELD: tenancy.sign_tenant("pha-evil", SECRET)}
    out = ti.build_output(_event(args=forged, token=tok), SECRET, True)
    args = out["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]
    assert args[tenancy.TENANT_FIELD] == "pha-real"                 # identity wins, always
    assert tenancy.verified_tenant_from_args(args, SECRET) == "pha-real"


def test_multitenant_no_tenant_is_denied_before_target():
    tok = _jwt({"sub": "u1"})                                       # no custom:tenant
    out = ti.build_output(_event(token=tok), SECRET, True)
    resp = out["mcp"]["transformedGatewayResponse"]
    assert resp["statusCode"] == 403
    assert resp["body"]["id"] == 7 and resp["body"]["error"]["code"] == -32000
    # no bearer at all is denied too
    assert "transformedGatewayResponse" in ti.build_output(_event(), SECRET, True)["mcp"]


def test_tools_list_and_silo_pass_through_unchanged():
    lst = _event(method="tools/list")
    out = ti.build_output(lst, SECRET, True)
    assert out["mcp"]["transformedGatewayRequest"]["body"] == lst["mcp"]["gatewayRequest"]["body"]
    # silo mode: an un-tenanted tools/call passes through with NO injection
    out = ti.build_output(_event(args={"case": "x"}), SECRET, False)
    args = out["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]
    assert tenancy.TENANT_FIELD not in args and args == {"case": "x"}


def test_verify_rejects_bad_sig_and_bind_sets_routing_context(monkeypatch):
    good = {tenancy.TENANT_FIELD: "pha-alameda",
            tenancy.TENANT_SIG_FIELD: tenancy.sign_tenant("pha-alameda", SECRET)}
    bad = dict(good, **{tenancy.TENANT_SIG_FIELD: "deadbeef" * 8})
    assert tenancy.verified_tenant_from_args(bad, SECRET) is None       # forged/unsigned -> refused
    assert tenancy.verified_tenant_from_args(good, "wrong-key") is None  # wrong trust domain -> refused
    assert tenancy.verified_tenant_from_args({}, SECRET) is None
    # bind: on success the routing context carries the tenant (multi-tenant routing works)
    monkeypatch.setenv("MULTITENANT", "1")
    try:
        assert tenancy.bind_tenant_from_args(good, SECRET) == "pha-alameda"
        assert tenancy.resolve_tenant() == "pha-alameda"
        assert tenancy.route_store("pv-e2e-case-store", "case-store") == "pv-e2e-pha-alameda-case-store"
        # a failed bind clears the context -> fail-closed
        assert tenancy.bind_tenant_from_args(bad, SECRET) is None
        with pytest.raises(tenancy.TenantError):
            tenancy.resolve_tenant()
    finally:
        tenancy.clear_request_claims()
