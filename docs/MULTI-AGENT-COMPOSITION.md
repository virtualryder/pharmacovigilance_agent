# Multi-agent composition — what is proven, what is designed, what is roadmap

**Read this first.** This document exists because "how does this work with other agents?" has three
possible answers and only one of them is true today. Conflating them is the fastest way to lose a
technical audience, so they are separated here and labelled.

| | Claim | Status |
|---|---|---|
| **A** | A reusable governed-agent **pattern**, re-instantiated across four regulated domains | **Proven.** Deployed and live-validated four times. |
| **B** | A **shared control plane** that agents onboard onto — one policy engine, one ledger, one identity plane | **Target architecture. Not built.** Specified below with the gaps named. |
| **C** | Agents that **compose** — agent A hands off to agent B under governance | **Roadmap.** No orchestration or agent-to-agent mechanism exists. |

Everything below is verifiable from the repositories. Where something is not built, it says so.

---

## A. The proven claim — a reusable pattern, instantiated four times

Four independently deployable agents in four regulated domains share one control-plane design:

| Agent | Domain | Stack prefix | Regulatory frame |
|---|---|---|---|
| `pharmacovigilance_agent` | Drug safety / ICSR intake | `pv-` | ICH E2B(R3), 21 CFR 314.80 / 312.32, Part 11, GVP |
| `benefits_eligibility_agent` | Public benefits screening | `ben-` | SNAP 7 CFR 273, Medicaid 42 CFR 435, *Goldberg v. Kelly* |
| `edu_financial_aid_agent` | Student financial aid | `fa-` | FERPA, GLBA |
| `Housing_eligibility_agent` | Housing eligibility | `hou-` | HUD program rules, Fair Housing |

### What is genuinely identical — the cryptographic core

Eight of fifteen control-plane modules are **byte-identical across all four agents** (verified by
hash, 2026-08-03):

`evidence.py` · `verify_chain.py` · `write_audit.py` · `identity.py` · `approve_signoff.py` ·
`request_signoff.py` · `idp_group_mapper.py` · `mcp_client.py`

That set is precisely the security-critical core: the hash-chained append-only ledger, chain
verification, the audit writer, JWKS identity verification, and the separation-of-duties approval
path. **The part that must not vary does not vary.** That is the strongest form of claim A and it is
the one to lead with.

### What legitimately differs — domain shaping

`workflow_guards.py` differs in all four, correctly. It encodes each domain's pipeline: PV guards an
`intake → openfda → mask → assess → draft → audit → signoff` sequence; benefits guards
`intake → mask → assess → notice → audit → signoff`. The *mechanism* (machine-verifiable transition
evidence, fail-closed defaults) is identical; the *sequence* is domain-specific. This is correct by
design, not drift.

Cedar policy sets follow the same shape — a domain permit plus mask-before-X forbids plus
no-self-commit forbids — with domain-specific action names:

| Agent | Policies |
|---|---|
| PV | `pv_reviewer_permit`, `mask_before_assess/causality/draft`, `no_self_submit`, `no_self_causality_commit` |
| Benefits | `caseworker_permit`, `mask_before_assess/draft/overpayment/redetermine`, `no_self_commit`, `no_self_fraud_referral` |
| EDU | `aid_officer_permit`, `mask_before_assess/draft/pj`, `no_self_commit`, `no_self_professional_judgment` |
| Housing | `housing_specialist_permit`, `mask_before_assess/draft/overpayment/recertify`, `no_self_commit`, `no_self_fraud_referral` |

### What is drift — and why it is the argument for B

Copying is not sharing. Three modules have diverged in ways that are **not** domain shaping:

| Module | Divergence | Assessment |
|---|---|---|
| `finalize_signoff.py` | EDU and Housing implement an exactly-once `FINAL#` conditional-put marker (52 lines). **PV and Benefits do not** (26 lines, no marker). | **Capability gap.** See the defect note below. |
| `provenance.py` | EDU and Housing support multiple signing trust domains (`_DOMAINS`); PV and Benefits are single-domain. | Documented as scoped in PV, but the newer control did not flow back. |
| `sanitized.py`, `case_store.py`, `signoff_register.py` | Comment and structural drift | Mostly cosmetic; still unversioned. |

> ### Defect found by this analysis (2026-08-03)
>
> **PV documents and grants IAM permission for a control it does not implement.**
>
> - `cdk/pv_stacks/compute_stack.py:138` — comment: *"finalize: writes the COMMITTED evidence + the
>   exactly-once FINAL# marker (conditional put)"*, and grants `dynamodb:TransactWriteItems`.
> - `DEPLOYMENT-GUIDE.md:51` — documents *"finalize (exactly-once `FINAL#` marker)"*.
> - `evidence/EP1-VALIDATION.md` — claims the marker is covered by a unit test.
> - `lib/controls/finalize_signoff.py` — **contains no `FINAL#` marker, no conditional put, and no
>   exactly-once logic.** `tests/test_audit_chain.py::test_idempotent_replay` exercises the *audit
>   chain's* idempotency (which lives in the shared `evidence.py`), not finalize exactly-once.
>
> **Consequence for this workload specifically.** Without the commit-gate marker, a retried Lambda, a
> replayed execution, or a second approval path can write a second COMMITTED record — i.e. an
> uncontrolled path to **double-reporting an ICSR to a regulator**. The threat model treats
> double-reporting as the outcome `DuplicateHold` exists to prevent; this is a different route to the
> same harm.
>
> **This is exactly the failure mode a shared control plane prevents.** A hardening fix (GA-5) landed
> in two agents and never reached the other two, and nothing detected it — because there is no shared
> package, no version pin between agents, and no drift gate. The fix is a port from
> `edu_financial_aid_agent/lib/controls/finalize_signoff.py`, which is proven in two agents.

