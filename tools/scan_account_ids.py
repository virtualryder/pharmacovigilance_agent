#!/usr/bin/env python3
"""Fail the build if a real AWS account id is committed.

WHY THIS EXISTS
---------------
On 2026-08-03 a portfolio-wide scan found the live account id committed seven times in this repo, in
two files that are *generated at deploy time* and were never meant to be tracked:

  core/src/governed_core/runtime/.bedrock_agentcore.yaml   (written by the AgentCore CLI)
  core/src/governed_core/runtime/ssm-pol.json              (written by runtime/_obs_setup.sh)

Every sibling agent repo already gitignored both files, so the same artifacts were clean there. Only
this repo tracked them. Nothing detected it because nothing was looking: the rule "account ids are
redacted to 111122223333 in committed files" existed only as a habit, never as a gate.

This makes it a gate. It scans TRACKED content only (`git grep --cached`), so a developer's local
deploy artifacts are irrelevant — which is the correct boundary, since those files must exist locally
for a real deploy to work.

WHAT COUNTS AS A FINDING
------------------------
Any 12-digit number appearing in an AWS account position — an ARN's account field, an ECR/S3 hostname,
or an explicit `account:` key — that is not one of the documented placeholders.

    python tools/scan_account_ids.py            # scan tracked files, exit 1 on any finding
    python tools/scan_account_ids.py --all      # also scan untracked files (local hygiene check)
"""
import re
import subprocess
import sys

# Placeholders that are SUPPOSED to appear. 111122223333 is this portfolio's redaction target;
# 123456789012 is the placeholder used throughout AWS's own documentation.
# AWS publishes a fixed set of example account ids for documentation; a test that must show a
# SECOND account (to prove the renderer substitutes real principals rather than leaving the
# redaction target in place) has to use one of them, or this gate and that test cannot both pass.
ALLOWED = {"111122223333", "123456789012", "000000000000",
           "444455556666", "555555555555", "666666666666",
           "777788889999", "888888888888", "999999999999"}

# 12 digits in a position that actually denotes an account. A bare 12-digit number elsewhere (a
# timestamp, a hash fragment, a test fixture id) is not a finding, and treating it as one would train
# people to ignore this check.
PATTERNS = [
    # {1,3} colons: S3 and IAM ARNs leave the region and/or account fields EMPTY
    # (arn:aws:s3:::864...-bucket), so requiring exactly one colon missed them.
    re.compile(r"arn:aws[a-z\-]*:[a-z0-9\-]*:[a-z0-9\-]*:{1,3}(\d{12})"),
    re.compile(r"\b(\d{12})\.dkr\.ecr\."),
    # L67b: this was `\baccount\b\s*[:=]\s*['"]?(\d{12})` and could not match `"account": "864..."`
    # - the JSON spelling - because \s* cannot cross the closing quote of the KEY. Nor could the
    # prose pattern below, whose separator class has no quote in it. So the single most likely way an
    # account id appears in this repo's evidence (a JSON key) was the one shape the gate could not
    # see, along with "account_id" and "awsAccountId", the two spellings the AWS CLI actually emits.
    # Found 2026-09-09 by writing tests/test_scan_account_ids.py, not by the gate ever firing. Same
    # family as L38-L49 and L40's own comment two lines down: this control was narrow enough to
    # never false-positive and therefore narrow enough to never fire.
    re.compile(r"(?i)\b(?:aws[_\-]?)?account(?:[_\-]?(?:id|number))?\b[\s:=*_`'\"\-]{0,6}(\d{12})\b"),
    re.compile(r"[a-z0-9\-]+-(\d{12})-[a-z0-9\-]+"),   # e.g. codebuild-sources-<acct>-us-east-1
    # L40: the three shapes above are how MACHINES write an account id. Every evidence file writes
    # it the way a PERSON does - "Account **111122223333** - us-east-1" in a header, or
    # "account 111122223333 / us-east-1" in prose - and none of those patterns match that, because
    # they all require a colon, an ARN, or a trailing hyphenated segment. Seven committed evidence
    # files carried the live account id in plain prose while this gate reported "clean". A control
    # that is narrow enough to never false-positive is also narrow enough to never fire.
    re.compile(r"(?i)\baccount\b[\s:=*_`\-]{0,4}(\d{12})\b"),
    # ...and at the END of a resource name, where pattern 4 above needs a trailing segment it
    # does not have: bucket `ben-mt2-pha-a-worm-111122223333`
    re.compile(r"[a-z0-9]-(\d{12})\b"),
]

# L67: a UUID's final field is 12 HEX characters, and when they happen to be all decimal it is
# indistinguishable from an account id sitting after a hyphen - which is exactly what the last
# pattern matches. Lambda request ids quoted inside evidence excerpts hit this:
# "35c572b3-d0da-404e-9377-039106473245". Found while committing the 2026-09-09 gate evidence; the
# scan failed and the obvious "fix" was to rewrite the digits, i.e. to corrupt an evidence file to
# satisfy an instrument that was wrong. Excluding this shape costs no coverage: an account id in an
# ARN, an ECR hostname, an `account:` key or prose is caught by a different pattern, and none of
# those positions can be preceded by four hex fields of 8-4-4-4. Proven by
# tests/test_scan_account_ids.py, which asserts every real shape STILL fires.
UUID_TAIL = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-$")


def in_uuid(content, start):
    """True when the 12 digits starting at `start` are the last field of a UUID."""
    return bool(UUID_TAIL.search(content[:start]))


# Binary/vendored paths where a match is noise.
SKIP = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".pptx", ".docx", ".xlsx", ".zip", ".ico")


def scan(lines):
    findings = []
    for raw in lines:
        # `git grep -n` output is "path:lineno:content"; plain file reads have no prefix.
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        path, lineno, content = parts
        if path.lower().endswith(SKIP):
            continue
        for pat in PATTERNS:
            for m in pat.finditer(content):
                acct = m.group(1)
                if acct in ALLOWED or in_uuid(content, m.start(1)):
                    continue
                findings.append((path, lineno, acct, content.strip()[:120]))
    return findings


def main():
    scan_all = "--all" in sys.argv
    # --cached scans committed content (the gate). --all additionally walks untracked and ignored
    # files, which is the local-hygiene view: `git grep` skips those by default, so omitting --cached
    # alone would silently report "clean" while the deploy artifacts on disk were full of the id.
    cmd = ["git", "grep", "-I", "-n", "-E", r"[0-9]{12}"]
    cmd.insert(2, "--untracked" if scan_all else "--cached")
    if scan_all:
        cmd.insert(3, "--no-exclude-standard")
    out = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    # git grep exits 1 when there are no matches at all — that is a pass, not an error.
    findings = scan(out.stdout.splitlines()) if out.stdout else []

    if not findings:
        print("account-id scan: clean (%s)" % ("working tree" if scan_all else "tracked content"))
        return 0

    print("account-id scan FAILED — %d real AWS account id(s) in committed content:\n"
          % len(findings))
    for path, lineno, acct, content in findings:
        print("  %s:%s  account %s" % (path, lineno, acct))
        print("      %s" % content)
    print("\nRedact to 111122223333. If the file is a deploy-time artifact (.bedrock_agentcore.yaml,")
    print("ssm-pol.json, or similar), it should be gitignored and `git rm --cached`d instead — those")
    print("files must exist locally for a real deploy and must never be tracked.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
