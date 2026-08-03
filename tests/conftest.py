"""Shared test setup.

The control plane is no longer copied into this repo. `governed-core` is a PINNED, HASH-VERIFIED
dependency (see requirements-core.txt), and importing it puts the packaged `controls/` and
`connector/` directories on sys.path. That preserves the flat-import contract the tool handlers rely
on (`import evidence`, `import identity`) — the same contract that holds at deploy time, when the
bundler stages those modules flat beside each handler.

Order matters. `governed_core` goes on the path FIRST, then this repo's own `lib/controls`, so an
agent-specific module (mask_pii, provenance, workflow_guards, sanitized, case_store) shadows the
packaged one if a name ever collides. It should not collide — `tests/test_core_dependency.py`
asserts that no agent module shadows a core module, because a silent shadow would reintroduce
exactly the drift the dependency exists to prevent.

Also set the provenance signing secret ONCE, before any tool module is imported, so mask_pii (the
signer) and the consumers (verifiers) share the same key for the P0-1 sanitized-artifact gate.
"""
import os
import pathlib
import sys

os.environ.setdefault("PROVENANCE_SECRET", "pv-unit-provenance-secret")

# Imported for the side effect: this is what makes `import evidence` resolve to the pinned package.
import governed_core  # noqa: E402  (must precede the flat imports below)

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (ROOT / "lib" / "controls", ROOT / "agents" / "pharmacovigilance" / "tools",
           ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CORE_CONTROLS = governed_core.controls_dir()
