"""Gate: a "passed/total" test ratio in a counted document must have equal halves.

WHY THIS EXISTS
---------------
The doc-count updater rewrites the quoted suite size across the READMEs and the deck
generators. Its patterns include `\\b(\\d{2,4})/\\1\\b` - the "N/N" form meaning "N of N green".
It captured only the FIRST number and substituted only that, so `189/189` became `204/189`:
a customer-facing PowerPoint stat claiming 204 of 189 tests passed.

The second-order failure is the dangerous one. `204/189` no longer matches `(\\d+)/\\1`, and it
is not a bare number either, so test_doc_counts.py stopped seeing it. The count gate went GREEN
on a document carrying an impossible number, because the corruption removed the number from the
gate's view. Three such stats had already shipped when a cross-pack audit found them by hand:
  edu_financial_aid_agent  RELEASE-MANIFEST.md   "271 / 221 passing"
  edu_financial_aid_agent  leadership_deck.js    "204/190 automated tests green"
  pharmacovigilance_agent  RELEASE-MANIFEST.md   "270 / 218 passing"

A ratio is the one shape where a partial rewrite is both wrong and invisible. Gate it directly.
"""
import pathlib
import re

import test_doc_counts as gate

ROOT = pathlib.Path(__file__).resolve().parent.parent

RATIO = re.compile(r"(\d{2,4})\s*/\s*(\d{2,4})")

# A pass ratio is always stated next to the thing it counts. Regulatory citations
# ("21 CFR 314.80 / 312.32", "24 CFR Parts 5/960/966/982") and threshold chains
# ("60/85/100 %", "30/50/80% AMI") never are, so this keeps the gate on its subject.
TEST_CONTEXT = re.compile(r"test|passing|green|suite|assertion", re.I)


def _is_chain_or_decimal(text, m):
    """`5/960/966/982` and `314.80 / 312.32` are not ratios. Reject a pair that is part of a
    longer slash chain, or whose either half is the fractional part of a decimal."""
    before = text[max(0, m.start() - 2): m.start()]
    after = text[m.end(): m.end() + 2]
    return ("/" in before or "." in before or "/" in after.lstrip()
            or after.lstrip()[:1] == ".")


def test_no_counted_document_quotes_a_mismatched_pass_ratio():
    problems = []
    for rel in gate.COUNTED_DOCS:
        p = ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()
        for m in RATIO.finditer(text):
            a, b = int(m.group(1)), int(m.group(2))
            if a == b:
                continue
            # Only judge pairs where BOTH halves are plausible suite sizes.
            if not (60 <= a <= 999 and 60 <= b <= 999):
                continue
            if _is_chain_or_decimal(text, m):
                continue
            line = text[: m.start()].count("\n") + 1
            context = lines[line - 1] if line - 1 < len(lines) else ""
            if not TEST_CONTEXT.search(context):
                continue
            if "count-gate:historical" in text[m.end(): m.end() + 40]:
                continue
            problems.append("%s:%d quotes %d/%d - a pass ratio whose halves disagree"
                            % (rel, line, a, b))
    assert not problems, (
        "Impossible pass ratio in a counted document:\n  " + "\n  ".join(sorted(set(problems)))
        + "\n\nA 'passed/total' stat must have equal halves. If the count updater rewrote only "
          "one half, fix BOTH - and note that test_doc_counts.py cannot see a mismatched ratio, "
          "so this is the only gate that catches it."
    )
