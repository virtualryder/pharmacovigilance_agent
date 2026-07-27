# Gate-B Checklist — Pharmacovigilance ICSR Assistant

*The Gate-B security posture, as CDK switches, and what remains to a captured-evidence pilot. "Built"
means the control is in the IaC and synth-validated; "live-validated" requires the EP1 run.*

| Gate-B control | Switch | Built (synth) | Live-validated (EP1) |
|---|---|---|---|
| **B1 · Private networking + locked egress** — VPC, isolated subnets, Network Firewall allowlist = `.api.fda.gov` ONLY | `-c network_mode=private` | ✅ | ☐ |
| **B2 · Customer-managed KMS** over tables, secrets, Lambda env, log groups, SNS | `-c kms=customer-managed` | ✅ | ☐ |
| **B3 · Pilot identity** — MFA ON (software token), threat protection ENFORCED, admin-create-only, zero users; OIDC IdP federation as IaC | `-c identity_mode=pilot` | ✅ | ☐ (enterprise IdP round-trip) |
| **B4 · PHI-telemetry canary** — strict 0-hit gate across Logs/X-Ray/DLQ/SFN history | (harness) | ✅ **R3-2 pass-by-reference implemented** (no raw/masked content in SFN state — synth + runtime proven) | ☐ (run the canary on EP1 — should PASS) |
| **B5 · Tenant isolation** — deployment-pinned tenant HMAC-signed into artifacts | `-c tenant=<sponsor-id>` | ✅ | ☐ |
| **B6 · Load / replay** — concurrency + exactly-once replay storm | (harness) | ◐ | ☐ |

## Controls proven offline now (independent of EP1)

- Signed `sanitized_ref` masking proof; token boundary; deterministic guard set with DuplicateHold;
  no-fabrication openFDA; exact-ARN IAM + tamper-Deny; no users/passwords; retention profiles; gateway
  ENFORCE with the PV tool/policy set — all asserted by the 95-test suite (incl. 22 CDK assertions).

## Remaining to a captured-evidence pilot

1. **Run EP1** on a clean account with all switches (the SA/customer runs it) — capture happy path,
   DuplicateHold, PHI canary, load, exactly-once; tear down; record in `VALIDATED_RELEASE.md`; cut the tag.
2. ~~Pass-by-reference (R3-2)~~ — **done** (ingest/case-store; raw + masked content out of SFN state).
3. **Enterprise IdP** round-trip and **QPPV SME sign-off** (`docs/SME-REVIEW-PACKET.md`). The
   operating-model doc bundle is now complete: KEY-MANAGEMENT, RETENTION-PROFILES, INCIDENT-RESPONSE,
   AUDIT-READINESS, MCP-GATEWAY, CONFIGURATION-WORKSHEET, SME-REVIEW-PACKET (all in `docs/`).
4. **Gate D** (production): independent pen test, multi-account, GA-2 split keys, MedDRA/WHODrug + E2B
   gateway integration.

See `PV-PILOT-READINESS-PLAN.md` for the full gate sequence.
