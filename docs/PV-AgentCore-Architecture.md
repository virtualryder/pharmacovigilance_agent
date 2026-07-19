# Pharmacovigilance ICSR Intake Agent — AgentCore-Native Architecture

*Target architecture for the pharmacovigilance (drug-safety) ICSR intake & reporting hero agent, built natively on Amazon Bedrock AgentCore. This note is the anchor design — it doubles as the opening of the leadership deck and the first section of the SA runbook. It is the HCLS counterpart to the benefits-eligibility (SLG) agent, produced from the same reusable governed-hero-agent template. Draft v1.0 · 2026-07.*

---

## 1. What this agent does (the regulated workflow)

Pharmacovigilance intake is high-volume, time-critical safety work: adverse-event reports arrive from patients, health-care professionals, literature, and partners, each under hard regulatory clocks. When an adverse-event source arrives, a regulated case-management workflow must run end to end:

**intake the non-PHI decision fields → pull authoritative FAERS background → de-identify PHI → assess ICH E2B(R3) seriousness and the reporting clock (expedited vs. periodic vs. routine) → draft a CIOMS/ICSR narrative → a qualified safety reviewer reviews and signs off → the submission is committed to the safety system of record.**

Under ICH E2B(R3) and EU GVP, and under FDA 21 CFR 314.80 / 312.32 (postmarket and IND expedited reporting), **a qualified person (safety reviewer) must make and commit the causality/reportability determination and the ICSR submission** — with a documented, defensible basis and a tamper-evident record. The agent intakes, looks up background, de-identifies, assesses, and drafts; it never self-submits. That single rule drives the whole security design.

## 2. Design thesis

AWS now ships, in Amazon Bedrock AgentCore, the governance primitives a regulated agent needs. So we don't build a parallel governance platform — we become **the regulated-industry pattern implemented natively on AgentCore**: governed agentic AI built on AWS-native services, plus the three last-mile controls regulated customers need that AgentCore doesn't provide out of the box. This pharmacovigilance agent is the HCLS proof of that pattern: it was produced from the same manifest-driven template as the benefits agent, by swapping the domain tools, the Cedar policies, and the masking primitive — the governance spine, runtime, and control library were reused unchanged.

## 3. Native on AgentCore vs. built alongside

| Control (governed-agent requirement) | Native? | AgentCore component / how |
|---|---|---|
| Verified human + agent identity | Native | **AgentCore Identity** — inbound JWT authorizer (Cognito / customer IdP) |
| Deny-by-default tool authorization | Native | **AgentCore Policy (Cedar)** — default-deny + forbid-wins, enforced at the Gateway |
| Least-privilege intersection (agent ∩ safety reviewer) | Native | Cedar principal with JWT group claims (`pv_reviewer`) as tags + tool-parameter conditions |
| Tools as governed endpoints | Native | **AgentCore Gateway** — Lambda → MCP tools; every call passes Policy |
| Agent hosting / runtime | Native | **AgentCore Runtime** — hosts the Strands agent, serverless, session-isolated |
| Tracing / observability | Native | **AgentCore Observability** — OpenTelemetry spans per agent/tool step |
| Fail-closed PHI de-identification | Build | `mask_pii` Gateway tool: Comprehend `DetectPiiEntities` (name, DOB, address, identifiers…), before model + before audit |
| Human sign-off gate (separation of duties) | Build | Step Functions `waitForTaskToken` — bound, single-use approval; AgentCore has no native human gate |
| Append-only, tamper-evident WORM audit (Part 11 / GxP evidence) | Build | Append-only DynamoDB + S3 Object Lock; Observability traces are for ops, not tamper-proof evidence |

## 4. Target architecture (components)

**AgentCore Runtime** hosts the Strands pharmacovigilance agent (`pv_runtime_agent`). The Strands agent gets a `BedrockAgentCoreApp` entrypoint and is deployed with the AgentCore starter toolkit (`agentcore configure` / `agentcore launch`), which containerizes it (ARM64 via CodeBuild) and manages the endpoint. The agent is generic — its workflow prompt is rendered from the manifest, so the same runtime image serves any agent built from the template.

**AgentCore Gateway** (`pv-pharmacovigilance-gw`) exposes each capability as an MCP tool backed by a Lambda target: `intake_icsr`, `openfda_lookup`, `mask_pii` (fail-closed), `assess_seriousness`, `detect_duplicate`, `record_causality`, `draft_narrative`, `write_audit`, and `request_signoff`. Because every tool call is a Gateway call, Policy can gate all of them uniformly. The consequential `finalize_submission` and `commit_causality` actions exist only behind the human gate.

