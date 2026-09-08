"""Gate: any document sentence describing the CURRENT pinned core must name the pinned version.

WHY THIS EXISTS
---------------
`requirements-core.txt` is the pin, `lib/CORE_VERSION` records it, `lib/core.lock` is derived from
it, and `tools/check_core_parity.py` compares it across packs. Nothing checked the PROSE. So when
the pack moved to governed-core 1.11.0 and then 1.11.1, nine sentences across four packs went on
saying 1.10.1 — including a copy-pasteable install line,

    python -m pip install --require-hashes -r requirements-core.txt   # governed-core 1.10.1

which tells a reader they are about to install a version that file has not pinned for two releases.

Only sentences that DESCRIBE the pin are judged: a line has to mention `requirements-core.txt`
(or "pins governed-core") to be in scope, so the many correct historical statements — "the Tier-1
live re-gate passed on governed-core 1.10.1" — are untouched. A number that is deliberately
historical inside a described-pin sentence takes the same `<!-- count-gate:historical -->` marker
the test-count gate uses, scoped to that number.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

DOCS = ["README.md", "START-HERE.md", "RELEASE-MANIFEST.md", "VALIDATED_RELEASE.md",
        "PILOT-SCOPE.md", "DEPLOYMENT-GUIDE.md", "cdk/README.md",
        "docs/GATE-B-CHECKLIST.md", "docs/VALIDATED-MATRIX.md"]

# A line is "about the pin" if it names the pin file or says the pack pins the core.
ABOUT_THE_PIN = re.compile(r"requirements-core\.txt|pins?\s+\*{0,2}governed[- ]core", re.I)
VERSION = re.compile(r"\b(\d+\.\d+\.\d+)\b")


def _pinned():
    return (ROOT / "lib" / "CORE_VERSION").read_text(encoding="utf-8").strip()


def test_documents_describing_the_core_pin_name_the_pinned_version():
    pinned = _pinned()
    problems = []
    for rel in DOCS:
        p = ROOT / rel
        if not p.exists():
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not ABOUT_THE_PIN.search(line):
                continue
            for m in VERSION.finditer(line):
                v = m.group(1)
                if v == pinned:
                    continue
                if "count-gate:historical" in line[m.end(): m.end() + 40]:
                    continue
                problems.append("%s:%d says governed-core %s while lib/CORE_VERSION pins %s"
                                % (rel, n, v, pinned))
    assert not problems, (
        "The docs describe a core pin this pack does not have:\n  "
        + "\n  ".join(sorted(set(problems)))
        + "\n\nUpdate the sentence, or mark a deliberately historical number with an inline "
          "<!-- count-gate:historical --> within 40 characters after it."
    )
