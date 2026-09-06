#!/usr/bin/env python3
"""Offline proof of the perimeter Cedar model (#160 + #161) for this pack - PAR-1 step 5 port of the
benefits gate (2026-09-06).

Renders the real manifest through lib/engine/render.py and asserts that every declared condition is
present as a Cedar statement, then LINTS the policies/*.cedar files (the deploy's source of truth)
against the rules the GA AgentCore Policy engine enforces at create-policy time - rules a schema-less
parse does NOT catch and a live gate surfaced on benefits (2026-09-05):
  1. an UNSCOPED forbid (action, resource is Gateway) must NOT read context.input - the built-in
     actions (InvokeAgent/InvokeLLM/Mcp/...) carry no `input`.
  2. every optional context.input.<field> access must be presence-guarded (`context.input has <field>`);
     `deidentified` is a required tool field (exempt).
  3. a `number` tool field is a Cedar DECIMAL: compare with the decimal extension, never Long <=/>=.
"""
import glob
import os
import re
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RENDER = os.path.join(ROOT, "lib", "engine", "render.py")
MANIFEST = os.path.join(ROOT, "agents", "pharmacovigilance", "manifest.yaml")
POLICIES_DIR = os.path.join(ROOT, "policies")
GW_ARN = "arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/pv-safety-gw"
REQUIRED_INPUT_FIELDS = {"deidentified"}   # required in the tool schema -> no `has` guard needed


def _render():
    build = tempfile.mkdtemp(prefix="cedar_render_")
    subprocess.run([sys.executable, RENDER, MANIFEST, build], check=True, capture_output=True, text=True)
    rows = {}
    with open(os.path.join(build, "policies.tsv"), encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            name, mode, stmt = line.split("\t", 2)
            rows[name] = (mode, stmt)
    return rows


@pytest.fixture(scope="module")
def policies():
    return _render()


NINE_CONDITIONS = [
    ("pv_require_entitlement",           'principal.getTag("custom:tools") != ""'),                      # 1 entitlement
    ("pv_mask_before_assess",            "context.input.deidentified == true"),                          # 2 data-class
    ("pv_consent_purpose_before_assess_seriousness", "context.input has consent && context.input.consent == true"),  # 3 consent
    ("pv_consent_purpose_before_assess_seriousness", 'context.input.purpose == "pharmacovigilance"'),    # 4 purpose
    ("pv_budget_before_draft_narrative", "context.input has budget_ok && context.input.budget_ok == true"),  # 5 budget
    ("pv_require_service_window",        "context.input has within_service_window && context.input.within_service_window == true"),  # 6 temporal
    ("pv_require_tenant",                'principal.hasTag("custom:tenant")'),                           # 8 tenant
    ("pv_no_self_submit",                'AgentCore::Action::"pv-core___finalize_submission"'),           # 9 SoD
]
# 7 QUANTITATIVE: no numeric decision field exists in this pack's tool schemas (nothing to cap), so the
# quantitative condition is N/A here and PACK-PARITY records it as such - it is not silently "missing".
QUANTITATIVE_NA = True


def test_all_declared_policies_render(policies):
    assert len(policies) == 11, sorted(policies)


@pytest.mark.parametrize("name,fragment", NINE_CONDITIONS)
def test_condition_present(policies, name, fragment):
    assert name in policies, "missing policy %s" % name
    _mode, stmt = policies[name]
    assert fragment in stmt, "policy %s missing %r:\n%s" % (name, fragment, stmt)


def test_entitlement_is_zero_default(policies):
    """#160: the entitlement forbid is unconditional over every action/resource (no action==), so a
    principal with no NON-EMPTY custom:tools claim (and not in tools_granted) is denied ALL tools."""
    _mode, stmt = policies["pv_require_entitlement"]
    assert "action, resource is AgentCore::Gateway" in stmt
    assert 'principal.getTag("custom:tools") != ""' in stmt
    assert "tools_granted" in stmt


def test_every_statement_parses_under_cedar(policies):
    cedarpy = pytest.importorskip("cedarpy")
    policy_set = "\n".join(stmt.replace("__GW_ARN__", GW_ARN) for _m, stmt in policies.values())
    req = {"principal": 'AgentCore::User::"u"', "action": 'AgentCore::Action::"assess-seriousness___assess_seriousness"',
           "resource": 'AgentCore::Gateway::"%s"' % GW_ARN, "context": {}}
    cedarpy.is_authorized(req, policy_set, [])


def _cedar_files():
    return sorted(glob.glob(os.path.join(POLICIES_DIR, "*.cedar")))


def test_cedar_files_exist():
    names = {os.path.basename(p) for p in _cedar_files()}
    for expected in ('require_entitlement.cedar', 'require_service_window.cedar'):
        assert expected in names, "missing %s" % expected


@pytest.mark.parametrize("path", _cedar_files())
def test_cedar_file_parses(path):
    cedarpy = pytest.importorskip("cedarpy")
    body = open(path, encoding="utf-8").read()
    stmt = re.sub(r'AgentCore::Gateway::"arn:[^"]+"', 'AgentCore::Gateway::"%s"' % GW_ARN, body)
    req = {"principal": 'AgentCore::User::"u"', "action": 'AgentCore::Action::"assess-seriousness___assess_seriousness"',
           "resource": 'AgentCore::Gateway::"%s"' % GW_ARN, "context": {}}
    cedarpy.is_authorized(req, stmt, [])   # raises on a syntax error


@pytest.mark.parametrize("path", _cedar_files())
def test_cedar_ga_schema_rules(path):
    body = open(path, encoding="utf-8").read()
    code = "\n".join(re.sub(r"//.*$", "", ln) for ln in body.splitlines())
    reads_input = "context.input" in code
    unscoped = re.search(r"forbid\s*\(\s*principal\s*,\s*action\s*,", code) is not None
    if reads_input:
        assert not unscoped, (
            "%s: an UNSCOPED forbid reads context.input - the GA engine rejects this "
            "(built-in actions have no input). Scope it to specific tool actions." % os.path.basename(path))
    accessed = set(re.findall(r"context\.input\.([A-Za-z_][A-Za-z0-9_]*)", code))
    guarded = set(re.findall(r"context\.input has ([A-Za-z_][A-Za-z0-9_]*)", code))
    for field in accessed:
        if field in REQUIRED_INPUT_FIELDS:
            continue
        assert field in guarded, (
            "%s: optional context.input.%s is accessed without a `context.input has %s` guard "
            "(the GA engine rejects unguarded optional-attribute access)." % (os.path.basename(path), field, field))
    for num in ("cost_of_attendance", "student_aid_index", "prior_monthly_benefit"):
        assert not re.search(r"context\.input\.%s\s*(<=|>=|<|>)\s*\d" % num, code), (
            "%s: %s is a Cedar decimal - use .lessThanOrEqual(decimal(\"..\")), not a Long comparison."
            % (os.path.basename(path), num))
