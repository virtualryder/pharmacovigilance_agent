# Pharmacovigilance Agent — Depth Evidence (runtime trace + OAuth connector)

*Captured live against the deployed agent (us-east-1) with Cedar in **ENFORCE**, 2026-07. Account id is the live deploy; the public repo scrubs it. Together with the red-team harness (`bash lib/engine/redteam.sh agents/pharmacovigilance`, governance-holds-under-attack), this is the portfolio depth pack applied to this vertical.*

---

## Item 1 — End-to-end agent runtime trace (Observability / X-Ray)

The Strands agent runs on **AgentCore Runtime** (`pv_runtime_agent`). A pv_reviewer authenticates and their access token is the bearer for every governed Gateway (MCP) tool call, so Cedar evaluates the real human principal. With Transaction Search enabled, the per-invocation OTel spans are captured as one X-Ray trace. The captured trace (`1-6a5b0489-27f014d56f75c158607287c3`, 47.2s) shows the agent autonomously orchestrating each **governed tool through the MCP gateway** and stopping at the human gate:

```
invoke_agent (Strands Agents)                          47.2s
├─ execute_tool  intake_icsr                                468ms
├─ execute_tool  openfda_lookup (live openFDA/FAERS)        1846ms
├─ execute_tool  mask_pii                                   2989ms
├─ execute_tool  assess_seriousness                         476ms
├─ execute_tool  draft_narrative (guarded Bedrock)          10016ms
├─ execute_tool  write_audit (WORM)                         4341ms
└─ execute_tool  request_signoff (human gate)               3773ms
```

Every `execute_tool` span is a Cedar-authorized call through the governed gateway — including the **live authoritative-data lookup** and the **fail-closed PHI/PII masking** before the model ever drafts. The consequential `finalize_submission` never appears: the agent completes everything it is allowed to do and then **waits on a human** (the sign-off gate is left in PENDING_APPROVAL). An outsider invocation returns `ACCESS DENIED, tools_available: []`.

## Item 2 — Real OAuth connector via AgentCore Identity outbound auth

`verify_source` calls a genuinely **OAuth2-protected** external system of record (`MOCK-Argus-SafetyDB`, an API-Gateway HTTP API that requires a Cognito M2M access token). The outbound token is minted by an **AgentCore Identity** OAuth2 credential provider (client_credentials / M2M) — the tool holds **no secret** (it lives in the Identity token vault). Proven live (`bash lib/connector/prove_connector.sh agents/pharmacovigilance`):

```
1. the system of record REALLY requires OAuth  -> no-token 401, bad-token 401  (genuinely OAuth-protected)
2. governed tool: pv_reviewer calls verify_source; AgentCore Identity mints the outbound token -> verified
   the tool holds NO client secret (it lives in the Identity token vault)
3. deny-by-default extends to the new connector -> outsider DENIED
=== CONNECTOR PROOF: 4 passed, 0 failed ===  CONNECTOR PROOF: PASS
```

Built from the reusable, prefix-parameterized `lib/connector` kit — the same connector applied across the portfolio; swapping the mock system of record for a real one (EIV / SIS-COD / a safety database) is a configuration change, with the governance and secret-handling posture already in place.

## Why this matters

The happy-path demo proves the controls work when everything cooperates. The runtime trace proves it **runs as an autonomous agent** (not a scripted sequence) and produces court-defensible evidence, the red-team proves it **holds when the agent is adversarial**, and the connector proves it **authenticates to a real dependency without holding a secret**. This vertical carries the same depth as the flagship.
