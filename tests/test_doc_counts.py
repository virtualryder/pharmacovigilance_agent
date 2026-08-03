"""Gate: the test count quoted in the docs must match the suite that actually exists.

Why this exists. Before this gate, four different counts (95, 109, 112, 114) coexisted across seven
documents, and RELEASE-MANIFEST.md contradicted itself two rows apart - inside the file that declares
itself the authoritative record. The cause was structural: test_release_consistency.py gated tag
references but nothing asserted a count, so every suite change silently invalidated the docs.

A reviewer who spot-checks one number and finds it wrong stops trusting the control claims too. In a
regulated-industry review that is expensive. So the count is now machine-enforced.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Files that quote the offline test count and must therefore agree with reality.
COUNTED_DOCS = [
    "README.md", "START-HERE.md", "RELEASE-MANIFEST.md", "VALIDATED_RELEASE.md",
    "PILOT-SCOPE.md", "PV-PILOT-READINESS-PLAN.md", "DEPLOYMENT-GUIDE.md",
    "docs/GATE-B-CHECKLIST.md", "evidence/EP1-VALIDATION.md",
]

# Any "<n> offline tests" / "<n> tests" / "<n>/<n>" style count in prose.
COUNT_PATTERNS = [
    re.compile(r"\*\*(\d{2,4}) offline tests?\*\*"),
    re.compile(r"\b(\d{2,4}) offline tests?\b"),
    re.compile(r"\b(\d{2,4}) / \1\b"),
    re.compile(r"\b(\d{2,4})/\1\b"),
]


def _collected_count():
    """Number of tests pytest actually collects.

    Must be real collection, not a static count of `def test_` - parametrized
    tests expand to several collected items each, so static counting undercounts
    (it read 113 against a true 114 on first run, which would have baked a fresh
    wrong number into the docs).

    Guarded by an env var so the nested run cannot recurse into this module.
    """
    import os
    import subprocess
    import sys

    env = dict(os.environ, PV_DOC_COUNT_GATE="1")
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--collect-only",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=300).stdout
    m = re.search(r"(\d+) tests? collected", out)
    if not m:
        pytest.skip("could not determine collected count from pytest output")
    return int(m.group(1))


@pytest.mark.skipif(__import__("os").environ.get("PV_DOC_COUNT_GATE") == "1",
                    reason="nested collection run - avoid recursion")
def test_offline_count_in_docs_matches_the_suite():
    actual = _collected_count()
    problems = []
    for rel in COUNTED_DOCS:
        p = ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for pat in COUNT_PATTERNS:
            for m in pat.finditer(text):
                n = int(m.group(1))
                # Only judge numbers in the plausible suite-size range; this
                # avoids flagging years, ports, CFR sections and dollar figures.
                if 60 <= n <= 999 and n != actual:
                    line = text[: m.start()].count("\n") + 1
                    problems.append(f"{rel}:{line} quotes {n}, suite has {actual}")
    assert not problems, (
        "Test-count drift between the docs and the suite:\n  "
        + "\n  ".join(sorted(set(problems)))
        + f"\n\nThe suite currently defines {actual} tests. Update the documents, "
          "or update this gate if the range heuristic caught a false positive."
    )


def test_cdk_assertion_count_in_docs_matches_the_cdk_test_module():
    cdk_tests = len(re.findall(
        r"^def (test_\w+)",
        (ROOT / "tests" / "test_cdk_stacks.py").read_text(encoding="utf-8"), re.M))
    problems = []
    for rel in COUNTED_DOCS + ["cdk/README.md"]:
        p = ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        # "7 CDK stacks" is a stack count, not an assertion count - only judge
        # numbers that are actually describing assertions.
        for m in re.finditer(r"\b(\d{1,3}) CDK(?! stacks?\b)\b", text):
            n = int(m.group(1))
            if n != cdk_tests:
                line = text[: m.start()].count("\n") + 1
                problems.append(f"{rel}:{line} quotes {n} CDK, module has {cdk_tests}")
    assert not problems, (
        "CDK-assertion-count drift:\n  " + "\n  ".join(sorted(set(problems)))
    )


@pytest.mark.parametrize("phrase", [
    "fails soft to a deterministic aggregate",   # the removed fabrication path (P0-4)
    "deidentified=false is refused",             # pre-P0-1 masking semantics
    "32-check",                                  # superseded harness count
    "32/32",
    "7/7 red-team",
])
def test_generators_do_not_carry_superseded_claims(phrase):
    """The Word-doc generators ship customer-facing regulatory material.

    test_release_consistency.py gates markdown only, so these drifted silently and
    the shipped regulatory .docx ended up describing a data-fabrication path that
    had been deliberately removed. Gate the generators too.
    """
    offenders = [
        str(f.relative_to(ROOT))
        for f in (ROOT / "docs" / "generators").glob("*.js")
        if phrase in f.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"Superseded claim {phrase!r} still present in: {offenders}. "
        "Regenerate the .docx after fixing the generator."
    )
