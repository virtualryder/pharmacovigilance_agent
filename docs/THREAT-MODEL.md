# Threat model — Pharmacovigilance ICSR Intake Assistant

*System-specific threats and the controls that answer them. Companion to the control-plane code and the
CDK stacks. Evidence to date is author-produced on synthetic data; independent testing is a Gate-D item.*

| # | Threat | Control | Test / evidence |
|---|---|---|---|
| T1 | **Prompt injection** — text in the case steers the model to skip masking, exfiltrate PHI, or call a forbidden tool | Deterministic masking (not promptable); Cedar deny-by-default + forbid-wins; consequential tools hidden + forbidden; output Guardrail (PHI anonymize + prompt-attack HIGH); the sanitized-ref gate refuses unproven-masked input regardless of what the model was told | `redteam.sh`; `tests/test_sanitized_artifact.py` |
| T2 | **Masking bypass** — caller/model asserts PHI is de-identified when it is not | Server-issued signed `sanitized_ref` (proof-of-masking) + `sanitized_sha256` content binding; the boolean is never accepted | `tests/test_sanitized_artifact.py` (spoofed boolean refused; substituted content refused) |
| T3 | **Token misuse** — bearer token reaches the model / telemetry, stolen, or replayed | Trusted runtime boundary: no token field in any tool schema; credential-shaped args scrubbed; runtime injects the token out-of-band into sign-off only; Lambda re-verifies; logs record `token_present` boolean only | `tests/test_token_boundary.py` |
| T4 | **Autonomous submission** — agent (or injected prompt) submits an ICSR to the regulator | Cedar `no_self_submit` forbid + tool refusal; Step Functions `waitForTaskToken` human gate; approver ≠ requester on a VERIFIED identity; single-use approval token | `tests/test_tools.py::test_core_finalize_refused`; `test_signoff_identity.py` |
| T5 | **Autonomous causality commit** — agent commits a causality/reportability determination instead of only preparing one | Cedar `no_self_causality_commit` forbid + tool refusal (agent may prepare + rationale, never commit); human-only under GVP / 21 CFR | `tests/test_tools.py::test_core_commit_causality_refused`, `::test_record_causality_requires_rationale` |
| T6 | **Fabricated background** — invented FAERS aggregate presented as real | openFDA returns `found:false` on source failure (no canned aggregate); the workflow `background` guard fails closed on an authoritative-but-empty lookup | `tests/test_openfda_provenance.py`; `tests/test_workflow_guards.py::test_background_optional_and_honest` |
| T7 | **Double-reporting** — the same ICSR submitted twice | Deterministic duplicate detection → the `duplicate` guard HOLDS at the terminal DuplicateHold state (no submission); exactly-once finalize marker | `tests/test_workflow_guards.py::test_duplicate_holds`; `test_audit_chain.py` |
| T8 | **Audit tampering / fork** — rewrite or fork history | Server-read hash-chain head + atomic CAS; `attribute_not_exists` immutability; IAM Deny on update/delete/governance-bypass; WORM S3 copy; `verify_chain` replay | `tests/test_audit_chain.py`; compute-stack tamper-Deny (`tests/test_cdk_stacks.py`) |
| T9 | **PHI in telemetry** — traces/logs become a second copy of sensitive data | Masking before model + audit; `token_present` boolean logging; Guardrail anonymizes model output | (partial) — see residual risk below |
| T10 | **Deployment-path compromise** — wrong role modified by name prefix; default creds | Exact-ARN IAM (no role-lookup-by-prefix; `_obs_setup.sh` fixed); CDK explicit IAM; zero users / no default passwords | `tests/test_token_boundary.py::test_no_role_lookup_by_name_prefix...`; `tests/test_cdk_stacks.py` (no users/passwords) |

**T9 update — CLOSED at the design/synth level (R3-2 pass-by-reference).** Raw `source` never enters
Step Functions state (ingest → opaque `case_ref`) and masked text is reached only server-side via the
signed `sanitized_ref`, so the strict PHI canary can PASS. Proven at synth (no raw/masked content in the
state machine — `tests/test_cdk_stacks.py`) + runtime (`tests/test_pass_by_reference.py`); confirmed on
the live EP1 canary run. (B5 tenant-scoped fetch on the case store is a follow-on.)

**Residual risks (tracked, not closed).** **Live EP1 evidence** (canary, load, exactly-once) is unrun;
independent penetration test + source/Cedar/prompt-injection review; enterprise IdP/MFA round-trip;
multi-account separation; QPPV SME sign-off. All current evidence is author-produced on synthetic data —
see `RELEASE-MANIFEST.md`.
