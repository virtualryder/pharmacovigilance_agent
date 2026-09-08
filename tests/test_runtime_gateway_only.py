"""RT-4 / #233: the runtime must be reachable only through the gateway.

Until 2026-09-08 the AgentCore Runtime's authorizer was:

    {"customJWTAuthorizer": {"discoveryUrl": ..., "allowedClients": ["<pool client id>"]}}

which accepts ANY caller holding a valid JWT for that client - so a token that could reach the
gateway could also reach the runtime directly, past the gateway's Cedar interceptor. The fourth
external review recorded this as R4-2; the answer at the time was that the runtime's own model calls
are IAM- and guardrail-governed, which is true and is not the same as the runtime being unreachable.

AWS ships the field that closes it. `allowedWorkloadConfiguration` on the customJWTAuthorizer
"restricts which workloads in the request's identity chain are allowed to invoke the target ... At
launch, this is supported only for AgentCore Runtime targets, and the allowed workloads are
AgentCore Gateways."
  https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore-control/update-agent-runtime.html
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html#deploy-agent-allowed-workload

These tests pin the two things that make it a control rather than a line of shell: the JSON is the
shape AWS documents, and a missing gateway ARN REFUSES instead of silently configuring the old
permissive posture.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIGURE = ROOT / "lib" / "runtime" / "_configure.sh"

pytestmark = pytest.mark.skipif(not CONFIGURE.exists(), reason="this pack has no runtime configure script")

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
        # Refuse the WSL stub rather than reporting a failure it caused.
        return None if (found and "WindowsApps" in found) else found
    return shutil.which("bash")


_BASH = _find_bash()

_HARNESS = r"""
set -euo pipefail
DISCOVERY="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_ABC/.well-known/openid-configuration"
CLIENT_ID="abc123client"
STATE="/tmp/fake-spine-state.env"
%(gw)s
%(snippet)s
printf '%%s\n' "$ACJSON"
"""


def _snippet():
    """The RT-4 block from _configure.sh, lifted verbatim so the test cannot drift from the script."""
    src = CONFIGURE.read_text(encoding="utf-8")
    start = src.index("if [ -n \"${GW_ARN:-}\" ]; then")
    end = src.index("\n", src.index("ACJSON=", start))
    return src[start:end]


def _run(gw_line, tmp_path=None):
    """Run the lifted snippet through a real bash.

    The script goes to a FILE, not `bash -c "<string>"`: on Windows the argv round-trip through
    bash.EXE mangles the escaped quotes in the JSON, so `bash -c` reported a REFUSED that the same
    script run from a file does not produce. Testing the quoting is half the point here, so the
    harness must not be the thing that changes it.
    """
    if not _BASH:
        pytest.skip("bash is not available on this machine")
    import tempfile
    script = _HARNESS % {"gw": gw_line, "snippet": _snippet()}
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


def test_the_authorizer_json_is_the_shape_aws_documents():
    r = _run('GW_ARN="arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/ben-gw-xyz"')
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout.strip().splitlines()[-1])
    auth = doc["customJWTAuthorizer"]
    assert set(auth) == {"discoveryUrl", "allowedClients", "allowedWorkloadConfiguration"}, auth
    envs = auth["allowedWorkloadConfiguration"]["hostingEnvironments"]
    assert isinstance(envs, list) and envs, envs
    arn = envs[0]["arn"]
    assert arn.startswith("arn:aws:bedrock-agentcore:") and ":gateway/" in arn, arn


def test_a_missing_gateway_arn_REFUSES_rather_than_configuring_the_permissive_posture():
    """Fail-closed. Silently omitting the restriction is the posture this control exists to remove."""
    r = _run("unset GW_ARN || true")
    assert r.returncode != 0, (
        "configuring with no gateway ARN succeeded - the runtime would accept any holder of a pool "
        "token, bypassing the gateway's Cedar interceptor entirely:\n" + r.stdout)
    assert "REFUSED" in (r.stdout + r.stderr)


def test_the_permissive_posture_requires_an_explicit_opt_in():
    r = _run('unset GW_ARN || true\nRT4_ALLOW_UNRESTRICTED=1')
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout.strip().splitlines()[-1])
    assert "allowedWorkloadConfiguration" not in doc["customJWTAuthorizer"]
    assert "DISABLED" in r.stdout, "the opt-out must announce itself in the deploy log"
