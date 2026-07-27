# MCP Secure Gateway & Portability (Pharmacovigilance)

*How the tools are exposed to the agent through a secured MCP gateway, how auth works, and what stays
portable off Amazon Bedrock AgentCore.*

---

## Why a gateway

The agent never calls a tool Lambda directly. Every governed tool (intake, openFDA lookup, mask,
seriousness, duplicate, causality-prepare, draft, sign-off) is an **MCP target** behind a gateway that
authenticates the caller, authorizes the specific action with Cedar (deny-by-default, forbid-wins), and
invokes only the exact tool Lambda by ARN. This is where "which identity may call which tool on which
resource" is decided.

## How it is built (IaC)

`cdk/pv_stacks/gateway_stack.py` provisions the AgentCore/Gateway/Cedar attachment via a custom-resource
provider: policy engine → MCP gateway (**CUSTOM_JWT** bound to the identity pool) → SSM discovery param →
one target per governed tool Lambda (**exact ARN, never by name**) → every Cedar policy (gateway ARN
injected into the forbids) → flip to **ENFORCE**; reversed on stack delete. Targets are generated **at
synth from the manifest**, so the gateway's advertised tool schemas can never drift from the tools the
agent ships. The gateway role can invoke **only** the governed tool Lambdas; the provider IAM is scoped
to the AgentCore control plane + the one SSM param + PassRole of the one gateway role.

## Auth model

- **Caller → gateway:** CUSTOM_JWT from the pilot identity pool (MFA-enforced Cognito; enterprise IdP
  federation is the Gate-D round-trip).
- **Gateway → tool:** the gateway assumes its scoped role and invokes the exact tool Lambda ARN.
- **Authorization:** Cedar deny-by-default — `pv_reviewer_permit`, `mask_before_{assess,causality,draft}`,
  `no_self_submit`, `no_self_causality_commit`. A new tool with no explicit permit is denied.
- **Token hygiene (P0-3):** no bearer token in any tool schema; credential-shaped args scrubbed; the
  runtime injects the sign-off token out-of-band; logs record `token_present` only.

## Portability — AgentCore vs. portable

The **governance is not AgentCore-specific.** The load-bearing controls — deterministic masking + signed
`sanitized_ref`, HMAC provenance, the deterministic Step Functions controller with fail-closed guards,
the human sign-off gate, the WORM audit ledger, pass-by-reference — run as plain Lambdas + shared Python
and are exercised fully offline by the suite (no AgentCore needed). AgentCore/Gateway/Cedar provides the
**managed MCP gateway + policy enforcement plane**. If an institution cannot use AgentCore, the same
tools can sit behind an alternative MCP gateway (or API Gateway + Lambda authorizer) enforcing the same
Cedar policies; the tool contracts and guarantees are unchanged — you would re-implement the gateway
wiring in `gateway_stack.py`, not the agent.

## What an evaluator should check

Gateway is ENFORCE; targets resolve to exact ARNs; the gateway role can invoke only the tool Lambdas;
every Cedar forbid carries the gateway ARN; a hypothetical new tool is denied by default. Asserted in
`tests/test_cdk_stacks.py`.
