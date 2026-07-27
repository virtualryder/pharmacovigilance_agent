# Key Management — Pharmacovigilance ICSR Assistant

*Signing keys, encryption keys, rotation. One page.*

---

## Keys in the system

| Key | Purpose | Where | Rotation |
|---|---|---|---|
| **Provenance signing secret** (HMAC-SHA256) | Signs the mask_pii `sanitized_ref` (proof-of-masking) and the openFDA provenance token | AWS Secrets Manager `pv-<env>/provenance-signing`, referenced by ARN (`PROVENANCE_SECRET_ARN`) — never plaintext in the template | New secret version; consumers re-read on cold start |
| **Customer-managed KMS CMK** (`-c kms=customer-managed`) | Encrypts DynamoDB tables, Secrets Manager secrets, Lambda env vars, log groups, SNS | KMS key `alias/pv-<env>-data`, key rotation ON, `RETAIN` on stack delete | Automatic annual rotation (AWS-managed rotation of the CMK) |
| **Cognito / OIDC** | Identity (pilot MFA, IdP federation) | Cognito user pool; enterprise IdP client secret is a Secrets Manager dynamic reference | Per the institution's IdP policy |

## Provenance signing (single-key today)

PV uses **one** HMAC signing key for both trust statements (the sanitized_ref and the openFDA
provenance). Signer and verifier are Lambdas in the same deployment/account that already share a trust
boundary, so a per-deploy shared secret binds each proof to its genuine minter. Absent the secret, every
sign/verify fails closed (`authoritative:false`) — the assistant never proceeds on an unproven value.

**Follow-on (GA-2 domain split):** the financial-aid agent splits the de-identification key from the
source-provenance key so neither minter can forge the other's statement. For PV this is a hardening
item, not a pilot blocker — documented in `PV-PILOT-READINESS-PLAN.md`.

## Rotation procedure (signing secret)

1. Put a new version on the Secrets Manager secret (`pv-<env>/provenance-signing`).
2. Lambdas pick it up on the next cold start; in-flight executions using the prior version still verify
   because the verifier reads the same secret. (Overlap is acceptable for a short window; force a cold
   start via a config change to cut over immediately.)
3. Record the rotation in the change log.

## Compromise response

Suspected exposure of the signing secret or CMK → rotate immediately (new secret version / schedule CMK
disable+replace), review the WORM audit chain (`verify_chain`) for integrity, and follow
`docs/INCIDENT-RESPONSE.md`. The WORM vault + audit ledger are RETAIN'd, so evidence survives key
rotation.
