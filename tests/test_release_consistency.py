"""Release-tag consistency gate. The `RELEASE` file at the repo root is the SINGLE SOURCE OF TRUTH for
the current/target validated tag, and every deploy-facing reference must match it. Cutting a new release
= update `RELEASE`, run this test, fix what it names. (Kept portable: only files that exist here are
checked.)"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
TAG = (ROOT / "RELEASE").read_text(encoding="utf-8").strip()


def test_release_file_shape():
    assert re.fullmatch(r"v\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?", TAG), f"RELEASE malformed: {TAG!r}"


def test_every_checkout_instruction_matches_release():
    """Any `git checkout vX.Y.Z` in a tracked doc must reference THE release."""
    offenders = []
    for name in ("README.md", "DEPLOYMENT-GUIDE.md", "START-HERE.md", "cdk/README.md"):
        p = ROOT / name
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"git checkout (v\d+\.\d+\.\d+[^\s`\"]*)", line)
            if m and m.group(1) != TAG:
                offenders.append(f"{name}:{i} says {m.group(1)}, RELEASE says {TAG}")
    assert not offenders, "stale deploy instructions:\n" + "\n".join(offenders)


def test_anchor_documents_name_the_release():
    """The anchor docs must each explicitly carry the current tag."""
    checks = [
        ("README.md", f"releases/tag/{TAG}"),
        ("START-HERE.md", f"releases/tag/{TAG}"),
        ("VALIDATED_RELEASE.md", f"`{TAG}`"),
        ("cdk/README.md", f"`{TAG}`"),
    ]
    for name, needle in checks:
        p = ROOT / name
        assert p.exists(), f"anchor doc {name} missing"
        assert needle in p.read_text(encoding="utf-8"), f"{name} does not reference the current release {TAG}"


# ---- 2026-09-08: "contains the tag somewhere" was not enough --------------------------------------
# RELEASE said v0.6.0-pilot-rc1 while RELEASE-MANIFEST.md's own "Pilot tag" row still named
# v0.5.2-pilot-rc1 - inside the file whose header declares "if a number anywhere disagrees with this
# table, this table is correct and the other file is a bug". The gate above did not see it for two
# reasons: RELEASE-MANIFEST.md was not in the anchor list at all, and the anchor check only asks
# whether a document MENTIONS the tag, which a document can do while a row asserts a different one.
# So the row that ASSERTS the current release is now checked directly.
#
# Scope matters here. VALIDATED_RELEASE.md is a per-release history: `## Current release` followed by
# several `## Previous release` sections, each with its own `| Tag |` row that correctly names an
# older tag. A first version of this gate judged every `Tag` row and flagged nine of those - a gate
# that fires on correct history teaches people to skip it. Only rows that CLAIM to be current are
# judged: labelled Pilot/Supported/Current tag anywhere, or plain `Tag` under a "current" heading.

_CURRENT_LABEL = re.compile(r"^(pilot tag|supported tag|current validated release|current tag)$", re.I)
_PLAIN_TAG_LABEL = re.compile(r"^tag$", re.I)
_ROW = re.compile(r"^\|\s*\*{0,2}([^|*]+?)\*{0,2}\s*\|(.*)$")
_ANY_TAG = re.compile(r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?")


def test_the_row_that_asserts_the_current_release_names_THE_release():
    offenders = []
    for name in ("RELEASE-MANIFEST.md", "VALIDATED_RELEASE.md", "START-HERE.md", "README.md"):
        p = ROOT / name
        if not p.exists():
            continue
        heading = ""
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("#"):
                heading = line
                continue
            m = _ROW.match(line)
            if not m:
                continue
            label, body = m.group(1).strip(), m.group(2)
            current = bool(_CURRENT_LABEL.match(label)) or (
                _PLAIN_TAG_LABEL.match(label) and "current" in heading.lower())
            if not current:
                continue
            first = _ANY_TAG.search(body)
            if first and first.group(0) != TAG:
                offenders.append("%s:%d row %r asserts %s, RELEASE says %s"
                                 % (name, i, label, first.group(0), TAG))
    assert not offenders, (
        "a row that asserts the CURRENT release disagrees with the RELEASE file:\n  "
        + "\n  ".join(offenders)
        + "\n\nUpdate the row, or move it under a 'Previous release' heading if it is history.")
