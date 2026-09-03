"""The control plane is a pinned dependency, not a copy. These tests hold that property.

BACKGROUND
----------
Until 2026-08-03 this repo carried its own copy of the governance controls in `lib/controls/`. The
copy was missing the exactly-once `FINAL#` finalization control that the financial-aid and housing
agents both had, while all four repos' integrity locks recorded the same tree hash and every CI run
was green. For an ICSR workflow that gap is a double-reporting risk: a retried or replayed finalize
writes a second COMMITTED record, i.e. the same case submitted to a regulator twice.

A cross-repo parity check DETECTS that after the fact. A pinned dependency PREVENTS it. These tests
are what stop the repo from quietly sliding back to a copy.

WHAT EACH TEST DEFENDS
----------------------
1. The dependency is installed and importable at all.
2. The requirement is pinned to an exact version AND an exact artifact hash — a floating pin is a
   copy with extra steps.
3. The version installed is the version pinned, so the lockfile is not decorative.
4. The core control modules resolve to the PACKAGE, not to anything in this repo.
5. No agent module shadows a core module — a shadow is how a "fix" gets made locally and never
   reaches the other verticals, which is the original failure mode.
6. The exactly-once control is actually present in what we resolved. This is the specific control
   whose absence started all of this; assert it by behaviour, not by trusting the version string.
"""
import hashlib
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
REQ = ROOT / "requirements-core.txt"

# Modules that MUST come from the pinned package: the hash chain, chain verification, the audit
# writer, identity, the separation-of-duties approval path, and the exactly-once commit gate.
CORE_MODULES = [
    "evidence", "verify_chain", "write_audit", "identity",
    "approve_signoff", "request_signoff", "idp_group_mapper", "mcp_client",
    "finalize_signoff",
    # governed-core 1.6.0: hybrid multi-tenant routing (ledger / WORM / approvals per tenant)
    "tenancy", "tenant_interceptor",
    # governed-core 1.7.0 / 1.8.0: correlation + the kill switch (containment)
    "telemetry", "kill_switch", "kill_switch_control",
    # governed-core 1.9.0: the per-tenant budget meter
    "budget",
]

# `mcp_client` is a CLI entry point, not a library: it reads sys.argv at module scope, so importing
# it raises IndexError. Check it by location rather than by import.
NOT_IMPORTABLE = {"mcp_client"}

# Modules this agent DELIBERATELY overrides because they encode domain rules, mapped to why. An
# override is legitimate; an UNDECLARED override is not, because that is indistinguishable from the
# drift this whole dependency exists to prevent. Anything shadowing a core module must appear here
# with a reason, or the test fails.
DOMAIN_OVERRIDES = {
    "mask_pii": "PV masks ICSR/CIOMS fields (patient initials, DOB, reporter identity) and mints the "
                "signed sanitized_ref for this domain's entity set.",
    "provenance": "PV binds provenance to openFDA as the non-authoritative background source, with "
                  "single-domain signing (no GA-2 deid/HUD key split).",
}


def _req_text():
    assert REQ.exists(), "requirements-core.txt is missing — the core dependency is undeclared"
    return REQ.read_text(encoding="utf-8")


def test_governed_core_is_installed():
    governed_core = pytest.importorskip(
        "governed_core",
        reason="governed-core is not installed; run: "
               "pip install --require-hashes -r requirements-core.txt")
    assert governed_core.__version__, "the installed core does not report a version"


def test_requirement_is_pinned_to_an_exact_version_and_hash():
    text = _req_text()
    ver = re.search(r"governed_core-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl", text)
    assert ver, "requirements-core.txt does not pin an exact governed_core wheel version"
    digest = re.search(r"--hash=sha256:([0-9a-f]{64})", text)
    assert digest, (
        "requirements-core.txt has no --hash= pin. Without it pip will accept whatever is at the "
        "URL, which makes this a copy with extra steps rather than a dependency.")


def test_installed_version_matches_the_pin():
    governed_core = pytest.importorskip("governed_core")
    pinned = re.search(r"governed_core-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl",
                       _req_text()).group(1)
    assert governed_core.__version__ == pinned, (
        "installed governed-core is %s but requirements-core.txt pins %s — reinstall with "
        "pip install --require-hashes -r requirements-core.txt"
        % (governed_core.__version__, pinned))


def test_the_pinned_core_passes_its_own_integrity_check():
    """The wheel ships core.lock; the package can verify itself. If this fails, the artifact was
    tampered with after publication or built from a dirty tree."""
    governed_core = pytest.importorskip("governed_core")
    pkg = pathlib.Path(governed_core.__file__).parent
    res = subprocess.run([sys.executable, str(pkg / "verify_core.py")],
                         capture_output=True, text=True)
    assert res.returncode == 0, (
        "the installed core failed its own integrity check:\n%s%s" % (res.stdout, res.stderr))


