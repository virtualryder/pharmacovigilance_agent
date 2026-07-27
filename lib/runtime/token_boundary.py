"""token_boundary — the trusted runtime credential boundary (P0-3), ported from the financial-aid agent.

THE DEFECT THIS FIXES: `request_signoff` declared `access_token` as an MCP tool INPUT, so the model had
to construct a tool call containing the user's live bearer token — putting a credential into model
context, tool-call telemetry, and anything that logs tool arguments. A model should never handle or
reproduce a bearer token.

THE FIX: the runtime (which already holds the token as the gateway `Authorization` header — the trusted
boundary) injects the credential OUT-OF-BAND at dispatch time:

  * the tool schema no longer declares any token field — the model cannot be asked to supply one;
  * `scrub_args()` strips ANY credential-shaped key the model tries to pass on ANY tool (a model-supplied
    credential is never forwarded — it could be stolen, stale, or fabricated);
  * `inject()` adds the runtime-held access token to the sign-off call only, after the scrub, so the
    verifying Lambda (identity.verify_access_token, unchanged) still receives and re-verifies a genuine
    token — the principal is derived at the trusted boundary, never from model output.

Pure stdlib so it is unit-testable offline (tests/test_token_boundary.py). Wire `wrap_mcp_client` into
the runtime agent (pv-runtime) so the boundary runs on every outgoing MCP tool call."""

# Credential-shaped argument names the model may NEVER supply (case-insensitive).
CREDENTIAL_FIELDS = frozenset({
    "access_token", "accesstoken", "token", "bearer", "bearer_token", "authorization",
    "id_token", "refresh_token", "api_key", "apikey", "secret", "client_secret", "password",
})

# Gateway tool names arrive as "<target>___<tool>"; the sign-off tool is the only credential consumer.
SIGNOFF_TOOL = "request_signoff"


def is_signoff(tool_name):
    return bool(tool_name) and str(tool_name).split("___")[-1] == SIGNOFF_TOOL


def scrub_args(arguments):
    """Drop every credential-shaped key from model-produced tool arguments. Returns (clean, dropped)."""
    args = dict(arguments or {})
    dropped = [k for k in args if k.replace("-", "_").lower() in CREDENTIAL_FIELDS]
    for k in dropped:
        args.pop(k)
    return args, dropped


def prepare_args(tool_name, arguments, runtime_token):
    """The single dispatch-time transform: scrub model args, then inject the runtime-held token for the
    sign-off tool only. The model's view of the schema contains no token field."""
    args, _dropped = scrub_args(arguments)
    if is_signoff(tool_name) and runtime_token:
        args["access_token"] = runtime_token   # trusted-boundary injection (out-of-band from the model)
    return args


def wrap_mcp_client(mcp_client, runtime_token):
    """Intercept every outgoing MCP tool call on a Strands MCPClient so prepare_args() runs at the
    trusted boundary. Signature-agnostic: (tool_use_id, name, arguments, ...) positionally or by kw."""
    original = mcp_client.call_tool_sync

    def call_tool_sync(*a, **kw):
        a = list(a)
        name = kw.get("name", a[1] if len(a) > 1 else None)
        if "arguments" in kw:
            kw["arguments"] = prepare_args(name, kw["arguments"], runtime_token)
        elif len(a) > 2:
            a[2] = prepare_args(name, a[2], runtime_token)
        return original(*a, **kw)

    mcp_client.call_tool_sync = call_tool_sync
    return mcp_client
