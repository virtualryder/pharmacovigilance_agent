"""Unit tests for the IdP-federation pre-token-generation group mapper. Pure logic (no AWS): an
external IdP group maps to the agent's Cedar role so the existing permit fires; unknown groups map to
nothing (deny-by-default holds); native Cognito groups are preserved."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CTRL = ROOT / "lib" / "controls"

spec = importlib.util.spec_from_file_location("idp_group_mapper", CTRL / "idp_group_mapper.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CFG = {"map": {"FinancialAidOfficers": "aid_officer", "Approvers": "aid_officer"},
       "source_attr": "custom:idp_roles", "role_group": "aid_officer", "strict": True}


def test_external_group_maps_to_role():
    out = m.map_groups({"custom:idp_roles": "FinancialAidOfficers"}, [], CFG)
    assert out == ["aid_officer"]


def test_multi_and_dedup():
    out = m.map_groups({"custom:idp_roles": "FinancialAidOfficers,Approvers"}, [], CFG)
    assert out == ["aid_officer"]              # both map to the same role, deduped


def test_unknown_group_gets_no_role():
    out = m.map_groups({"custom:idp_roles": "Interns,Contractors"}, [], CFG)
    assert out == []                           # nothing maps -> deny-by-default holds


def test_native_groups_are_preserved():
    out = m.map_groups({"custom:idp_roles": "Interns"}, ["aid_officer"], CFG)
    assert out == ["aid_officer"]              # a native Cognito user is unaffected


def test_strict_drops_unknown_target_roles():
    cfg = dict(CFG, map={"Admins": "super_admin"})   # maps to a role the app doesn't recognize
    out = m.map_groups({"custom:idp_roles": "Admins"}, [], cfg)
    assert out == []                           # strict: only the app's known role_group survives


def test_separators_and_whitespace():
    out = m.map_groups({"custom:idp_roles": " FinancialAidOfficers ; Approvers "}, [], CFG)
    assert out == ["aid_officer"]


def test_handler_shapes_v2_response():
    ev = {"request": {"userAttributes": {"custom:idp_roles": "FinancialAidOfficers"},
                      "groupConfiguration": {"groupsToOverride": []}}}
    import os
    os.environ.update({"GROUP_MAP": '{"FinancialAidOfficers":"aid_officer"}',
                       "SOURCE_ATTR": "custom:idp_roles", "ROLE_GROUP": "aid_officer", "STRICT": "1"})
    out = m.handler(ev, None)
    gd = out["response"]["claimsAndScopeOverrideDetails"]["groupOverrideDetails"]
    assert gd["groupsToOverride"] == ["aid_officer"]