**AgentCore Identity** provides inbound auth — a JWT authorizer (Amazon Cognito or the customer's IdP) authenticates the safety reviewer on whose behalf the agent acts — and outbound auth for the credentials the Gateway uses to reach connectors (the safety system of record — Argus / ArisG / E2B gateway — delivered as a labeled stub).

**AgentCore Policy (Cedar)** is the deny-by-default authorization engine (`pv_pharmacovigilance_authz`). Default-deny and forbid-wins are automatic. Principal = the OAuth user (JWT `cognito:groups` surfaced as a tag); Action = the specific tool invocation (auto-mapped from the Gateway's tool definitions); Resource = the Gateway; conditions can test both user claims and tool input parameters. This is simultaneously the deny-by-default gateway and the least-privilege intersection — natively.

**AgentCore Observability** emits OpenTelemetry spans for every agent and tool step.

**Built alongside — the regulated last mile:**
- **Fail-closed PHI de-identification:** the `mask_pii` tool de-identifies the adverse-event case (Amazon Comprehend `DetectPiiEntities` — name, DOB, address, identifiers, and more) before the model drafts and before anything is written to the audit. Fail-closed — if masking can't run, the call stops rather than exposing PHI. (The drug name is non-PHI, so `openfda_lookup` runs *before* masking.)
- **Human sign-off gate (separation of duties):** `request_signoff` starts a Step Functions execution that pauses on `waitForTaskToken`; a *different* qualified person approves with a bound, single-use token. The agent cannot submit an ICSR itself.
- **Append-only, tamper-evident WORM audit:** an append-only, tamper-evident record (append-only DynamoDB + S3 Object Lock) capturing `INTENT → COMMITTED` for the 21 CFR Part 11 / GxP evidence trail.

## 5. How one governed action flows

1. The safety reviewer authenticates (Cognito/IdP) and receives a JWT.
2. The agent (on AgentCore Runtime) decides to call a tool.
3. The call goes through AgentCore Gateway; **Inbound Auth** validates the JWT.
4. The **Policy Engine** evaluates Cedar: principal (user claims) + action (the tool) + resource (the gateway) + conditions (group, tool parameters), default-deny. A deny means the tool never runs — and the denial is auditable.
5. The allowed tool runs. For assessment and drafting, `mask_pii` runs first (fail-closed), so the model only ever sees de-identified text. (`openfda_lookup` runs on the non-PHI drug name before masking, for reviewer context.)
6. The consequential step never executes inline: `request_signoff` opens the Step Functions human gate; a second qualified person approves; only then does `finalize_submission` run.
7. Every decision and state change is written to the WORM audit, and every step is traced in Observability.

## 6. The seriousness & reporting-clock rules engine (deterministic, illustrative)

`assess_seriousness` is a **deterministic rules engine**, not a model. It applies the **ICH E2B(R3) / 21 CFR 314.80 seriousness criteria** (death, life-threatening, hospitalization, persistent/significant disability, congenital anomaly, other medically important condition) and a **reporting clock** to the de-identified decision fields, and returns SERIOUS / non-serious and the reporting category: **EXPEDITED (15-day)** for serious + unexpected, **PERIODIC** for serious + listed, **ROUTINE** for non-serious. Every determination **stamps its basis** — the criteria met and the rule source (`ICH-E2B(R3)/21CFR314.80`) — into its output, so it is traceable to a named standard in the audit, not a magic number. When expectedness is unknown it is conservatively treated as unlisted (err toward expedited) and says so. It fails closed if the case is not marked de-identified. Market-specific thresholds and clocks (e.g. IND 7-day, EU GVP timelines) remain a per-market configuration item. This is the pharmacovigilance counterpart to the benefits agent's eligibility/processing-clock step: a transparent, auditable, non-model determination that a qualified person can defend. Authoritative, non-PHI FAERS background (report count + top MedDRA reaction terms) comes from `openfda_lookup`, a live call to the public **openFDA** drug-event API that fails soft to a deterministic aggregate.

## 6a. Deeper caseload workflows (step two)

Beyond intake and assessment, the agent adds the workflows a real safety caseload needs — each a **new governed tool with its own Cedar control**, following one rule: the higher-risk the action, the stronger the governance.

- **`detect_duplicate`** — deterministic duplicate-ICSR detection on de-identified key fields (suspect product | event term | onset | reporter type). Adverse-event cases arrive from multiple channels; a suspected duplicate is **HELD** so the same case isn't reported to the regulator twice. It operates on the de-identified key only, so the governance point is the HOLD state.
- **`record_causality`** — PREPARE a documented causality/reportability determination, **the highest-risk discretionary safety judgment**. It requires a written, case-specific clinical rationale and returns a record that **a DIFFERENT senior safety physician must approve**. Fail-closed (`mask_before_causality`); it never commits.
- **`commit_causality`** — a **consequential, senior-human-only** action the agent can **never** take. Committing a causality determination is forbidden outright by `no_self_causality_commit`, exactly mirroring `no_self_submit`.

The point for an adopter: the governance model scales to new workflows with no new plumbing — a tool body plus a deny-by-default forbid — and each new forbid fires *by name* in ENFORCE.

## 7. Cedar policy model for pharmacovigilance (illustrative)

Default-deny is automatic; we author explicit permits plus a few targeted forbids. Deployed policy names are **prefixed per agent** (`pv_…`) so multiple template agents coexist in one account/region. Illustrative — final syntax is pinned against the account during deploy:

```cedar
// A pharmacovigilance safety reviewer may intake, look up, mask, assess, and draft — gated on the group claim.
permit(principal, action, resource is AgentCore::Gateway)
when { principal.hasTag("cognito:groups") &&
       principal.getTag("cognito:groups") like "*pv_reviewer*" };

// No seriousness assessment on un-masked PHI: assess requires the de-identified flag.
forbid(principal, action == AgentCore::Action::"assess-seriousness___assess_seriousness",
       resource == AgentCore::Gateway::"<gateway-arn>")
unless { context.input.deidentified == true };

// No CIOMS narrative on un-masked data.
forbid(principal, action == AgentCore::Action::"pv-core___draft_narrative",
       resource == AgentCore::Gateway::"<gateway-arn>")
unless { context.input.deidentified == true };

// The ICSR submission is never a direct tool call — only the approval workflow can finalize.
forbid(principal, action == AgentCore::Action::"pv-core___finalize_submission",
       resource == AgentCore::Gateway::"<gateway-arn>");
```

The shape is the point: a group-scoped permit, `mask_before_*` forbids that enforce masking-before-processing and masking-before-model (assess, causality, and draft), and no path for the agent to self-submit an ICSR or self-commit a causality determination.

## 8. Build order

1. **Governance spine first** — Cedar policies + Policy Engine + Gateway, with deny-by-default proven before anything else.
2. **Tools as Gateway Lambda targets** — `intake_icsr`, `openfda_lookup`, `mask_pii`, `assess_seriousness`, `detect_duplicate`, `record_causality`, `draft_narrative`, `write_audit`, `request_signoff`.
3. **Runtime + Identity** — the generic Strands agent onto AgentCore Runtime; Cognito inbound JWT wired to the Cedar principal.
4. **Human sign-off gate** — Step Functions `waitForTaskToken` wired to `request_signoff` and `finalize_submission`.
5. **WORM audit + Observability.**
6. **Manifest + validate** — the whole agent is one manifest; deploy; end-to-end run (Cedar allow/deny, live openFDA background, masking, seriousness + reporting clock, real Bedrock narrative, tamper-evident audit) + negative tests + a red-team harness; teardown. Proven live: **32/32 governance checks in ENFORCE** and **7/7 red-team ("governance holds under attack")**.

## 9. What's ours vs. the customer's (honesty boundary)

The accelerator owns: the agent, the Cedar policies, the tools, the fail-closed PHI masking, the human-gate workflow, the WORM audit design, the seriousness/reporting-clock rules engine, the live openFDA integration, the IaC/manifest, and the docs. The customer owns: IdP federation and qualified-person role mapping; validated connectors to the safety system of record (Argus / ArisG / E2B gateway); licensed MedDRA/WHODrug coding dictionaries; the authoritative market-specific reporting rules and their regulatory review; computer-system validation (CSV/CSA); and production authorization to operate. Seriousness thresholds and reporting clocks here are illustrative regulatory defaults, and `meddra_code` / `whodrug_code` / safety-system connectors ship as labeled stubs. Nothing here is production-certified on day one — and saying so is part of the credibility.

## 10. Regulatory anchors (full mapping is a separate guide)

- **ICH E2B(R3) / EU GVP** (ICSR data elements, seriousness definition, case management) → `assess_seriousness` rules engine + `draft_narrative`; the **qualified-person determination** → the human gate.
- **FDA 21 CFR 314.80 / 312.32** (postmarket 15-day and IND 7-/15-day expedited reporting) → the reporting-clock logic + the WORM record; `no_self_submit` keeps submission human-only.
- **FDA 21 CFR Part 11 / GxP** (attributable, tamper-evident electronic records and signatures; computer-system validation) → append-only WORM audit + least-privilege Cedar + the single-use sign-off; CSV/CSA remains the sponsor's.
- **HIPAA** (safeguarding PHI in the adverse-event case) → fail-closed `mask_pii` + least-privilege Cedar + tamper-evident audit + encryption.

Each of these becomes a control-to-requirement line in the regulatory-adherence guide.
