"""PAR-4: this pack's descriptor has to match this pack.

The portfolio gate lives in the benefits repo and is pointed at a pack with --repo; everything it
needs to know about THIS pack comes from pack.json. So the descriptor is load-bearing, and a
descriptor that merely parses is worthless: the lineage proof compares CloudTrail Lambda invokes
against aegis.call lines BY TOOL NAME, so a tool that is instrumented but not declared here is
simply never checked - and the gate still reports zero orphans. A silent hole, not a failure.

These tests are the PV half of the contract. The gate-side half lives in the benefits repo's
tests/test_pack_descriptor.py; neither repo can test the other, which is itself a fact about the
harness worth keeping visible.
"""
import io
import json
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pack():
    with io.open(os.path.join(ROOT, "pack.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_the_descriptor_is_valid_json_with_the_fields_the_gate_needs():
    d = _pack()
    for k in ("pack", "agent_dir", "prefix_prefix", "tenants"):
        assert d.get(k), "pack.json must declare %r" % k
    assert d["lineage"]["tool_names"], "the governed tool identity is a pack fact"
    assert d["workflow"]["signoff_state"]
    assert os.path.isdir(os.path.join(ROOT, *d["agent_dir"].split("/"))), "agent_dir must resolve"


def test_the_declared_prefix_is_the_one_the_cdk_app_builds():
    """pv-<env>. If this drifts, every proof looks in the wrong log groups and finds nothing."""
    app = io.open(os.path.join(ROOT, "cdk", "app.py"), encoding="utf-8").read()
    m = re.search(r'prefix\s*=\s*f"([a-z]+)-\{env_name\}"', app)
    assert m, "could not find the prefix assignment in cdk/app.py"
    assert m.group(1) == _pack()["prefix_prefix"]


def test_the_declared_tenants_are_the_ones_this_pack_s_proofs_use():
    """Tenant ids are NOT in the CDK - they arrive as `-c tenants=`, which is right. So the place a
    mismatch actually bites is the proof scripts: the gate passes --tenants from this descriptor,
    and every script that has a --tenants default has to agree with it. Benefits' scripts still
    default to pha-a/pha-b, PV's to sp-a/sp-b, and the two packs' docstrings are cross-copied - the
    residue of an era when the harness was duplicated by hand (L35). Masked today only because the
    orchestrator always passes --tenants explicitly, which is one edit away from not being true."""
    declared = set(_pack()["tenants"])
    found = set()
    for sub in ("scripts",):
        for fn in sorted(os.listdir(os.path.join(ROOT, sub))):
            if not fn.endswith(".py"):
                continue
            src = io.open(os.path.join(ROOT, sub, fn), encoding="utf-8", errors="replace").read()
            found.update(re.findall(r"\b((?:sp|pha)-[ab])\b", src))
    assert found, "no tenant ids found under scripts/ - the scan itself is broken"
    assert found <= declared, (
        "these proof scripts name tenants this pack does not declare, so running one without an "
        "explicit --tenants would gate the wrong tenants and still report PASS: %s"
        % sorted(found - declared))


def _instrumented_tool_names():
    """Every @telemetry.instrument('<name>') in this pack's TRACKED source. cdk/.build is gitignored,
    so only tracked files are scanned; tools from the pinned governed-core wheel are outside the pack
    and are not asserted here."""
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    names, pat = set(), re.compile(r"instrument\(\s*['\"]([a-z_]+)['\"]")
    for rel in files:
        try:
            names.update(pat.findall(io.open(os.path.join(ROOT, rel), encoding="utf-8",
                                             errors="replace").read()))
        except OSError:
            continue
    return names


def test_every_instrumented_tool_in_this_pack_is_declared():
    declared = set(_pack()["lineage"]["tool_names"])
    found = _instrumented_tool_names()
    assert found, "no @telemetry.instrument names found - the scan itself is broken"
    undeclared = found - declared
    assert not undeclared, (
        "these tools emit aegis.call lines but are not in pack.json tool_names, so the lineage "
        "proof would never check them: %s" % sorted(undeclared))


def test_every_alias_resolves_to_a_declared_tool():
    """L21d: an alias whose target is not a declared tool silently never fires, and the tool it was
    meant to rescue is reported as an orphan on every run. PV needs one - the core-tools Lambda
    stem shares no lexical overlap with the pv_core identity it logs under."""
    lin = _pack()["lineage"]
    declared = set(lin["tool_names"])
    aliases = {k: v for k, v in lin.get("tool_aliases", {}).items() if not k.startswith("//")}
    assert aliases, "PV's core-tools Lambda cannot be resolved without an alias"
    for stem, tool in aliases.items():
        assert tool in declared, "alias %r -> %r, which is not a declared tool" % (stem, tool)


def test_the_manifest_tool_names_are_deliberately_not_the_lineage_tool_names():
    """A trap worth pinning. manifest.yaml exposes draft_narrative / finalize_submission /
    commit_causality on the pv-core target, but all three run in ONE Lambda that logs a single
    aegis.call line under 'pv_core'. Declaring the manifest names here would produce three
    audited_not_invoked orphans on every run."""
    import yaml
    m = yaml.safe_load(io.open(os.path.join(ROOT, "agents", "pharmacovigilance", "manifest.yaml"),
                               encoding="utf-8").read())
    mcp_names = set()
    for t in m["tools"]:
        for v in t.values():
            if isinstance(v, list):
                mcp_names.update(x["name"] for x in v if isinstance(x, dict) and "name" in x)
    declared = set(_pack()["lineage"]["tool_names"])
    assert "pv_core" in declared
    assert not ({"draft_narrative", "finalize_submission", "commit_causality"} & declared), (
        "the pv-core sub-tools must NOT be declared as lineage tools - they share one aegis.call "
        "identity, pv_core")
    assert mcp_names, "manifest tool scan is broken"
