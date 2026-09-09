"""RT-4 / #233: the gateway-only runtime restriction is OPT-IN, and off by default.

The original control set `allowedWorkloadConfiguration` on the runtime's customJWTAuthorizer
whenever a gateway ARN was present, to close R4-2: "a token that could reach the gateway could ALSO
reach the runtime directly, past the gateway's Cedar interceptor."

It works, and it makes the agent unreachable. AWS restricts the runtime to workloads in the request's
identity chain and the only allowed workload type is an AgentCore GATEWAY. Here the gateway is
DOWNSTREAM of the runtime - its targets are the tool Lambdas built from the manifest, and the runtime
is not one of them - so nothing ever invokes the runtime through it. Live on 2026-09-09, every proof
that drives the agent got:

    {"code": -32001, "message": "Transaction token required: authorizer has
     AllowedWorkloadConfiguration configured"}

and four gate checks failed for that single reason. The repository had already reached the right
answer and the control contradicted it - MATURITY.yaml, on the fourth external review: "R4-2 ...
direct runtime invocation by a JWT holder is the designed entry."

R4-2 is mitigated at the GATEWAY, by Cedar: mask_before_* forbid the consequential tool actions
unless context.input.deidentified == true, with consent, budget, entitlement and service-window
gates beside them. Those bind every tool call whoever makes it.

These tests pin the inverted contract. The one that matters most is
test_a_gateway_arn_alone_does_not_enable_the_restriction: the old code turned the restriction on
merely because an ARN was in scope, which is exactly how it reached a live deployment without anyone
choosing it.
"""
import json
import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIGURE = ROOT / "lib" / "runtime" / "_configure.sh"

pytestmark = pytest.mark.skipif(not CONFIGURE.exists(), reason="this pack has no runtime configure script")

GW = "arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/ben-gw-xyz"


def _find_bash():
    """A bash that can run a script at the path we hand it.

    On Windows the `bash` on PATH is usually the WSL launcher in WindowsApps, which cannot resolve a
    Windows path - it reported `/bin/bash: C:UsersdarydAppData...: No such file or directory` and,
    via `bash -c`, silently mangled the escaped quotes in the JSON instead. Git-Bash handles both,
    so prefer it; fall back to PATH elsewhere (the CI runner is ubuntu and just works).
    """
    if os.name == "nt":
        for cand in (r"C:\Program Files\Git\bin\bash.exe",
                     r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.isfile(cand):
                return cand
        found = shutil.which("bash")
        return None if (found and "WindowsApps" in found) else found
    return shutil.which("bash")


_BASH = _find_bash()

_HARNESS = """
set -euo pipefail
DISCOVERY="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_ABC/.well-known/openid-configuration"
CLIENT_ID="abc123client"
STATE="/tmp/fake-spine-state.env"
%(env)s
%(snippet)s
printf '%%s\\n' "$ACJSON"
"""


def _snippet():
    """The RT-4 block lifted verbatim between its markers, so the test cannot drift from the script."""
    src = CONFIGURE.read_text(encoding="utf-8")
    start = src.index("# --- RT4-BLOCK-START ---")
    end = src.index("\n", src.index("ACJSON=", src.index("# --- RT4-BLOCK-END ---")))
    return src[start:end]


def _run(env_lines):
    """Run the lifted snippet through a real bash.

    The script goes to a FILE, not `bash -c "<string>"`: on Windows the argv round-trip through
    bash.EXE mangles the escaped quotes in the JSON. Testing the quoting is half the point, so the
    harness must not be the thing that changes it.
    """
    if not _BASH:
        pytest.skip("bash is not available on this machine")
    import tempfile
    script = _HARNESS % {"env": env_lines, "snippet": _snippet()}
    fd, path = tempfile.mkstemp(suffix=".sh", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(script)
        return subprocess.run([_BASH, path], capture_output=True, text=True)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _auth(r):
    return json.loads(r.stdout.strip().splitlines()[-1])["customJWTAuthorizer"]


def test_the_default_leaves_the_runtime_directly_invocable():
    """Direct invocation by a pool JWT holder is the designed entry (MATURITY.yaml, R4-2)."""
    r = _run('unset GW_ARN || true')
    assert r.returncode == 0, r.stderr
    auth = _auth(r)
    assert "allowedWorkloadConfiguration" not in auth, auth
    assert set(auth) == {"discoveryUrl", "allowedClients"}, auth
    assert "rt4_gateway_only=OFF" in r.stdout, "the posture must announce itself in the deploy log"


def test_a_gateway_arn_alone_does_not_enable_the_restriction():
    """THE regression guard. The old code enabled it merely because an ARN was in scope.

    Every deploy has a gateway ARN in the spine state, so `if [ -n "$GW_ARN" ]` meant "always on" -
    which is how a control that breaks the agent reached a live deployment without anyone choosing
    it. Turning it on must now be a decision, not a side effect of the spine being deployed.
    """
    r = _run('GW_ARN="%s"' % GW)
    assert r.returncode == 0, r.stderr
    assert "allowedWorkloadConfiguration" not in _auth(r), (
        "a gateway ARN alone re-enabled the restriction - the agent would be unreachable again")
    assert "rt4_gateway_only=OFF" in r.stdout


def test_the_opt_in_produces_the_shape_aws_documents():
    r = _run('GW_ARN="%s"\nRT4_GATEWAY_ONLY=1' % GW)
    assert r.returncode == 0, r.stderr
    auth = _auth(r)
    assert set(auth) == {"discoveryUrl", "allowedClients", "allowedWorkloadConfiguration"}, auth
    envs = auth["allowedWorkloadConfiguration"]["hostingEnvironments"]
    assert isinstance(envs, list) and envs, envs
    arn = envs[0]["arn"]
    assert arn.startswith("arn:aws:bedrock-agentcore:") and ":gateway/" in arn, arn


def test_the_opt_in_refuses_without_a_gateway_arn():
    """Fail loud on the OPT-IN. Asking for the restriction and silently not getting it is worse
    than not asking - that is the half of the original control worth keeping."""
    r = _run('unset GW_ARN || true\nRT4_GATEWAY_ONLY=1')
    assert r.returncode != 0, "opting in with no gateway ARN succeeded:\n" + r.stdout
    assert "REFUSED" in (r.stdout + r.stderr)


def test_the_authorizer_is_always_valid_json_with_the_base_fields():
    for env in ('unset GW_ARN || true',
                'GW_ARN="%s"' % GW,
                'GW_ARN="%s"\nRT4_GATEWAY_ONLY=1' % GW):
        r = _run(env)
        assert r.returncode == 0, (env, r.stderr)
        auth = _auth(r)
        assert auth["discoveryUrl"].startswith("https://"), auth
        assert auth["allowedClients"] == ["abc123client"], auth