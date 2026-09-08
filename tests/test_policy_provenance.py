"""#232: every audit row must be able to name the policy set that decided it.

`governed_core.controls.evidence` writes `policy_version`, `rule_version` and `deployment_version`
into the HASHED logical record of every audit row, reading them from the environment and defaulting
to the literal string "unset". Nothing in the CDK app set them, so every row written by a
CDK-deployed Lambda recorded:

    "policy_version": "unset", "rule_version": "unset"

which is what `evidence/AGENTCORE-111-GATE-2026-09-02.json` contains, in the WORM ledger, for a
regulated determination workflow. The legacy shell path set the literal "v1" - a placeholder, not
provenance: it does not change when the policies change, so it cannot answer the question the field
exists for.

The provenance is now content-addressed over the artefacts actually deployed. These tests pin the
three properties that make it provenance rather than decoration:

  1. it reaches the deployed functions at all
  2. it is not a placeholder
  3. it CHANGES when the policies change, and does not when they do not
"""
import hashlib
import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "cdk"))

_STACKS = next((d.name for d in (ROOT / "cdk").iterdir()
                if d.is_dir() and d.name.endswith("_stacks")), None)
_mod = importlib.import_module("%s.compute_stack" % _STACKS)
provenance_env = _mod.provenance_env


def test_provenance_is_derived_not_placeholder():
    p = provenance_env(ROOT)
    assert p["POLICY_VERSION"].startswith("cedar-"), p
    assert p["RULE_VERSION"].startswith("manifest-"), p
    for k, v in p.items():
        assert v not in ("unset", "v1", ""), (
            "%s is %r - a placeholder is not provenance; it does not change when the thing it "
            "names changes, which is the whole point of the field" % (k, v))


def test_policy_version_changes_when_a_policy_changes(tmp_path):
    """Content-addressed, or it is decoration."""
    import shutil
    fake = tmp_path / "repo"
    shutil.copytree(ROOT / "policies", fake / "policies")
    shutil.copytree(ROOT / "agents", fake / "agents",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (fake / "RELEASE").write_text("vX.Y.Z", encoding="utf-8")

    before = provenance_env(fake)
    a_policy = sorted((fake / "policies").glob("*.cedar"))[0]
    a_policy.write_text(a_policy.read_text(encoding="utf-8") + "\n// touched\n", encoding="utf-8")
    after = provenance_env(fake)

    assert before["POLICY_VERSION"] != after["POLICY_VERSION"], (
        "editing a Cedar policy did not change POLICY_VERSION - the field cannot identify the "
        "policy set that decided a case")
    assert before["RULE_VERSION"] == after["RULE_VERSION"], (
        "editing a Cedar policy changed RULE_VERSION - the two must be independently attributable")


def test_provenance_is_stable_across_line_endings(tmp_path):
    """A CRLF checkout and an LF checkout of the same tree must agree.

    Otherwise the recorded 'policy version' says which machine ran `cdk synth`, not which policies
    were deployed - which is the L36 line-ending class of defect wearing a different hat.
    """
    import shutil
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    for dst in (lf, crlf):
        shutil.copytree(ROOT / "policies", dst / "policies")
        shutil.copytree(ROOT / "agents", dst / "agents",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (dst / "RELEASE").write_text("vX.Y.Z", encoding="utf-8")
    for f in (crlf / "policies").glob("*.cedar"):
        f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert provenance_env(lf)["POLICY_VERSION"] == provenance_env(crlf)["POLICY_VERSION"]


def test_the_synthesized_functions_carry_the_provenance():
    """It must reach the DEPLOYED artefact, not just exist as a helper.

    This reuses the pack's own synthesized compute template (`test_cdk_stacks.T_COMPUTE`) rather
    than synthesizing again - so it asserts against exactly the template every other CDK assertion
    in this suite is written against.
    """
    pytest.importorskip("aws_cdk.assertions")
    import test_cdk_stacks as t

    template = getattr(t, "T_COMPUTE", None)
    assert template is not None, (
        "test_cdk_stacks no longer exposes T_COMPUTE - this test must be repointed rather than "
        "left to skip. A provenance check that skips proves nothing, which is the exact failure "
        "class this campaign keeps finding.")

    fns = template.find_resources("AWS::Lambda::Function")
    assert fns, "no Lambda functions in the compute template"

    audit_writers, missing = [], []
    for lid, res in fns.items():
        env = ((res.get("Properties") or {}).get("Environment") or {}).get("Variables") or {}
        if "AUDIT_TABLE" not in env:      # only the governed tool functions write audit rows
            continue
        audit_writers.append(lid)
        for key in ("POLICY_VERSION", "RULE_VERSION", "DEPLOYMENT_VERSION"):
            v = env.get(key)
            if not v or v in ("unset", "v1"):
                missing.append("%s.%s=%r" % (lid, key, v))

    assert audit_writers, (
        "no audit-writing Lambda found in the compute template - the selector (AUDIT_TABLE in the "
        "environment) no longer identifies them, so this test would pass vacuously")
    assert not missing, (
        "audit-writing functions without real provenance:\n  " + "\n  ".join(missing))