---

## B. The target architecture — a shared control plane (NOT BUILT)

Today each agent deploys its own everything: its own 7-stack CDK set, its own audit ledger, its own
Cedar policy set, its own identity pool, its own KMS key. Nothing is shared at runtime. There is no
shared Python package — `lib/controls/` is copied into each repository.

The target inverts that: **the governance substrate is deployed once; agents onboard onto it.**

### What would become shared

| Component | Today | Target | Why |
|---|---|---|---|
| Control-plane code | Copied into 4 repos | One versioned, pinned package | Kills the drift class above. A hash-chain fix ships once. |
| Audit ledger | One per agent | One ledger, agent-scoped partition key | A single evidence chain across all agents is what an auditor actually wants. |
| Cedar policy engine | One per agent | One engine, agent as a first-class principal | Enables cross-agent isolation to be *expressed*, not just implied by account separation. |
| Identity plane | One pool per agent | One pool, agent-scoped groups/claims | One deprovisioning action removes a reviewer everywhere. |
| Signing keys | Per-deploy secret | Per trust domain, centrally rotated | Rotation currently has no bounded cutover window. |
| WORM evidence vault | One per agent | One vault in a **separate log-archive account** | Today a single compromised admin credential reaches both the system and its evidence. |

### What must NOT become shared

Stated explicitly, because "shared platform" invites the wrong inference:

- **Domain rules.** Seriousness criteria, eligibility thresholds and reporting clocks stay per-agent
  and per-market. A shared rules engine would be a regulatory liability.
- **Data.** Case stores stay isolated per agent and per tenant. Shared governance, never shared PHI/PII.
- **Human approval authority.** A qualified PV reviewer is not a qualified housing specialist. Roles
  are domain-scoped even on a shared identity plane.

### Cross-agent isolation — how Cedar would express it

Cedar today treats the *human* as principal and the *tool* as resource. In a shared plane the agent
becomes a principal too, and the default is deny:

```cedar
// Agents may invoke only tools registered to their own agent scope.
forbid (principal in Agent::"*", action, resource)
unless { resource.agent_scope == principal.agent_scope };

// Consequential actions are never permitted to any agent principal,
// regardless of scope — this is the existing no_self_* rule generalised.
forbid (principal in Agent::"*", action in Action::"consequential", resource);
```

The second rule matters more than the first: it means adding an agent cannot widen the blast radius
of consequential actions, because agents are categorically excluded from them.

### What would have to be true before claiming B

1. `lib/controls/` extracted to a versioned package with pinned consumption in all four agents.
2. A drift gate in CI that fails if an agent's control-plane version lags the released package.
3. One ledger and one policy engine actually deployed and shared by two agents, live-validated.
4. Cedar cross-agent isolation implemented, with negative tests proving agent A cannot invoke agent
   B's tools.
5. Multi-account separation, with the evidence vault in a log-archive account.

Until all five are true, this section is a design, and this document says so.

---

## C. Composing agents — the human gate model (ROADMAP)

No agent-to-agent or orchestration mechanism exists. The design question — *where does the human gate
sit when several agents touch one case?* — is answered here because the answer shapes B.

### The model: gate on consequence class, not on agent boundary

Three options were considered:

| Option | Why not |
|---|---|
| Gate at every agent boundary | Reviewers approve fragments without context. Produces rubber-stamping, which is worse than fewer, better-informed gates. |
| Gate once at the end | A human signs off on a chain they never observed. Makes the §11.70 signature-to-record problem *worse*, not better. |
| **Gate on consequence class** | **Chosen.** The gate follows the action's risk, regardless of how many agents touched the case. |

**This is not a new invention — it is what the existing policies already do.** `no_self_submit` and
`no_self_causality_commit` bind to *actions*, not to agents. Extending to multiple agents is a
continuation of a decision already made and already tested.

### Consequence classes

| Class | Definition | Gate |
|---|---|---|
| **Informational** | Produces a draft, an estimate, or reference context. Reversible; nothing leaves the system. | None. Audited. |
| **Consequential — internal** | Writes a determination to a system of record inside the organisation. | One qualified human, separation of duties from the requester. |
| **Consequential — external** | Reaches a regulator, a beneficiary, or a third party. Irreversible in practice. | One qualified human **plus** the domain's specific authority (e.g. QPPV for an ICSR submission). Never delegable to an agent. |

An action's class is a property of the **action**, so a chain of five informational steps across three
agents still requires exactly one gate — at the first consequential action. Conversely, two
consequential actions in a single agent require two gates. Agent count is irrelevant, which is the
point.

### What this implies for the audit record

For a multi-agent chain the ledger must record which agent performed each step and under which
version, so an inspector can reconstruct the chain. The existing entry already stamps `model_id`;
extending it with `agent_id` and `control_plane_version` is a small change and a prerequisite for C.

---

## Honest summary

- **A is proven** and is the claim to make: one governed-agent pattern, four regulated domains, the
  cryptographic core byte-identical across all four, each independently deployed and live-validated.
- **B is designed, not built.** Nothing is shared at runtime today. The drift documented above —
  including a missing exactly-once control in two of four agents — is the evidence that B is worth
  building, not evidence that it exists.
- **C is roadmap.** The gate model is decided; the mechanism is not built.

Anyone presenting this should say all three sentences.
