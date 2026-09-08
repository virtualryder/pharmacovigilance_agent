"""L44: a BLOCKING gate that cannot read its own config is not a gate.

The Security workflow's secret-detection step is marked BLOCKING. It had never scanned anything:
`.secrets.baseline` was written as UTF-16LE with a byte-order mark, and detect-secrets does
`json.loads(f.read())` on a utf-8 handle, so it aborted with

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0

before looking at a single file. The step failed, the job failed, and because the job had been
failing for other reasons too, nobody separated "the gate found something" from "the gate could not
start". Same encoding disease as L36 - a PowerShell redirect writes UTF-16 by default on Windows.

The lesson generalises past this one file, so the test does too: every machine-readable gate config
in the repo must be UTF-8, BOM-free, and parse as what it claims to be.
"""
import io
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (path, loader) - every config a BLOCKING control reads before it can run.
GATE_CONFIGS = [
    (".secrets.baseline", "json"),
    (".checkov.baseline", "text"),
    ("requirements-core.txt", "text"),
]


def _existing():
    return [(p, kind) for p, kind in GATE_CONFIGS if os.path.exists(os.path.join(ROOT, p))]


@pytest.mark.parametrize("rel,kind", _existing(), ids=lambda v: v if isinstance(v, str) else "")
def test_gate_config_has_no_byte_order_mark(rel, kind):
    raw = open(os.path.join(ROOT, rel), "rb").read(4)
    for bom, name in ((b"\xff\xfe", "UTF-16LE"), (b"\xfe\xff", "UTF-16BE"),
                      (b"\xef\xbb\xbf", "UTF-8-BOM")):
        assert not raw.startswith(bom), (
            "%s starts with a %s BOM. The tool that reads it opens it as UTF-8 and will abort "
            "before scanning anything - a blocking gate that cannot read its config silently "
            "stops being a gate (L44)." % (rel, name))


@pytest.mark.parametrize("rel,kind", _existing(), ids=lambda v: v if isinstance(v, str) else "")
def test_gate_config_decodes_as_utf8(rel, kind):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:                    # pragma: no cover - the bug being pinned
        pytest.fail("%s is not valid UTF-8: %s" % (rel, exc))
    if kind == "json":
        json.loads(text)


def test_the_secrets_baseline_is_the_shape_detect_secrets_expects():
    """Parsing is necessary but not sufficient - it must be a baseline, not just any JSON."""
    with io.open(os.path.join(ROOT, ".secrets.baseline"), encoding="utf-8") as fh:
        doc = json.load(fh)
    for key in ("version", "plugins_used", "results"):
        assert key in doc, "baseline is missing %r - detect-secrets will not accept it" % key
