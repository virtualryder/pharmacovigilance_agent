"""negation — is a term ACTUALLY asserted in this text, or is it negated?

Live-found on the full-portfolio gate, 2026-09-06 (register L20). The benefits extractor decided
categorical eligibility with a bare token search:

    categorical = bool(re.search(r"\b(ssi|tanf|general assistance)\b", low))

An application reading "... no TANF." matched `tanf` and set categorical_eligibility=True.
Categorical eligibility SKIPS the income/resource test, so a negation-blind match silently converts
an income-tested case into an automatic approval — a materially wrong legal determination drawn
from text that says the opposite. No unit test caught it; the contextual-grounding guardrail did,
by refusing to draft a notice its own case facts contradicted.

Every pack extracts decision-relevant flags from free text the same way, so the same class of bug
existed in housing (elderly / disabled) and pharmacovigilance (seriousness criteria, expectedness).
This module is the ONE implementation all of them use, staged flat into the Lambda bundle from
lib/controls/ — a copy per pack is exactly how the original bug would come back.

What it does NOT do: this is a deterministic clause-window heuristic, not clinical or legal NLP.
It is deliberately FAIL-CLOSED for grants — an ambiguous or negated mention never counts as an
assertion — so its errors fall on the side of "not asserted", which routes a case to a human
rather than to an automatic approval. Callers whose flag is protective rather than permissive
(pharmacovigilance seriousness, where over-flagging is the safe direction) should say so explicitly
at the call site rather than inverting the default here.
"""
import re

# Cues that negate a term mentioned nearby, in either direction:
#   "no TANF", "TANF: none", "denied SSI", "SSI - terminated", "not receiving general assistance"
NEGATION_CUES = (
    "no ", "not ", "non-", "never", "none", "without", "denied", "denies", "denial",
    "ineligible", "terminated", "discontinued", "closed", "ended", "no longer",
    "does not", "doesn't", "did not", "didn't", "is not", "isn't", "are not", "aren't",
    "was not", "wasn't", "were not", "weren't", "declined", "withdrew", "withdrawn",
    "expired", "n/a", "refused", "negative for", "denies any", "rules out", "ruled out",
)

# A clause boundary ends the negation window: "no TANF. Receives SSI" is still an SSI assertion.
# A colon breaks the window only when looking BACKWARD — forward it usually introduces the value
# ("TANF: none"), so the negation must still be visible after it.
_CLAUSE_BREAK_BEFORE = ".;:\n|"
_CLAUSE_BREAK_AFTER = ".;\n|"

_WIDTH_BEFORE = 40
_WIDTH_AFTER = 40


def clause_before(low, start, width=_WIDTH_BEFORE):
    """The text just before a match, back to the nearest clause boundary (max `width` chars)."""
    seg = low[max(0, start - width):start]
    for ch in _CLAUSE_BREAK_BEFORE:
        seg = seg.rsplit(ch, 1)[-1]
    return seg


def clause_after(low, end, width=_WIDTH_AFTER):
    """...and just after it. Wide enough to see a trailing status ("SSI benefits were terminated")."""
    seg = low[end:end + width]
    for ch in _CLAUSE_BREAK_AFTER:
        seg = seg.split(ch, 1)[0]
    return seg


def is_negated(low, start, end):
    """True when a negation cue sits in the clause around low[start:end]."""
    window = clause_before(low, start) + " " + clause_after(low, end)
    return any(cue in window for cue in NEGATION_CUES)


def asserted(text, pattern):
    """True when `pattern` matches `text` at least once WITHOUT a negation cue around that match.

    `pattern` is a regex, matched case-insensitively. Fail-closed: a term mentioned only in negated
    form returns False, so an extracted flag is never granted on text that denies it.
    """
    low = (text or "").lower()
    for m in re.finditer(pattern, low, flags=re.IGNORECASE):
        if not is_negated(low, m.start(), m.end()):
            return True
    return False


def asserted_flags(text, patterns):
    """{name: asserted(text, pattern)} for a dict of named patterns."""
    return {name: asserted(text, pat) for name, pat in patterns.items()}
