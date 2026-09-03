"""Phase 107 — tenant identity is DERIVED, never REQUESTED, in BOTH silo and multi-tenant modes.

Proves: (a) silo mode returns the pinned TENANT_ID and ignores any request body; (b) multi-tenant mode
derives the tenant from the VERIFIED custom:tenant claim; (c) multi-tenant mode is FAIL-CLOSED when the
claim is absent/blank; (d) a body-supplied `tenant` is ignored in both modes; (e) cross-tenant refs are
refused; (f) tenant_from_bearer reads the claim from a JWT payload without verifying. Pure stdlib, no AWS.
"""
import base64
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
import governed_core  # noqa: E402,F401  (tenancy ships in governed-core >= 1.6.0: flat-import path)
sys.path.insert(0, str(ROOT / "lib" / "controls"))
import tenancy  # noqa: E402


def _reset(monkeypatch, *, mt=False, pinned=None):
    tenancy.clear_request_claims()
    monkeypatch.delenv("MULTITENANT", raising=False)
    monkeypatch.delenv("TENANT_ID", raising=False)
    if mt:
        monkeypatch.setenv("MULTITENANT", "1")
    if pinned is not None:
        monkeypatch.setenv("TENANT_ID", pinned)


def _jwt(claims):
    hdr = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{hdr}.{body}.sig"


def test_silo_returns_pinned_and_ignores_body(monkeypatch):
    _reset(monkeypatch, pinned="pha-alameda")
    assert tenancy.resolve_tenant() == "pha-alameda"
    # a request body claiming another tenant is ignored
    assert tenancy.resolve_tenant({"tenant": "pha-evil"}) == "pha-alameda"


def test_silo_default_when_unset(monkeypatch):
    _reset(monkeypatch)
    assert tenancy.resolve_tenant() == tenancy.DEFAULT_TENANT


def test_multitenant_derives_from_verified_claim(monkeypatch):
    _reset(monkeypatch, mt=True, pinned="pha-ignored-in-mt")
    claims = {"sub": "u1", "custom:tenant": "pha-oakland", "cognito:groups": "pv_reviewer"}
    assert tenancy.resolve_tenant(claims=claims) == "pha-oakland"
    # the pinned env is NOT used in multi-tenant mode
    assert tenancy.resolve_tenant(claims=claims) != "pha-ignored-in-mt"
    # tenant membership as a Cognito GROUP (access tokens carry cognito:groups, not custom attrs)
    assert tenancy.resolve_tenant(claims={"cognito:groups": ["pv_reviewer", "tenant_pha-alameda"]}) == "pha-alameda"
    assert tenancy.resolve_tenant(claims={"cognito:groups": "pv_reviewer tenant_pha-fresno"}) == "pha-fresno"
    # custom:tenant takes precedence when both are present; a non-tenant group alone is not a tenant
    assert tenancy.resolve_tenant(claims={"custom:tenant": "pha-x", "cognito:groups": ["tenant_pha-y"]}) == "pha-x"
    with pytest.raises(tenancy.TenantError):
        tenancy.resolve_tenant(claims={"cognito:groups": ["pv_reviewer"]})


def test_multitenant_fail_closed_without_claim(monkeypatch):
    _reset(monkeypatch, mt=True, pinned="pha-fallback")
    with pytest.raises(tenancy.TenantError):
        tenancy.resolve_tenant(claims={"sub": "u1"})        # no custom:tenant
    with pytest.raises(tenancy.TenantError):
        tenancy.resolve_tenant(claims={"custom:tenant": "  "})  # blank
    with pytest.raises(tenancy.TenantError):
        tenancy.resolve_tenant()                             # no claims at all


def test_multitenant_ignores_body_tenant(monkeypatch):
    _reset(monkeypatch, mt=True)
    # body says pha-evil; only the verified claim counts
    claims = {"custom:tenant": "pha-real"}
    assert tenancy.resolve_tenant({"tenant": "pha-evil"}, claims=claims) == "pha-real"


def test_check_ref_tenant_cross_tenant_refused(monkeypatch):
    _reset(monkeypatch, mt=True)
    claims = {"custom:tenant": "pha-real"}
    assert tenancy.check_ref_tenant({"tenant": "pha-real"}, claims=claims) is True
    assert tenancy.check_ref_tenant({"tenant": "pha-other"}, claims=claims) is False
    assert tenancy.check_ref_tenant({}, claims=claims) is False
    assert tenancy.check_ref_tenant("not-a-dict", claims=claims) is False


def test_check_ref_tenant_silo(monkeypatch):
    _reset(monkeypatch, pinned="pha-alameda")
    assert tenancy.check_ref_tenant({"tenant": "pha-alameda"}) is True
    assert tenancy.check_ref_tenant({"tenant": "pha-other"}) is False


