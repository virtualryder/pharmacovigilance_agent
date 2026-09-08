"""L41: every governed Lambda must map to a declared tool, or the lineage proof cannot see it.

The lineage proof compares CloudTrail Lambda invokes against aegis.call audit lines BY TOOL NAME.
The mapper is lexical - it matches the function-name stem against the tool identity - with an alias
table for the cases that share no letters. Anything it cannot map was DROPPED (`if t:`), silently.

So a governed Lambda whose name does not resemble the tool it hosts was simply absent from the
parity check, and "zero orphans" meant "zero orphans among the ones that happened to map". Found
2026-09-08 while preparing REL-5, by running the mapper over both packs' real function names before
spending a live cycle:

    benefits  ben-fp-overpayment -> None   (hosts detect_overpayment)
    benefits  ben-fp-finalize    -> None   (hosts finalize_signoff)
    pv        pv-fp-finalize     -> None   (hosts finalize_signoff)

Two of thirteen governed tools were invisible to the proof that certified v0.6.0-pilot-rc1. That
tag's "orphans=0" is still true, but it is narrower than it reads, and the release record says so.

This test is the guard. It is static, so it costs nothing and runs on every commit.
"""
import importlib.util
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.environ.get("AEGIS_HARNESS_REPO", os.path.join(os.path.dirname(ROOT), "benefits_eligibility_agent"))
LINEAGE = os.path.join(HARNESS, "scripts", "lineage_proof.py")


def _pack():
    with io.open(os.path.join(ROOT, "pack.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _lineage_proof():
    if not os.path.isfile(LINEAGE):
        pytest.skip("lineage_proof.py lives in the harness repo; not present here")
    spec = importlib.util.spec_from_file_location("lineage_proof_under_test", LINEAGE)
    m = importlib.util.module_from_spec(spec)
    sys.modules["lineage_proof_under_test"] = m
    spec.loader.exec_module(m)
    return m


def test_the_pack_declares_the_lambdas_it_deploys():
    lin = _pack()["lineage"]
    assert lin.get("lambda_stems"), "pack.json must declare lambda_stems (L41)"


def test_every_governed_lambda_maps_to_a_declared_tool():
    """The check that was missing. An unmapped governed Lambda is invisible to the lineage proof."""
    lp = _lineage_proof()
    pack = _pack()
    names = pack["lineage"]["tool_names"]
    aliases = {k: v for k, v in pack["lineage"]["tool_aliases"].items() if not k.startswith("//")}
    prefix = "%s-fp" % pack["prefix_prefix"]
    unmapped = [s for s in pack["lineage"]["lambda_stems"]
                if not lp.tool_of("%s-%s" % (prefix, s), names, aliases=aliases)]
    assert not unmapped, (
        "these governed Lambdas do not map to any declared tool, so the lineage proof drops their "
        "invokes and reports zero orphans regardless: %s" % unmapped)


def test_the_mapping_is_stable_across_deployment_prefixes():
    """tool_of strips a prefix it is not told, so it must not depend on which env is deployed."""
    lp = _lineage_proof()
    pack = _pack()
    names = pack["lineage"]["tool_names"]
    aliases = {k: v for k, v in pack["lineage"]["tool_aliases"].items() if not k.startswith("//")}
    p = pack["prefix_prefix"]
    for env in ("fp", "gate", "mt6", "rel5"):
        for stem in pack["lineage"]["lambda_stems"]:
            fn = "%s-%s-%s" % (p, env, stem)
            assert lp.tool_of(fn, names, aliases=aliases), "%s did not map under env %r" % (fn, env)


def test_an_unmapped_invoke_is_reported_not_dropped():
    """The systemic half: assess_coverage must surface what it could not name."""
    lp = _lineage_proof()
    v = lp.assess_coverage(
        {"cloudtrail": [{"event_source": "lambda.amazonaws.com", "event_name": "Invoke",
                         "target": "arn:aws:lambda:us-east-1:111122223333:function:x-fp-not-a-tool"}],
         "aegis": []},
        ["mask_pii"], aliases={})
    assert v["unmapped_lambda_invokes"] == {"x-fp-not-a-tool": 1}, (
        "an invoke the mapper could not name must appear in the verdict, not vanish")
