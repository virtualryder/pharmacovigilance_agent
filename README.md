# Pharmacovigilance Agent — Governed Agentic AI on Amazon Bedrock AgentCore

[![CI](https://github.com/virtualryder/pharmacovigilance_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/virtualryder/pharmacovigilance_agent/actions/workflows/ci.yml)

> **Continuous validation.** CI runs render + unit + eval on every push. An **opt-in** end-to-end job (`.github/workflows/e2e.yml`, manual `workflow_dispatch`) deploys the spine to a sandbox AWS account, proves it live with the demo in ENFORCE, and tears it down — see the workflow header for one-time setup.


A **governed** pharmacovigilance (drug-safety) ICSR intake agent for Healthcare & Life Sciences. It
assembles and codes an adverse-event report, pulls authoritative FAERS background, de-identifies PHI,
assesses seriousness and the regulatory reporting clock, drafts a CIOMS/ICSR narrative, and **pauses at
a human sign-off gate** — a qualified safety reviewer makes and commits the submission; the agent never
self-submits. Built on the same governed-hero-agent pattern as the benefits, financial-aid, and housing
agents, from a reusable, manifest-driven template.

> **Accelerator, not a certification.** Reference implementation of the *pattern*. Not a
> production-certified system. Computer-system validation (CSV/CSA), IdP federation, connectors to live
> safety systems (Argus/ArisG/E2B gateway), licensed MedDRA/WHODrug dictionaries, and production
> authorization to operate remain the adopter's responsibility. Seriousness thresholds and reporting
> clocks here are **illustrative regulatory defaults** — configure per market and product.

---

## Why this agent

Pharmacovigilance intake is high-volume, time-critical work under hard regulatory clocks (ICH E2B(R3),
GVP, FDA 21 CFR Part 11, HIPAA). It's an obvious place for an AI agent — but a regulated safety
organization cannot adopt an ungoverned one: PHI must never leak, every decision needs a tamper-evident
audit, tool access must be least-privilege, and a **qualified person must make and commit the
causality/reportability determination and the submission**. This agent keeps the human in charge and
makes the platform enforce it.

## The governed workflow

```
intake_icsr -> openfda_lookup -> mask_pii -> assess_seriousness -> draft_narrative -> write_audit -> request_signoff
   (FAERS background, non-PHI, before masking)                                                            |
                                                     safety reviewer (a DIFFERENT person) approves -> finalize_submission
```

- **intake_icsr** — extract the non-PHI decision fields (suspect product, event terms, ICH E2B
  seriousness flags, expectedness) from the raw adverse-event source.
- **openfda_lookup** — fetch **aggregate, non-PHI FAERS background** (report count + top MedDRA reaction
  terms) for the suspect drug from the live **openFDA** drug-event API. The drug name is non-PHI, so this
  runs *before* masking; the background gives the reviewer context. (The HCLS parallel to the other
  agents' authoritative-data lookups.)
- **mask_pii** — fail-closed PHI de-identification (Amazon Comprehend `DetectPiiEntities`: name, DOB,
  address, identifiers…). If masking can't run, nothing downstream proceeds.
- **assess_seriousness** — a deterministic rules engine (ICH E2B(R3) / 21 CFR 314.80 seriousness
  criteria + reporting clock) returning SERIOUS/non-serious and EXPEDITED (15-day) / PERIODIC / ROUTINE.
  No model, no licensed data.
- **draft_narrative** — a real Bedrock (Claude) CIOMS narrative, through a fail-closed output guardrail,
  on de-identified data only.
- **write_audit** — append-only DynamoDB ledger + S3 Object Lock (WORM) copy of every decision.
- **request_signoff** — starts a Step Functions separation-of-duties gate; a *different* qualified person
  approves with a single-use token before `finalize_submission` ever runs.

Authorization is **Cedar deny-by-default** at the AgentCore Gateway: `pv_reviewer_permit` (role-gated),
the `mask_before_*` forbids (no assessment/drafting on un-masked data), and `no_self_submit` (the agent
can never submit an ICSR). The Runtime discovers the gateway via SSM and validates the reviewer's Cognito
JWT.

## Tests — proven live in ENFORCE

`bash lib/engine/demo.sh agents/pharmacovigilance` exercises the full governed workflow against the
deployed system with Cedar in **ENFORCE**: deny-by-default (reviewer ALLOW / outsider DENY), the live
openFDA FAERS background lookup, fail-closed PHI masking, the mask-before forbids firing *by name*, the
seriousness + reporting-clock determination, a real guarded Bedrock narrative, the immutable WORM audit
(write-once + duplicate rejection), `no_self_submit`, and the human sign-off gate (separation of duties +
single-use token).

### Deeper caseload workflows (each a governed tool + its own Cedar control)

The higher-risk the action, the stronger the governance. Beyond intake/assessment, the agent adds:

- **`detect_duplicate`** — deterministic duplicate-ICSR detection on de-identified key fields; a suspected
  duplicate is **HELD** so the same case isn't reported twice.
- **`record_causality`** — PREPARE a documented causality/reportability determination (the highest-risk
  discretionary safety judgment); requires a written clinical rationale and **a DIFFERENT senior safety
  physician must approve**. Fail-closed (`mask_before_causality`); never commits.
- **`commit_causality`** — a **consequential, senior-human-only** action the agent can **never** take.
  Forbidden by Cedar `no_self_causality_commit` — the same deny-by-default pattern as `no_self_submit`.

## Deploy / prove / run / tear down

Requirements: AWS CLI v2 (admin, us-east-1), Python 3.12 + `pyyaml`, Bedrock model access, Bash
(Git-Bash on Windows). One agent = one manifest (`agents/pharmacovigilance/manifest.yaml`) + domain tool
bodies + Cedar policies; the engine, control library, and runtime are reused.

```bash
bash lib/engine/deploy.sh  agents/pharmacovigilance   # spine: engine -> gateway -> targets -> policies -> ENFORCE
bash lib/engine/demo.sh    agents/pharmacovigilance   # governance proof (Cedar ENFORCE)
bash lib/engine/redteam.sh agents/pharmacovigilance   # adversarial proof: governance holds under attack
# Runtime (from a fresh venv):
bash lib/runtime/setup_venv.sh
bash lib/runtime/_obs_setup.sh  agents/pharmacovigilance
bash lib/runtime/_configure.sh  agents/pharmacovigilance
bash lib/runtime/_launch.sh     agents/pharmacovigilance
bash lib/runtime/_invoke.sh     agents/pharmacovigilance pv_reviewer   # or: bash invoke_demo.sh (with sample data)
# Optional depth add-on — the governed OAuth connector (real outbound auth via AgentCore Identity, no stored secret):
bash lib/connector/deploy_connector.sh agents/pharmacovigilance   # mock OAuth SoR (MOCK-Argus-SafetyDB) + Identity provider + verify_source
bash lib/connector/prove_connector.sh  agents/pharmacovigilance   # proves OAuth + RS256/JWKS signature check + no secret + deny-by-default
bash lib/engine/destroy.sh agents/pharmacovigilance   # zero-residual teardown (identity preserved)
```

> **Windows / Git-Bash note.** Deploy from a **path without spaces**, launch long runs detached with a
> single space-free `Start-Process bash.exe -ArgumentList '/c/…/runner.sh'`, and stop orphaned sign-off
> executions before teardown. Policy names are prefixed per agent (`pv_mask_before_assess`) so multiple
> template agents coexist in one account. See `docs/` for the full SA runbook.

Test-user passwords are env-driven with placeholder defaults (`ChangeMe-*1!`) — rotate before shared use.
Region/account resolve dynamically.

## Layout

```
lib/engine/     manifest-driven engine: render.py + deploy/demo/redteam/destroy + deploy_identity + signoff.asl.tmpl
lib/controls/   shared control tools: mask_pii, write_audit, request/approve/finalize sign-off, mcp_client
lib/runtime/    generic Strands agent on AgentCore Runtime (agent.py + Dockerfile + toolkit helpers)
lib/connector/  reusable governed OAuth connector: verify_source (token via AgentCore Identity, no stored secret) + deploy/prove scripts + RS256/JWKS-verified mock SoR
agents/pharmacovigilance/
                manifest.yaml (single source of truth) + tools/ (intake_icsr, openfda_lookup,
                assess_seriousness, detect_duplicate, record_causality, pv_core) + demo_extra.sh
policies/       the six Cedar policies (rendered from the manifest), human-readable + a README
docs/           architecture note + Word/PowerPoint guides (regulatory-adherence, SA runbook, maintenance, depth-evidence, cost/latency one-pager; generators/ regenerates the guides & decks, decks)
```

## Honesty boundary

The accelerator owns the governed agent, the Cedar policies, the tools, the fail-closed PHI masking, the
human-gate workflow, the WORM audit design, the seriousness rules engine, the live openFDA integration,
the IaC, the tests. The adopter owns: IdP federation and reviewer role mapping; validated connectors to
the safety system of record (Argus/ArisG/E2B gateway); licensed MedDRA/WHODrug coding; the authoritative
market-specific reporting rules and their regulatory review; computer-system validation (CSV/CSA); and
production authorization to operate. `meddra_code` and `whodrug_code` remain licensed-dictionary stubs, and connectors to the production safety system of record (Argus/ArisG/E2B gateway) remain adopter work. The repo does ship a **real** governed OAuth connector — `verify_source` authenticates to a mock safety database via AgentCore Identity (no stored secret) and the SoR verifies the token's RS256 signature against the Cognito JWKS — as the reference pattern.

## License

Apache-2.0 — see [LICENSE](LICENSE).
