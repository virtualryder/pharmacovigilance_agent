#!/usr/bin/env python3
"""Cross-repo governance-core parity check.

WHY THIS EXISTS
---------------
Each agent repo already ships `lib/verify_core.py` + `lib/core.lock`, which verify that the repo's
own governance core matches its own pinned lock. That is an INTRA-repo integrity check. The lock's
header states a stronger intent — "Every vertical carries this identical core... sync the identical
core to every vertical" — but nothing ever verified it ACROSS repos.

The consequence, found 2026-08-03: the exactly-once `FINAL#` finalization control existed in
edu_financial_aid_agent and Housing_eligibility_agent and was absent from pharmacovigilance_agent and
benefits_eligibility_agent, while all four `core.lock` files still recorded the SAME tree hash
(cb0794c9...), i.e. all four claimed to carry an identical core. For pharmacovigilance the absence was
an ICSR double-reporting risk. Nothing detected it for weeks, because per-repo integrity was never the
same question as cross-repo parity.

This tool answers the cross-repo question. Run it from any checkout, pointing at the sibling repos.

    python tools/check_core_parity.py ../benefits_eligibility_agent ../edu_financial_aid_agent \
                                      ../Housing_eligibility_agent

Exit 0 = the CORE set is identical everywhere. Exit 1 = divergence, itemised.

WHAT IS AND IS NOT EXPECTED TO MATCH
------------------------------------
CORE_IDENTICAL — the security-critical modules that must be byte-identical in every vertical. A
difference here is a defect, full stop.

DOMAIN_SHAPED — modules that legitimately differ because they encode each domain's pipeline or rules.
Reported for visibility, never failed on.
"""
import argparse
import hashlib
import pathlib
import sys

# Must be byte-identical everywhere: the hash chain, chain verification, the audit writer, identity
# verification, and the separation-of-duties approval path.
CORE_IDENTICAL = [
    "lib/controls/evidence.py",
    "lib/controls/verify_chain.py",
    "lib/controls/write_audit.py",
    "lib/controls/identity.py",
    "lib/controls/approve_signoff.py",
    "lib/controls/request_signoff.py",
    "lib/controls/idp_group_mapper.py",
    "lib/controls/mcp_client.py",
]

# Files whose MECHANISM must exist everywhere but whose commentary is legitimately domain-specific
# (PV frames the risk as ICSR double-reporting, benefits as committing an adverse action twice).
# Byte-equality is the wrong test for these — assert the control is PRESENT instead. This is the
# check that would have caught the 2026-08-03 exactly-once gap.
CORE_BEHAVIOUR = {
    "lib/controls/finalize_signoff.py": [
        ("_exactly_once_marker", "the exactly-once commit-gate function"),
        ("FINAL#", "the conditional-put marker key"),
        ("attribute_not_exists", "the conditional put that makes it exactly-once"),
    ],
    "lib/controls/evidence.py": [
        ("attribute_not_exists", "append-only conditional put on the audit chain"),
        ("prev_hash", "hash-chain linkage"),
    ],
}

# Legitimately domain-specific. Reported, not failed.
DOMAIN_SHAPED = [
    "lib/controls/workflow_guards.py",
    "lib/controls/mask_pii.py",
    "lib/controls/case_store.py",
    "lib/controls/sanitized.py",
    "lib/controls/provenance.py",
    "lib/controls/signoff_register.py",
]


def sha(path):
    """Hash with normalised line endings, matching verify_core.py, so a Windows checkout and a
    Linux CI runner agree."""
    if not path.exists():
        return None
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("others", nargs="*", help="paths to sibling agent repos")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    here = pathlib.Path(__file__).resolve().parent.parent
    repos = [here] + [pathlib.Path(o).resolve() for o in args.others]
    repos = [r for r in repos if r.exists()]

    if len(repos) < 2:
        print("need at least two repos to compare; pass sibling paths as arguments")
        return 0

    print("comparing %d repos:" % len(repos))
    for r in repos:
        print("  -", r.name)
    print()

    failures = []

    print("CORE (must be identical everywhere)")
    for rel in CORE_IDENTICAL:
        hashes = {r.name: sha(r / rel) for r in repos}
        distinct = {h for h in hashes.values() if h is not None}
        missing = [n for n, h in hashes.items() if h is None]
        if missing:
            failures.append("%s MISSING in: %s" % (rel, ", ".join(missing)))
            print("  FAIL  %-42s missing in %s" % (rel, ", ".join(missing)))
        elif len(distinct) > 1:
            groups = {}
            for n, h in hashes.items():
                groups.setdefault(h[:8], []).append(n)
            detail = " | ".join("%s: %s" % (k, ",".join(v)) for k, v in groups.items())
            failures.append("%s DIVERGED — %s" % (rel, detail))
            print("  FAIL  %-42s %d variants — %s" % (rel, len(distinct), detail))
        elif not args.quiet:
            print("  ok    %-42s identical" % rel)

    print()
    print("CORE BEHAVIOUR (mechanism must be present in every repo; commentary may differ)")
    for rel, required in CORE_BEHAVIOUR.items():
        for token, why in required:
            absent = []
            for r in repos:
                p = r / rel
                if not p.exists() or token not in p.read_text(encoding="utf-8", errors="ignore"):
                    absent.append(r.name)
            if absent:
                failures.append("%s is MISSING %r (%s) in: %s"
                                % (rel, token, why, ", ".join(absent)))
                print("  FAIL  %-30s %-24s missing in %s"
                      % (rel.split("/")[-1], token, ", ".join(absent)))
            elif not args.quiet:
                print("  ok    %-30s %-24s present everywhere"
                      % (rel.split("/")[-1], token))

    print()
    print("DOMAIN-SHAPED (divergence expected; shown for visibility)")
    for rel in DOMAIN_SHAPED:
        hashes = {r.name: sha(r / rel) for r in repos}
        distinct = {h for h in hashes.values() if h is not None}
        print("  %-44s %d variant(s)" % (rel, len(distinct)))

    print()
    if failures:
        print("PARITY FAILED — %d core divergence(s):" % len(failures))
        for f in failures:
            print("  -", f)
        print()
        print("A file in the CORE set differing between agents means a control exists in one "
              "regulated workload and not another. That is how the exactly-once finalization gap "
              "reached production docs. Port the newer implementation, re-run "
              "lib/regen_core_lock.py in each repo, and re-run this check.")
        return 1

    print("PARITY OK — the core set is identical across all compared repos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