def test_tenant_from_bearer_reads_claim_without_verify(monkeypatch):
    tok = _jwt({"sub": "u1", "custom:tenant": "pha-oakland"})
    assert tenancy.tenant_from_bearer(tok) == "pha-oakland"
    assert tenancy.tenant_from_bearer("not-a-jwt") is None
    assert tenancy.tenant_from_bearer(_jwt({"sub": "u1"})) is None   # no tenant claim


def test_tenant_scoped_name():
    # hybrid: each tenant gets its OWN physically-separate store name; silo keeps the base name
    assert tenancy.tenant_scoped_name("audit-ledger", "pha-oakland") == "pha-oakland-audit-ledger"
    assert tenancy.tenant_scoped_name("audit-ledger", "") == "audit-ledger"
    assert tenancy.tenant_scoped_name("audit-ledger", None) == "audit-ledger"
    assert tenancy.tenant_scoped_name("audit-ledger", "  t  ") == "t-audit-ledger"


def test_route_store(monkeypatch):
    # silo: the physical name is unchanged
    _reset(monkeypatch)
    assert tenancy.route_store("pv-e2e-case-store", "case-store") == "pv-e2e-case-store"
    # multi-tenant with an explicit verified claim: tenant-scoped, matching the CDK DataStack naming
    _reset(monkeypatch, mt=True)
    assert tenancy.route_store("pv-e2e-case-store", "case-store",
                               claims={"custom:tenant": "pha-oakland"}) == "pv-e2e-pha-oakland-case-store"
    # multi-tenant with no tenant: fail-closed
    _reset(monkeypatch, mt=True)
    with pytest.raises(tenancy.TenantError):
        tenancy.route_store("pv-e2e-case-store", "case-store")


def test_request_claims_context_drives_routing(monkeypatch):
    # the Lambda binds verified claims once; resolve_tenant + route_store then read the context
    _reset(monkeypatch, mt=True)
    tenancy.set_request_claims({"custom:tenant": "pha-alameda"})
    try:
        assert tenancy.resolve_tenant() == "pha-alameda"
        assert tenancy.route_store("pv-e2e-sanitized-artifacts", "sanitized-artifacts") == \
            "pv-e2e-pha-alameda-sanitized-artifacts"
    finally:
        tenancy.clear_request_claims()
    # cleared -> fail-closed again
    with pytest.raises(tenancy.TenantError):
        tenancy.resolve_tenant()


def test_ingest_multitenant_boundary_derives_tenant_from_verified_token_only(monkeypatch):
    """governed-core 1.6.0: ingest is invoked directly (no gateway interceptor). In multi-tenant mode
    the tenant comes from a VERIFIED access token's tenant group (or an already-signed pair) — never a
    typed field — and the response mints the signed pair the workflow execution carries. Fail-closed."""
    import ingest_case
    import identity
    monkeypatch.setenv("PROVENANCE_SECRET", "pv-unit-provenance-secret")
    stored = {}
    monkeypatch.setattr(ingest_case.case_store, "put_case",
                        lambda text, kind="adverse-event", case_id="": stored.setdefault("tenant", tenancy.resolve_tenant()) and "case-x")
    _reset(monkeypatch, mt=True)
    # no token, a typed tenant -> refused, nothing stored
    out = ingest_case.handler({"source": "raw text", "tenant": "pha-b"}, None)
    assert out["ingested"] is False and "identity not verified" in out["error"] and not stored
    # a verified token of a tenant_pha-a member -> bound to pha-a, signed pair returned
    monkeypatch.setattr(identity, "verify_access_token",
                        lambda tok, require_group=True: ({"cognito:groups": ["pv_reviewer", "tenant_pha-a"]}, None)
                        if tok == "good" else (None, "bad token"))
    out = ingest_case.handler({"source": "raw text", "access_token": "good", "tenant": "pha-b"}, None)
    assert out["ingested"] and stored["tenant"] == "pha-a"
    binding = out["tenant_binding"]
    assert binding[tenancy.TENANT_FIELD] == "pha-a"
    tenancy.clear_request_claims()
    assert tenancy.bind_tenant_from_args(binding) == "pha-a"      # what every workflow Lambda re-verifies
    assert "access_token" not in json.dumps(out)
    # a bad token -> refused
    tenancy.clear_request_claims()
    out = ingest_case.handler({"source": "raw text", "access_token": "nope"}, None)
    assert out["ingested"] is False
    # silo mode: no token needed, no binding minted
    _reset(monkeypatch, pinned="agency-1")
    out = ingest_case.handler({"source": "raw text"}, None)
    assert out["ingested"] and "tenant_binding" not in out
    tenancy.clear_request_claims()
