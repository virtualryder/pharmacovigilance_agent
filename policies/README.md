# Cedar policies (the governance core)

These six Cedar statements are the authorization model for the agent. They are the
**single most important artifact in the repo** — everything else exists to enforce them.

They are **declared in `agents/pharmacovigilance/manifest.yaml`** (under `policies:`) and
rendered to Cedar by `lib/engine/render.py` at deploy time, then attached to the AgentCore
Policy engine. The `.cedar` files here are the rendered, human-readable form, checked in so the
model is reviewable without running a deploy. The account id (`111122223333`) and gateway ARN
are placeholders — the deploy substitutes the real account and the gateway ARN.

> **Deployed names are prefixed.** AgentCore Policy names are unique per account/region, so the
> deploy prefixes each policy with the agent prefix (e.g. `pv_mask_before_assess`). This lets
> multiple template agents coexist in one account. The logical names are used below.

| Policy | Kind | What it enforces |
|---|---|---|
| `pv_reviewer_permit` | permit | Only a member of the `pv_reviewer` Cognito group may use any tool. Everything else is denied by default. |
| `mask_before_assess` | forbid | `assess_seriousness` cannot run on data that hasn't been de-identified (`deidentified == true`). |
| `mask_before_causality` | forbid | `record_causality` (causality/reportability) cannot run on un-masked PHI. |
| `mask_before_draft` | forbid | `draft_narrative` cannot run on un-masked data — the model only sees de-identified text. |
| `no_self_submit` | forbid | The agent can never call `finalize_submission`; submitting an ICSR is reachable **only** through the human sign-off gate. |
| `no_self_causality_commit` | forbid | The agent can never call `commit_causality`; committing a causality determination is a senior-safety-physician decision. |

Two rules of the engine make this airtight:

1. **Deny-by-default.** No statement, no access. `pv_reviewer_permit` is the only broad grant.
2. **Forbid wins.** A `forbid` overrides any `permit`, so the forbids cannot be circumvented by the
   permit — masking-before-processing and no-self-submit always hold.

The demo (`bash lib/engine/demo.sh agents/pharmacovigilance`) proves each of these live in
ENFORCE mode, and each denial names the exact policy that fired. `bash lib/engine/redteam.sh
agents/pharmacovigilance` proves they hold even when the agent's reasoning is fully adversarial.
