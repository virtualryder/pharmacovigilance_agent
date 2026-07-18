# P0 Remediation Plan — Governed Agent Platform

Program tracker for the P0 gaps from the independent (JGPT) security/architecture review. This is the
**authoritative checklist**: no P0 is marked ✅ Done until its **validation gate** passes, and where a
gate says "live," that means deployed to AWS and exercised in Cedar ENFORCE — not asserted in a README.

**Maturity labels** (used throughout, per review P0-7): `Designed` · `Implemented` (code merged) ·
`Deployed` (stood up in AWS) · `Tested` (validation gate passed) · `Prod-enforced` (hardened for
regulated use). Nothing here is `Prod-enforced` yet — that is the whole point of the program.

Ground truth: every P0 below was **verified against the actual code**, not taken from the review at face
value. All ten findings reproduce. Status as of kickoff: all P0s `Not started` except the tracker + the
capability matrix (this document).

---

## Execution waves

**Wave A — Evidence & correctness integrity** (highest credibility risk; a security reviewer attacks here first)
P0-1 Canonical Evidence Service · P0-2 Drift removal · P0-3 NEEDS_REVIEW on source failure · P0-7 Maturity matrix + language

**Wave B — Security posture**
P0-4 IAM least-privilege · P0-5 Enterprise identity + approver-from-token · P0-8 Supply-chain controls

**Wave C — Architecture & production shape**
P0-6 Network/VPC/CMK/multi-account + diagram · P0-9 Governance-core consolidation · P0-10 Lighthouse scoping + repositioning

Rationale: fix what a reviewer can *disprove* first (evidence claims, drift, fabricated "authoritative"
data), then what a reviewer can *exploit* (IAM, identity, supply chain), then what a reviewer will *ask
for* (production architecture, one platform, honest scope).

---

## The checklist

### P0-1 — Canonical Evidence Service  ·  status: ☐ Not started  ·  Wave A
**Finding (verified):** `finalize_signoff.py` writes the COMMITTED event with a raw `put_item` — no
hash chain, no S3 WORM copy, and `except ClientError: pass` silently swallows failures. The chain in
`write_audit.py` trusts a caller-supplied `prev_hash` (no authoritative, atomic head → forkable).
**Fix:** one writer for EVERY consequential event (intake, request, approve, reject, override, finalize,
connector response, policy denial, failed action). Server-side authoritative chain via DynamoDB
`TransactWriteItems`: immutable event `PutItem` (`attribute_not_exists`) + a head compare-and-swap
(`PutItem` on `HEAD#<case>` conditioned on the expected tip). Fork-proof, atomic, still append-only
(no `UpdateItem`, so the tamper-Deny stands). Fail-loud. Enrich records: tenant, case, policy/rule/
model/deployment versions, actor.
**Validation gate:** (a) unit tests: chain links, fork rejected, stale-head rejected, concurrent writers
→ single chain; (b) **live**: deploy + full-lifecycle demo → the COMMITTED finalize event is hash-chained
AND present in S3 WORM AND `verify_chain` reports INTACT across intake→…→finalize; (c) inject two
concurrent writes for one case → no fork.

### P0-2 — Remove PV/template drift + unsafe defaults  ·  status: ☐ Not started  ·  Wave A
**Finding (verified):** shared controls hardcode `pv-audit`, `pv-signoff`, `pv-pending-approvals`,
`source:"pv-*"`, and use `icsr_id` as the case key in every agent. Table *names* are env-overridden per
agent at deploy, but record `source`/key naming leaks PV terms into Housing/EDU/Benefits evidence.
**Fix:** generic `case_id` (accept `icsr_id` as alias for back-compat), per-agent `SOURCE`/labels from
env, no `pv-*` literals on any write path.
**Validation gate:** grep across all 4 repos → no cross-agent naming on any record-writing path; live
demo per agent → records carry that agent's own naming.

### P0-3 — Authoritative-source failure → NEEDS_REVIEW  ·  status: ☐ Not started  ·  Wave A
**Finding (verified):** the lookup tools already fail soft (`found:false`), and `assess` already returns
NEEDS_REVIEW when limits are absent — good. The gap: `assess` treats a **caller-supplied** `il_source`
string as authoritative, and the demo substitutes hardcoded fallback limits while still labeling the
result HUD-authoritative.
**Fix:** `assess` must require a genuine provenance flag emitted by the lookup tool (not a hand-passed
string); on source failure the determination is NEEDS_REVIEW with `authoritative:false`; the demo must
never fabricate authoritative-labeled fallback data.
**Validation gate:** unit tests (source-down → NEEDS_REVIEW, `authoritative:false`, no fabricated
provenance); live demo shows the honest NEEDS_REVIEW path.

