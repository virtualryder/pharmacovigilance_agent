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
ALLOWED = {"111122223333", "123456789012", "000000000000"}

# 12 digits in a position that actually denotes an account. A bare 12-digit number elsewhere (a
# timestamp, a hash fragment, a test fixture id) is not a finding, and treating it as one would train
# people to ignore this check.
PATTERNS = [
    re.compile(r"arn:aws[a-z\-]*:[a-z0-9\-]*:[a-z0-9\-]*:(\d{12})"),
    re.compile(r"\b(\d{12})\.dkr\.ecr\."),
    re.compile(r"\baccount\b\s*[:=]\s*['\"]?(\d{12})"),
    re.compile(r"[a-z0-9\-]+-(\d{12})-[a-z0-9\-]+"),   # e.g. codebuild-sources-<acct>-us-east-1
]

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
            for acct in pat.findall(content):
                if acct not in ALLOWED:
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
