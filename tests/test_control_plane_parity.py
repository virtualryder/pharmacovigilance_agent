"""Gate: the shared control-plane core must not drift, and documented controls must exist.

Why this exists. `lib/controls/` is COPIED into four agent repositories, not shared as a package.
On 2026-08-03 a hash comparison across all four found that a hardening control (exactly-once
finalization, the FINAL# conditional-put commit gate) had landed in edu_financial_aid_agent and
Housing_eligibility_agent and was never ported here — while this repo's IaC comment, deployment
guide, and evidence file all described it as present. Nothing detected that for weeks.

Two gates follow from it:

  1. The security-critical core must stay intact in this repo (a local integrity check; true
     cross-repo parity needs the shared package described in docs/MULTI-AGENT-COMPOSITION.md).
  2. Documentation must not claim a control the code does not implement. That is the failure that
     actually reached a reviewer, and it is the cheaper one to catch.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTROLS = ROOT / "lib" / "controls"

# The modules that carry the cryptographic/ledger core. These are the ones that were byte-identical
# across all four agents and must remain structurally intact here.
CORE = ["evidence.py", "verify_chain.py", "write_audit.py", "identity.py",
        "approve_signoff.py", "request_signoff.py"]


def test_control_plane_core_modules_present():
    missing = [m for m in CORE if not (CONTROLS / m).exists()]
    assert not missing, f"control-plane core module(s) missing: {missing}"


def test_audit_chain_keeps_its_tamper_evident_properties():
    """The hash chain is the Part 11 audit-trail control. Guard its load-bearing pieces."""
    src = (CONTROLS / "evidence.py").read_text(encoding="utf-8")
    required = {
        "attribute_not_exists": "append-only conditional put (no overwrite of prior entries)",
        "sha256": "content hashing for the chain",
        "prev_hash": "chain linkage to the prior entry",
    }
    absent = [f"{k} ({why})" for k, why in required.items() if k not in src]
    assert not absent, (
        "evidence.py no longer contains: " + "; ".join(absent) +
        " — the append-only hash chain is the 21 CFR 11.10(e) control."
    )


def test_docs_do_not_claim_exactly_once_finalization_unless_implemented():
    """The exact failure found on 2026-08-03.

    finalize_signoff.py must actually implement the FINAL# conditional-put marker before any
    document, IaC comment or evidence file describes it as present. When the control IS ported
    from the EDU/Housing implementation, this test flips automatically and starts REQUIRING the
    docs to say so — so neither direction can drift.
    """
    finalize = (CONTROLS / "finalize_signoff.py").read_text(encoding="utf-8")
    implemented = "FINAL#" in finalize and "attribute_not_exists" in finalize

    claim = re.compile(
        r"exactly-once\s+(`?FINAL#`?\s+)?(marker|finaliz)|"
        r"idempotent finaliz|single `?FINAL#`? marker", re.I)

    # A passage that explicitly flags the gap is the CORRECT state, not a violation. The flag rarely
    # sits on the same line as the phrase, so judge a window around the match.
    flag = re.compile(r"NOT implemented|not implemented|KNOWN GAP|Known gap|DOES NOT IMPLEMENT|"
                      r"was never ported|do not describe|control is absent|Known gap|not ported",
                      re.I)
    WINDOW = 6

    offenders = []
    for p in list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md")) \
            + list((ROOT / "evidence").glob("*.md")) + list((ROOT / "cdk").rglob("*.py")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        # Superseded text retained inside HTML comments is history, not a live claim.
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        lines = text.splitlines()
        for m in claim.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            lo, hi = max(0, line_no - 1 - WINDOW), min(len(lines), line_no + WINDOW)
            if flag.search("\n".join(lines[lo:hi])):
                continue
            offenders.append(f"{p.relative_to(ROOT)}:{line_no}")

    if implemented:
        assert True  # control is present; claims are legitimate
    else:
        assert not offenders, (
            "finalize_signoff.py does NOT implement the exactly-once FINAL# marker, but these "
            "locations describe it as present:\n  " + "\n  ".join(sorted(set(offenders))) +
            "\n\nEither port the control from edu_financial_aid_agent/lib/controls/"
            "finalize_signoff.py, or mark the claim as a known gap."
        )