### P0-4 — IAM least-privilege  ·  status: ☐ Not started  ·  Wave B
**Finding (verified):** gateway role = `bedrock-agentcore:*` on `Resource:*`. Bedrock/Comprehend granted
on `*`. One shared tool-exec role for all tools (already scoped for DDB/S3/States + an explicit
tamper-Deny — better than the review implies, but still not per-component).
**Fix:** scope the gateway role to the exact actions/ARNs it needs; scope `bedrock:InvokeModel` to the
specific model + guardrail ARNs; split into per-component roles (masking / drafting / audit-write /
signoff-init / connector); add permission boundaries + `aws:SourceAccount`/`aws:SourceArn`.
**Validation gate:** no `*` on any sensitive action (grep + IAM Access Analyzer); **live** deploy +
demo still passes in ENFORCE with the scoped roles.

### P0-5 — Enterprise identity + approver-from-token  ·  status: ☐ Not started  ·  Wave B
**Finding (verified):** `approve_signoff` reads `approver = event.get("approver")` — trusts a string in
the event body. Demo identity is Cognito username/password from a generated file.
**Fix:** the approver identity is derived from a cryptographically validated JWT (the approver's own
token), never the event body; separation of requester vs approver enforced on verified identity; document
the enterprise federation baseline on top of `deploy_federation.sh` (MFA, short tokens, step-up for final
approval, lifecycle/revocation).
**Validation gate:** a spoofed `approver` string is rejected; token-derived identity enforced end to end;
**live** demo shows a forged approver denied and a valid approver (different token) accepted.

### P0-6 — Production network architecture  ·  status: ☐ Not started  ·  Wave C
**Finding (verified):** sandbox uses public endpoints; Security Hub treats public AgentCore Runtime as
high severity. No CMKs; single account.
**Fix (design + partial impl):** AgentCore Runtime VPC mode, private subnets, VPC endpoints/PrivateLink,
restricted egress, customer-managed KMS on AgentCore/DynamoDB/S3, separate workload/security-log/evidence
accounts. Deliverable: production architecture diagram + config knobs where implementable now.
**Validation gate:** diagram delivered; where implemented, deploy + Security Hub shows no public-runtime
high finding; CMK encryption verified on the evidence stores.

### P0-7 — Capability maturity matrix + language cleanup  ·  status: ◑ In progress (matrix below)  ·  Wave A
**Fix:** the matrix in this document labels every capability Designed/Implemented/Deployed/Tested/
Prod-enforced against ground truth; a follow-up pass removes/qualifies "court-defensible", "authoritative
eligibility", "production", and overclaimed "immutable" across READMEs/docs.
**Validation gate:** matrix accurate to code (done in this doc); grep shows overclaims removed or
explicitly qualified across all repos (language pass pending).

### P0-8 — Supply-chain controls  ·  status: ☐ Not started  ·  Wave B
**Finding (verified):** `requirements.txt` fully unpinned (`bedrock-agentcore`, `strands-agents`, `mcp`,
`boto3`, otel). Dockerfile uses unpinned `python:3.12-slim`, runs as root. CI = render + pytest + a
bug-class Ruff scan only.
**Fix:** pin requirements to exact versions; Dockerfile pinned-by-digest base + non-root `USER`; CI adds
pip-audit (deps), bandit (SAST), gitleaks (secrets), checkov (IaC), CycloneDX (SBOM), trivy (container),
coverage threshold.
**Validation gate:** CI green with all scanners wired; container builds + runs non-root; requirements
fully pinned.

### P0-9 — One versioned governance core  ·  status: ☐ Not started  ·  Wave C
**Finding (verified):** four repos carry byte-identical shared `lib/` — "four copies of a platform." Drift
already visible (PV names in Housing).
**Fix:** one versioned governance core (IaC modules, runtime base image, identity, policy engine,
canonical audit, approval, observability, eval harness, CI, governance SDK); vertical agents pin a
released core version. Interim: the shared `lib/` is single-sourced and propagated by version, not copy.
**Validation gate:** core versioned + tagged; each vertical pins a version; a core fix reaches verticals
by version bump, not copy-paste; drift grep clean.

