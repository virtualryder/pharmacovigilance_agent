"""L46: a base image pinned by digest is a supply-chain control that expires.

The runtime Dockerfile pins python:3.12-slim by digest, resolved 2026-07 - which is right, a
mutable tag is not a pin. But a digest freezes the OS packages too, so the pin quietly accumulates
every Debian advisory published after it. On 2026-09-08 trivy - working for the first time, see
L45 - reported 30 FIXABLE HIGH/CRITICAL findings against that base, with 0 in the Python packages.
Nothing was wrong with the pinning discipline; the pin had simply aged two months.

Pinning without a refresh cadence turns a control into a liability, and nothing was measuring the
age. This test does, offline: it reads the resolve date the Dockerfile already records in its own
comment and fails once the pin is older than MAX_AGE_DAYS. No network, so it works in any runner
and cannot flake.

It deliberately does NOT check the digest against a registry. That would need network in the test
suite and would fail for reasons unrelated to this repository.
"""
import datetime
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(ROOT, "lib", "runtime", "Dockerfile")

# Debian security updates land continuously; a quarter is the outer edge of defensible for an image
# that reaches production. Shorten it, never lengthen it to make a red build go away.
MAX_AGE_DAYS = 90


def _resolved_date(text):
    """The Dockerfile records when the digest was resolved: '... resolved 2026-07.'"""
    m = re.search(r"resolved\s+(\d{4})-(\d{2})(?:-(\d{2}))?", text)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    return datetime.date(y, mo, d)


def test_the_dockerfile_still_pins_by_digest():
    """The pin itself is the control; losing it is worse than it being stale."""
    text = open(DOCKERFILE, encoding="utf-8").read()
    assert re.search(r"^FROM\s+\S+@sha256:[0-9a-f]{64}", text, re.M), (
        "the runtime base image must be pinned by digest, not by a mutable tag")


def test_the_dockerfile_records_when_the_digest_was_resolved():
    text = open(DOCKERFILE, encoding="utf-8").read()
    assert _resolved_date(text) is not None, (
        "the Dockerfile must record when the digest was resolved (e.g. 'resolved 2026-07'), "
        "otherwise nothing can tell whether the pin has aged out")


def test_the_pinned_base_image_is_not_stale():
    text = open(DOCKERFILE, encoding="utf-8").read()
    resolved = _resolved_date(text)
    age = (datetime.date.today() - resolved).days
    assert age <= MAX_AGE_DAYS, (
        "the runtime base image digest was resolved %s, %d days ago (limit %d). A digest pin "
        "freezes the OS packages, so it accrues every advisory published since. Re-resolve "
        "python:3.12-slim on a machine with a container client, update the FROM digest AND the "
        "'resolved' date, and let trivy confirm. Do not raise MAX_AGE_DAYS to go green."
        % (resolved.isoformat(), age, MAX_AGE_DAYS))
