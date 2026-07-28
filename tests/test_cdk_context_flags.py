"""cdk.json feature-flag gate — a removed-in-v2 flag makes the REAL deploy impossible.

WHY THIS EXISTS. `cdk/cdk.json` carried `@aws-cdk/core:enableStackNameDuplicates`, a CDK **v1** feature
flag that was REMOVED in v2. On the pinned CDK (aws-cdk-lib 2.262.1) the CLI raises:

    RuntimeError: Unsupported feature flag '@aws-cdk/core:enableStackNameDuplicates'.
    This flag existed on CDKv1 but has been removed in CDKv2.

That is fatal: `cdk synth` and `cdk deploy` both abort, so the documented deployment path could not be
followed at all. It was found by actually walking the runbook on 2026-07-28, not by the test suite.

**Why the existing tests missed it.** `tests/test_cdk_stacks.py` builds stacks with
`aws_cdk.assertions.Template.from_stack()`, which instantiates constructs directly in-process and
**never reads `cdk.json`**. Only the `cdk` CLI loads that file. So the whole CDK suite stayed green
while the real deployment was broken — the unit tests and the shipped artifact disagreed.

This gate closes that gap cheaply: it asserts no known removed-in-v2 flag is present, without needing
Node or a real synth in CI.
"""
import json
import pathlib

CDK_JSON = pathlib.Path(__file__).resolve().parents[1] / "cdk" / "cdk.json"

# Flags that existed in CDK v1 and are REMOVED in v2 — presence is a hard CLI error.
REMOVED_IN_V2 = {
    "@aws-cdk/core:enableStackNameDuplicates",
    "@aws-cdk/core:newStyleStackSynthesis",
    "aws-cdk:enableDiffNoFail",
}


def test_cdk_json_parses():
    assert CDK_JSON.exists(), "cdk/cdk.json is missing"
    json.loads(CDK_JSON.read_text(encoding="utf-8"))


def test_no_removed_v1_feature_flags():
    """A removed-in-v2 flag aborts `cdk synth`/`cdk deploy` — the documented path stops working."""
    ctx = json.loads(CDK_JSON.read_text(encoding="utf-8")).get("context", {})
    offenders = sorted(set(ctx) & REMOVED_IN_V2)
    assert not offenders, (
        "cdk/cdk.json contains CDK v1 feature flags that were REMOVED in v2; the CDK CLI will refuse "
        f"to synth or deploy: {offenders}. Delete them from the context block.")
