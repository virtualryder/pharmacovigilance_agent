"""L45: the secret gate could never pass, and its error message blamed the developer.

The Security workflow ran `detect-secrets scan --baseline .secrets.baseline` and then
`git diff --exit-code .secrets.baseline`. detect-secrets rewrites `generated_at` with the current
UTC time on EVERY scan, so that diff was never empty and the BLOCKING step failed on every run -
printing "new potential secret(s) not in .secrets.baseline" even on an untouched tree.

Three separate defects stacked in one gate: the baseline was unreadable (L44), the action of
comparing it was wrong (this), and the message named the wrong cause. A control that cries wolf
every run is worse than an absent one, because it teaches people to skip the step.

tools/check_secrets_baseline.py compares the FINDINGS and ignores the timestamp. These tests pin
that, including the exact scenario that was failing.
"""
import importlib.util
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "check_secrets_baseline.py")

_spec = importlib.util.spec_from_file_location("check_secrets_baseline", TOOL)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_secrets_baseline"] = mod
_spec.loader.exec_module(mod)


def _baseline(results, generated_at="2026-01-01T00:00:00Z"):
    return {"version": "1.5.0", "plugins_used": [{"name": "Base64HighEntropyString"}],
            "filters_used": [], "results": results, "generated_at": generated_at}


def _write(tmp_path, name, doc):
    p = tmp_path / name
    io.open(p, "w", encoding="utf-8", newline="\n").write(json.dumps(doc, indent=2))
    return str(p)


FIND = {"a.py": [{"type": "Hex High Entropy String", "hashed_secret": "abc123",
                  "line_number": 10, "is_verified": False}]}


def test_a_changed_timestamp_alone_is_not_a_new_secret(tmp_path, capsys):
    """The defect: this exact case failed the build on every run."""
    a = _write(tmp_path, "committed.json", _baseline(FIND, "2026-09-08T02:41:31Z"))
    b = _write(tmp_path, "rescanned.json", _baseline(FIND, "2026-09-08T02:42:50Z"))
    assert mod.main(["x", a, b]) == 0
    assert "no new findings" in capsys.readouterr().out


def test_a_moved_line_is_not_a_new_secret(tmp_path):
    """Editing above a fixture shifts its line number; the secret did not change."""
    moved = {"a.py": [dict(FIND["a.py"][0], line_number=42)]}
    a = _write(tmp_path, "c.json", _baseline(FIND))
    b = _write(tmp_path, "r.json", _baseline(moved))
    assert mod.main(["x", a, b]) == 0


def test_a_genuinely_new_secret_fails_and_is_named(tmp_path, capsys):
    """A guard that cannot fail is not a guard."""
    extra = dict(FIND)
    extra["b.py"] = [{"type": "AWS Access Key", "hashed_secret": "deadbeef",
                      "line_number": 3, "is_verified": False}]
    a = _write(tmp_path, "c.json", _baseline(FIND))
    b = _write(tmp_path, "r.json", _baseline(extra))
    assert mod.main(["x", a, b]) == 1
    out = capsys.readouterr().out
    assert "b.py" in out and "AWS Access Key" in out


def test_a_changed_plugin_set_fails(tmp_path):
    """Silently dropping a detector would hide every secret it finds."""
    a = _write(tmp_path, "c.json", _baseline(FIND))
    weakened = _baseline(FIND)
    weakened["plugins_used"] = []
    b = _write(tmp_path, "r.json", weakened)
    assert mod.main(["x", a, b]) == 1


def test_windows_and_posix_paths_compare_equal(tmp_path):
    """The baseline is written on both; a path separator is not a new secret."""
    win = {"tests\\x.py": [FIND["a.py"][0]]}
    nix = {"tests/x.py": [FIND["a.py"][0]]}
    a = _write(tmp_path, "c.json", _baseline(win))
    b = _write(tmp_path, "r.json", _baseline(nix))
    assert mod.main(["x", a, b]) == 0


def test_a_tool_version_bump_alone_does_not_fail(tmp_path, capsys):
    """CI pins detect-secrets 1.5.0 and wrote a different version string than the machine that
    produced the baseline. That is a maintenance event, not a secret - failing on it would be the
    same cry-wolf defect in a new place. Coverage is what must not drift."""
    a = _write(tmp_path, "c.json", _baseline(FIND))
    bumped = _baseline(FIND)
    bumped["version"] = "1.5.50"
    b = _write(tmp_path, "r.json", bumped)
    assert mod.main(["x", a, b]) == 0
    assert "version changed" in capsys.readouterr().out


def test_a_dropped_filter_still_fails(tmp_path):
    """Coverage, unlike the version string, is blocking."""
    a = _write(tmp_path, "c.json", _baseline(FIND))
    weakened = _baseline(FIND)
    weakened["filters_used"] = [{"path": "detect_secrets.filters.heuristic.is_potential_uuid"}]
    b = _write(tmp_path, "r.json", weakened)
    assert mod.main(["x", a, b]) == 1
