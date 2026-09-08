#!/usr/bin/env python3
"""Compare a re-scanned secrets baseline against the committed one, ignoring the timestamp.

WHY THIS EXISTS
---------------
The Security workflow did:

    detect-secrets scan --baseline .secrets.baseline
    git diff --exit-code .secrets.baseline \
      || { echo "::error::new potential secret(s) not in .secrets.baseline"; exit 1; }

`detect-secrets scan` rewrites `generated_at` with the current UTC time on every run, so
`git diff --exit-code` ALWAYS reported a change and the step ALWAYS failed - with a message
accusing the developer of adding a secret. The gate could not pass even on an untouched tree.

A control that cries wolf on every run is worse than no control: it teaches people to skip the
step. This compares what the baseline is FOR - the findings - and says precisely what is new.

    python tools/check_secrets_baseline.py <committed.json> <rescanned.json>
"""
import io
import json
import sys

# generated_at is a clock. `version` is the TOOL's own version string, which legitimately changes
# when the pinned scanner is upgraded - that is a maintenance event, not a secret, and failing on it
# would be the same cry-wolf defect in a new place. What must NOT change silently is the detection
# coverage: plugins_used and filters_used are still compared and still fail hard, because quietly
# dropping a detector would hide every secret it would have found.
IGNORED_TOP_LEVEL = {"generated_at", "version"}

# `detect_secrets.filters.common.is_baseline_file` is not a detector. It exists so the baseline
# does not flag ITSELF, and it records the baseline's own path - which is whatever path the scan
# was invoked with. A baseline generated as `.secrets.baseline` and one generated at an absolute
# path disagree on that field alone, and comparing it as DETECTION COVERAGE made the BLOCKING
# secret gate fail on three packs with "coverage changed (filters_used)" when nothing about the
# detection had changed. Compare the filter's presence, not the machine-relative path it carries.
_PATH_DEPENDENT = {"detect_secrets.filters.common.is_baseline_file": ("filename",)}


def _normalise(doc, key):
    """Strip machine-relative bookkeeping so two baselines of the same tree compare equal."""
    value = doc.get(key)
    if key != "filters_used" or not isinstance(value, list):
        return value
    out = []
    for f in value:
        if isinstance(f, dict):
            drop = _PATH_DEPENDENT.get(f.get("path"), ())
            f = {k: v for k, v in f.items() if k not in drop}
        out.append(f)
    return sorted(out, key=lambda f: json.dumps(f, sort_keys=True))


def _findings(doc):
    """{(filename, type, hashed_secret)} - line numbers move when a file is edited above the
    finding, and a moved line is not a new secret."""
    out = set()
    for filename, entries in (doc.get("results") or {}).items():
        for e in entries:
            out.add((filename.replace("\\", "/"), e.get("type"), e.get("hashed_secret")))
    return out


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    committed = json.load(io.open(argv[1], encoding="utf-8"))
    rescanned = json.load(io.open(argv[2], encoding="utf-8"))

    new = _findings(rescanned) - _findings(committed)
    gone = _findings(committed) - _findings(rescanned)

    if committed.get("version") != rescanned.get("version"):
        print("note: detect-secrets version changed %s -> %s; detection coverage is compared below."
              % (committed.get("version"), rescanned.get("version")))

    for key in sorted(set(committed) | set(rescanned)):
        if key in IGNORED_TOP_LEVEL or key == "results":
            continue
        if _normalise(committed, key) != _normalise(rescanned, key):
            print("::error::secrets baseline DETECTION COVERAGE changed (%s) - a dropped plugin or "
                  "filter hides every secret it would have found. Re-baseline deliberately." % key)
            return 1

    if new:
        print("::error::%d potential secret(s) not in .secrets.baseline:" % len(new))
        for filename, kind, h in sorted(new):
            print("  %s  [%s]  %s" % (filename, kind, h))
        print("Review each one. If they are fixtures rather than credentials, re-baseline with")
        print("`detect-secrets scan --baseline .secrets.baseline` and say WHY in the commit.")
        return 1

    if gone:
        print("note: %d baseline entry/entries no longer found (file deleted or secret removed)."
              % len(gone))
    print("secrets baseline: no new findings (%d tracked)" % len(_findings(committed)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