### P0-10 — Lighthouse scoping + repositioning  ·  status: ☐ Not started  ·  Wave C
**Fix:** PV = the lighthouse; first pilot scoped to intake / completeness / preliminary assessment /
draft-for-qualified-review. Reposition titles/claims: Housing → "Intake & Income Screening", Benefits →
"Configurable Pre-Screening Pattern", EDU → "document completeness / policy-guided pre-review"; remove
"adjudication" / "autonomous eligibility" everywhere.
**Validation gate:** titles/READMEs/decks updated; a leadership-safe positioning one-pager; no
"autonomous determination" language remains (grep).

---

## Capability maturity matrix (P0-7 deliverable — accurate to code as of kickoff)

| Capability | Designed | Implemented | Deployed | Tested | Prod-enforced | Note |
|---|:--:|:--:|:--:|:--:|:--:|---|
| Cedar deny-by-default authorization | ✅ | ✅ | ✅ | ✅ (demo ENFORCE) | ❌ | Live-proven; not yet under enterprise identity |
| Deterministic rules (seriousness/income/aid) | ✅ | ✅ | ✅ | ✅ (unit + demo) | ❌ | Thresholds are illustrative defaults, not certified rules |
| Fail-closed PII/PHI masking | ✅ | ✅ | ✅ | ✅ (demo) | ❌ | Comprehend-based; no CMK/VPC yet |
| Bedrock output guardrail | ✅ | ✅ | ✅ | ✅ (demo) | ❌ | |
| Human approval + separation of duties | ✅ | ✅ | ✅ | ✅ (demo) | ❌ | **Approver identity from event body — P0-5** |
| Append-only + WORM evidence | ✅ | ✅ | ✅ | ✅ (demo) | ❌ | 1-day GOVERNANCE retention = demo-only |
| Tamper-evident hash-chained audit | ✅ | ✅ (caller-threaded) | ✅ | ◑ (unit only) | ❌ | **Not authoritative/atomic; finalize bypasses it — P0-1** |
| Canonical evidence for *every* consequential event | ✅ | ❌ | ❌ | ❌ | ❌ | **finalize/approve bypass the writer — P0-1/2** |
| Authoritative federal-data lookups | ✅ | ✅ | ✅ | ✅ (demo) | ❌ | Fail-soft; provenance integrity gap — P0-3 |
| OAuth connector (RS256/JWKS, no stored secret) | ✅ | ✅ | ✅ | ✅ (4/4 proof) | ❌ | Mock SoR; real SoR is adopter work |
| AgentCore Runtime + Gateway | ✅ | ✅ | ✅ | ✅ | ❌ | **Public endpoints; VPC mode pending — P0-6** |
| Enterprise IdP federation (OIDC/SAML→Cedar) | ✅ | ✅ (reference) | ❌ | ◑ (mapper unit) | ❌ | Reference only; not live against a real IdP — P0-5 |
| Least-privilege IAM | ◑ | ◑ | ✅ | ❌ | ❌ | **Gateway role `bedrock-agentcore:*` on `*` — P0-4** |
| Supply-chain controls (pins/SBOM/scans) | ◑ | ❌ | n/a | ❌ | ❌ | **Unpinned deps, root container — P0-8** |
| IaC (CDK/Terraform), multi-account | ◑ | ❌ | ❌ | ❌ | ❌ | Shell scripts today — P1/P0-6/9 |
| One versioned governance core | ✅ | ❌ | ❌ | ❌ | ❌ | Four copies today — P0-9 |
| Opt-in end-to-end deploy test (deploy→demo→destroy) | ✅ | ✅ | ✅ | ✅ | ❌ | Manual workflow_dispatch |

Legend: ✅ done · ◑ partial · ❌ not yet. This matrix is the honest baseline every leadership claim
should be checked against, and it is regenerated as gates pass.

---

## Working rules for this program
1. **No ✅ without a passed validation gate.** "Implemented" ≠ "Done." Live gates require an AWS deploy.
2. **Fix the shared core once, propagate by content-hash** until P0-9 gives us real versioning.
3. **Re-read this checklist at the start of every work session** and update statuses from evidence.
4. **Prefer honest scope over impressive claims** — the review's credibility point is the whole game.
