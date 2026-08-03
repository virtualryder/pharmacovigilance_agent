#!/usr/bin/env python3
"""Cross-repo governance-core parity — under the DEPENDENCY model.

WHAT CHANGED, AND WHY THIS FILE LOOKS DIFFERENT NOW
---------------------------------------------------
This tool used to hash `lib/controls/*.py` in each agent repo and compare the copies. That was the
right check for a world where the control plane was copied into four repositories. It is the wrong
check now: as of 2026-08-03 the agents no longer carry the core. They install it:

    governed-core @ https://github.com/virtualryder/governed-core/releases/download/
                    v<ver>/governed_core-<ver>-py3-none-any.whl  --hash=sha256:<digest>

So the question worth asking changed. It is no longer "do four copies still match?" — pip and the
artifact hash answer that. It is:

    1. Does every repo pin the SAME version and the SAME artifact hash?
    2. Is every pin hash-locked at all? A requirement without --hash is a copy with extra steps:
       pip would accept whatever happens to be at that URL.
    3. Has any repo quietly reintroduced a core module into lib/controls? Such a file shadows the
       package on sys.path and re-opens the drift by the back door.

WHY THE ORIGINAL CHECK EXISTED (keep this history — it is the justification for the gate)
----------------------------------------------------------------------------------------
The exactly-once `FINAL#` finalization control existed in edu_financial_aid_agent and
Housing_eligibility_agent and was absent from pharmacovigilance_agent and benefits_eligibility_agent,
while all four `core.lock` files recorded the SAME tree hash — i.e. all four claimed an identical
core. Every repo's CI was green. For pharmacovigilance the absence was an ICSR double-reporting risk.

Then, when the agents were finally compared against the package they derive from, all four agreed
with each other and ALL FOUR differed from the package, which had no exactly-once control at all.
The verticals were AHEAD of their own source. The same shape turned up a second time with
`signoff_register` (GA-5 duplicate-submission protection: present in two of four, absent from the
package and from the other two) — promoted into governed-core 1.4.0.

Usage:

    python tools/check_core_parity.py ../pharmacovigilance_agent ../benefits_eligibility_agent \
                                      ../edu_financial_aid_agent ../Housing_eligibility_agent

Exit 0 = every repo pins the same hash-locked core and none has re-copied a core module.
Exit 1 = divergence, itemised.
"""
import argparse
import pathlib
import re
import sys

# Modules that must come from the package in every vertical. A file with one of these names inside a
# repo's lib/controls/ shadows the package on sys.path — that is how a local "fix" gets made and
# never reaches the other three.
CORE_MODULE_NAMES = [
    "evidence", "verify_chain", "write_audit", "identity",
    "approve_signoff", "request_signoff", "idp_group_mapper", "mcp_client",
    "finalize_signoff", "signoff_register",
]

# Legitimately domain-specific: these encode each domain's pipeline or rules and are expected to
# differ. Reported for visibility, never failed on.
DOMAIN_SHAPED = ["workflow_guards.py", "mask_pii.py", "case_store.py", "sanitized.py",
                 "provenance.py", "ingest_case.py", "tenancy.py", "readability.py"]

REQ = "requirements-core.txt"
WHEEL_RE = re.compile(r"governed_core-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl")
HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def read_pin(repo):
    """Return (version, sha256, error) for a repo's pinned core."""
    p = repo / REQ
    if not p.exists():
        return None, None, "%s is missing — the core dependency is undeclared" % REQ
    text = p.read_text(encoding="utf-8")
    ver = WHEEL_RE.search(text)
    dig = HASH_RE.search(text)
    if not ver:
        return None, None, "%s does not pin an exact governed_core wheel version" % REQ
    if not dig:
        return ver.group(1), None, (
            "%s has no --hash= pin; pip would accept whatever is at that URL, which makes this a "
            "copy with extra steps rather than a dependency" % REQ)
    return ver.group(1), dig.group(1), None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos", nargs="*", help="paths to the agent repos")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    repos = [pathlib.Path(r).resolve() for r in args.repos]
    repos = [r for r in repos if r.exists()]
    if len(repos) < 2:
        print("need at least two repos to compare; pass their paths as arguments")
        return 0

    print("comparing %d repos:" % len(repos))
    for r in repos:
        print("  -", r.name)
    print()

    failures = []

    print("PINNED CORE (every vertical must pin the same hash-locked artifact)")
    pins = {}
    for r in repos:
        ver, dig, err = read_pin(r)
        pins[r.name] = (ver, dig)
        if err:
            failures.append("%s: %s" % (r.name, err))
            print("  FAIL  %-30s %s" % (r.name, err))
        elif not args.quiet:
            print("  ok    %-30s %s  sha256:%s..." % (r.name, ver, dig[:12]))

    versions = {v for v, _ in pins.values() if v}
    digests = {d for _, d in pins.values() if d}
    if len(versions) > 1:
        detail = ", ".join("%s=%s" % (n, v) for n, (v, _) in sorted(pins.items()))
        failures.append("core VERSION disagrees across repos — %s" % detail)
        print("  FAIL  %-30s %d different versions: %s"
              % ("version agreement", len(versions), detail))
    elif not args.quiet and versions:
        print("  ok    %-30s all repos pin %s" % ("version agreement", sorted(versions)[0]))

    if len(digests) > 1:
        failures.append("core artifact HASH disagrees across repos — same version, different "
                        "artifact, which should be impossible for a published release")
        print("  FAIL  %-30s %d different artifact hashes" % ("hash agreement", len(digests)))
    elif not args.quiet and digests:
        print("  ok    %-30s all repos pin the same artifact" % "hash agreement")

    print()
    print("NO CORE MODULE MAY BE COPIED BACK (a local copy shadows the package on sys.path)")
    for r in repos:
        ctrl = r / "lib" / "controls"
        back = sorted(n for n in CORE_MODULE_NAMES if (ctrl / (n + ".py")).exists())
        if back:
            failures.append("%s has re-copied core module(s) into lib/controls: %s"
                            % (r.name, ", ".join(back)))
            print("  FAIL  %-30s %s" % (r.name, ", ".join(back)))
        elif not args.quiet:
            print("  ok    %-30s no core module copied locally" % r.name)

    print()
    print("DOMAIN-SHAPED (divergence expected; shown for visibility)")
    for rel in DOMAIN_SHAPED:
        present = [r.name for r in repos if (r / "lib" / "controls" / rel).exists()]
        print("  %-24s present in %d/%d repos" % (rel, len(present), len(repos)))

    print()
    if failures:
        print("PARITY FAILED — %d divergence(s):" % len(failures))
        for f in failures:
            print("  -", f)
        print()
        print("Bump every repo's requirements-core.txt to the same version AND hash together, and "
              "delete any core module that has reappeared under lib/controls. A control that exists "
              "in one regulated workload and not another is how the exactly-once finalization gap "
              "reached production docs — twice.")
        return 1

    print("PARITY OK — every repo pins the same hash-locked core and none has copied one back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
