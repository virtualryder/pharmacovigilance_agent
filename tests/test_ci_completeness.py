"""CI completeness gate - the CDK security assertions must actually RUN, not silently skip.

WHY THIS EXISTS: `tests/test_cdk_stacks.py` begins with `pytest.importorskip("aws_cdk")`. That is the
right behaviour on a laptop without the CDK libs installed - but CI never installed `aws-cdk-lib` at all, so for a period the assertions SKIPPED on every push, so the 23 CDK security
assertions did not run at all while the build still reported green.

Silently skipped is worse than absent: the repository advertises continuous validation of controls
(Cedar deny-by-default in ENFORCE, customer-managed KMS, the WORM audit tamper-Deny, locked egress,
exact-ARN least-privilege IAM, and the "no case narrative or patient data in Step Functions state" pass-by-reference
assertion) that CI was not exercising.

These tests make that failure mode loud:
  * in CI (env `CI=true`) the CDK libs MUST be importable - fail, don't skip;
  * the CI workflow must install `cdk/requirements.txt` before pytest;
  * `cdk/requirements.txt` must be PINNED so an independent verifier resolves the same versions that
    were validated (an unpinned range makes their result incomparable to the recorded evidence).
"""
import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CDK_REQS = ROOT / "cdk" / "requirements.txt"


def _in_ci():
    return os.environ.get("CI", "").lower() in ("1", "true", "yes")


@pytest.mark.skipif(not _in_ci(), reason="only enforced inside CI; locally the CDK libs are optional")
def test_cdk_libs_are_installed_in_ci():
    """In CI the CDK security assertions must run. A skip here would hide real coverage loss."""
    try:
        import aws_cdk  # noqa: F401
        from aws_cdk import assertions  # noqa: F401
    except ImportError as exc:  # pragma: no cover - the failure path is the point
        pytest.fail(
            "aws-cdk-lib is NOT installed in CI, so tests/test_cdk_stacks.py would silently skip and "
            "the 23 CDK security assertions would not run. Install cdk/requirements.txt in the "
            f"workflow before pytest. ImportError: {{exc}}")


def test_ci_workflow_installs_cdk_requirements():
    """Static check, so this holds even when the suite runs outside CI."""
    assert CI_WORKFLOW.exists(), ".github/workflows/ci.yml is missing"
    wf = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "cdk/requirements.txt" in wf, (
        "ci.yml must install cdk/requirements.txt before running pytest - without it "
        "tests/test_cdk_stacks.py skips and the CDK security assertions never run in CI")
    assert re.search(r"pytest\s+tests/", wf), "ci.yml must run the test suite"


def test_cdk_requirements_are_pinned_for_reproducibility():
    """An independent verifier must resolve the SAME versions that were validated.

    `aws-cdk-lib>=2.150` lets a verifier pull a newer CDK whose synthesis differs, making their result
    incomparable to the recorded evidence - which undermines independent verification.
    """
    assert CDK_REQS.exists(), "cdk/requirements.txt is missing"
    unpinned = []
    for raw in CDK_REQS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "==" not in line:
            unpinned.append(line)
    assert not unpinned, (
        "cdk/requirements.txt must pin exact versions (==) so an independent verifier reproduces the "
        f"validated synthesis; unpinned: {{unpinned}}")
