# Governed Hero Agents on Amazon Bedrock AgentCore — Cost & Latency

**One fully-governed transaction ≈ $0.09**, of which **~94% is model inference**. The entire
governance apparatus — fail-closed PII/PHI masking, Cedar authorization on every tool call, immutable
WORM audit, the separation-of-duties human gate, live authoritative-data lookup, and full
OpenTelemetry/X-Ray observability — adds **under one cent** per transaction. Governance is not the cost;
the LLM is. Figures are us-east-1 list price, measured against the deployed benefits, pharmacovigilance,
financial-aid, and housing agents.

## Per-transaction cost model

| Component | What it does | Unit price | Per txn |
|---|---|---|---:|
| **Bedrock (Claude Sonnet-class)** | agent reasoning loops + guarded narrative draft (~20K in / 1.5K out tokens) | $3.00 / $15.00 per 1M in/out | **$0.0825** |
| AgentCore Runtime | serverless agent host, ~50s @ 1 vCPU / 2 GB | $0.0895 vCPU-hr + $0.00945 GB-hr | $0.0015 |
| Amazon Comprehend | fail-closed PII/PHI detection (mask_pii) | $0.0001 per 100-char unit | $0.0015 |
| Observability | X-Ray Transaction Search + CloudWatch (per-tool spans) | CloudWatch ingest/storage | $0.0015 |
| AgentCore Gateway | Cedar-authorized MCP tool calls (~12 API invocations) | $0.005 per 1,000 | $0.0001 |
| AWS Lambda | ~9 governed tool executions | $0.20 per 1M + $0.0000167 GB-s | $0.0002 |
| Step Functions | opens the separation-of-duties sign-off gate | $0.025 per 1,000 transitions | $0.0001 |
| AgentCore Identity | mints the outbound OAuth token for the connector (no stored secret) | $0.010 per 1,000 | <$0.0001 |
| DynamoDB + S3 Object Lock | append-only audit ledger + WORM copy | on-demand WRU + PUT | <$0.0001 |
| API Gateway (connector) | OAuth-protected system-of-record call | $1.00 per 1M | <$0.0001 |
| **Governance + infra overhead (everything except the LLM)** | | | **$0.0049** |
| **Total per governed transaction** | | | **≈ $0.087** |

At scale this is essentially volume-linear: **10,000 transactions/month ≈ $875** (Bedrock ~$825,
all governance + infrastructure ~$50), on a negligible fixed floor (tool indexing $0.02 / 100 tools /
month, log retention, Cognito). Sensitivity: on the premium extended-access Claude 3.5 Sonnet rate
($6 / $30 per 1M) the transaction is ~$0.17 and overhead falls to ~3%. The single lever that moves the
bill is the **model and token budget**, not the controls — a smaller draft model or tighter tool
schemas reduces cost directly; the governance line does not change.

## Latency — measured, from four captured X-Ray traces

End-to-end **37–55 seconds** per transaction (benefits 37.5s · pharmacovigilance 47.2s · housing 53.2s
· financial-aid 54.6s). The wall clock is dominated by the LLM, not the governance:

| Phase | Representative span | Share |
|---|---:|---:|
| **Model inference** — agent reasoning loops + guarded narrative draft | **~24 s** | ~55% |
| WORM audit write (DynamoDB + S3 Object Lock) | ~4.3 s | ~9% |
| Open the human sign-off gate (Step Functions) | ~3.8 s | ~8% |
| Fail-closed PII/PHI masking (Comprehend) | ~3.0 s | ~6% |
| Live authoritative-data lookup (openFDA / College Scorecard / HUD) | ~1.2–1.8 s | ~3% |
| Deterministic intake + rules-engine assessment | <1 s | ~2% |

The full control stack — masking, Cedar auth on every call, WORM audit, opening the separation-of-duties
gate — accounts for roughly **11 seconds**: the visible, modest "cost of governance," and the most
optimizable part (response streaming, a smaller draft model, cached tool schemas, parallelized
audit/lookup). Critically, **the human sign-off wait is asynchronous** — the agent opens the gate and
stops, so throughput is bounded by compute, not by human review time, and transactions run concurrently.

## The message for leadership

A fully-governed, audited, human-gated regulated transaction costs about a **dime** and completes in
under a minute. The governance that makes it safe to run in a regulated environment — the masking, the
deny-by-default authorization, the tamper-evident audit, the mandatory human in the loop — is
effectively **free** relative to the model call. The economics do not argue against governance; they
remove the reason to skip it.

<sub>us-east-1 list price, July 2026. Illustrative token/latency figures from the deployed reference agents; adopters should substitute their chosen model's current rate, token budget, and volume. Bedrock is the dominant and only materially variable line.</sub>
