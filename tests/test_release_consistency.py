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