@pytest.mark.parametrize("name", CORE_MODULES)
def test_core_modules_resolve_to_the_package_not_this_repo(name):
    governed_core = pytest.importorskip("governed_core")
    core_dir = pathlib.Path(governed_core.controls_dir()).resolve()

    # Whatever the mechanism, the module must NOT be served out of this repo.
    assert not (ROOT / "lib" / "controls" / (name + ".py")).exists(), (
        "lib/controls/%s.py exists — a core control is being served from this repo again" % name)

    if name in NOT_IMPORTABLE:
        assert (core_dir / (name + ".py")).is_file(), (
            "%s.py is not in the pinned package at %s" % (name, core_dir))
        return

    mod = __import__(name)
    resolved = pathlib.Path(mod.__file__).resolve()
    assert resolved.parent == core_dir, (
        "%s resolved to %s, not to the pinned package at %s. A core control being served from "
        "this repo means the copy came back." % (name, resolved, core_dir))


def test_every_shadowing_module_is_a_declared_domain_override():
    """A same-named file in lib/controls/ wins the sys.path race and silently replaces the core
    module. Sometimes that is correct — masking and provenance genuinely encode domain rules. But an
    UNDECLARED shadow is indistinguishable from the drift this dependency exists to prevent, so
    every shadow must be listed in DOMAIN_OVERRIDES with a reason."""
    governed_core = pytest.importorskip("governed_core")
    core_names = {p.stem for p in pathlib.Path(governed_core.controls_dir()).glob("*.py")
                  if p.stem != "__init__"}
    local_names = {p.stem for p in (ROOT / "lib" / "controls").glob("*.py")
                   if p.stem != "__init__"}

    undeclared = sorted((core_names & local_names) - set(DOMAIN_OVERRIDES))
    assert not undeclared, (
        "these lib/controls modules shadow a core module without being declared: %s. Either delete "
        "the local copy so the pinned core is used, or add it to DOMAIN_OVERRIDES with the reason "
        "the domain genuinely differs." % ", ".join(undeclared))

    # A core module may never be silently promoted to "domain-specific" — the ones that must be
    # identical everywhere are not overridable at all.
    illegal = sorted(set(DOMAIN_OVERRIDES) & set(CORE_MODULES))
    assert not illegal, (
        "%s is in CORE_MODULES and must come from the package; it cannot be a domain override"
        % ", ".join(illegal))

    stale = sorted(set(DOMAIN_OVERRIDES) - local_names)
    assert not stale, (
        "DOMAIN_OVERRIDES lists %s but there is no such file in lib/controls/ — remove the stale "
        "entry so the list keeps meaning something" % ", ".join(stale))


def test_exactly_once_finalization_is_present_in_the_resolved_core():
    """The control whose absence started this. Assert it on the module we actually resolved."""
    pytest.importorskip("governed_core")
    import finalize_signoff
    assert hasattr(finalize_signoff, "_exactly_once_marker"), (
        "the resolved finalize_signoff has no _exactly_once_marker — this is the ICSR "
        "double-reporting gap re-opening")
    src = pathlib.Path(finalize_signoff.__file__).read_text(encoding="utf-8")
    assert "FINAL#" in src, "the conditional-put marker key FINAL# is absent"
    assert "attribute_not_exists" in src, (
        "the conditional put that makes finalization exactly-once is absent; without it a retry "
        "writes a second COMMITTED record")


def test_lambda_bundle_stages_core_from_the_package():
    """The deployed artifact must be built from the pinned core, not from the repo."""
    sys.path.insert(0, str(ROOT / "cdk"))
    import app as cdk_app

    out = pathlib.Path(cdk_app.stage_lambda_bundle())
    governed_core = pytest.importorskip("governed_core")

    stamped = (out / "CORE_VERSION").read_text(encoding="utf-8").strip()
    assert stamped == governed_core.__version__, (
        "the staged bundle is stamped %s but the installed core is %s"
        % (stamped, governed_core.__version__))

    def sha(p):
        return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()

    core_dir = pathlib.Path(governed_core.controls_dir())
    for name in CORE_MODULES:
        staged, canonical = out / (name + ".py"), core_dir / (name + ".py")
        assert staged.exists(), "%s.py was not staged into the Lambda bundle" % name
        assert sha(staged) == sha(canonical), (
            "%s.py in the staged bundle does not match the pinned core — something overwrote a "
            "core control during bundling" % name)
