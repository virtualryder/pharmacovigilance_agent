"""Shared test setup. Put the shared control modules and the agent tools on sys.path so a tool's
plain-name imports (e.g. `import sanitized`, `import provenance`) resolve the same way they do when
bundled beside the handler at deploy time. Also set the provenance signing secret ONCE, before any
tool module is imported, so mask_pii (signer) and the consumers (verifiers) share the same key for the
P0-1 sanitized-artifact gate."""
import os
import pathlib
import sys

os.environ.setdefault("PROVENANCE_SECRET", "pv-unit-provenance-secret")

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (ROOT / "lib" / "controls", ROOT / "agents" / "pharmacovigilance" / "tools", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
