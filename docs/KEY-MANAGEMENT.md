# Key Management — Pharmacovigilance ICSR Assistant

*Signing keys, encryption keys, rotation. One page.*

---

## Keys in the system

| Key | Purpose | Where | Rotation |
|---|---|---|---|
| **Provenance signing secret** (HMAC-SHA256) | Signs the mask_pii `sanitized_ref` (proof-of-masking) and the openFDA provenance token | AWS Secrets Manager `pv-<env>/provenance-signing`, referenced by ARN (`PROVENANCE_SECRET_ARN`) — never plaintext in the template | New secret version; consumers re-read on cold start |
| **Customer-managed KMS CMK** (`-c kms=customer-managed`) | Encrypts DynamoDB tables, Secrets Manager secrets, Lambda env vars, log groups, SNS | KMS key `alias/pv-<env>-data`, key rotation ON, `RETAIN` on stack delete | Automatic annual rotation (AWS-managed rotation of the CMK) |
| **Cognito / OIDC** | Identity (pilot MFA, IdP federation) | Cognito user pool; enterprise IdP client secret is a Secrets Manager dynamic reference | Per the institution's IdP policy |

## Provenance signing (one signing domain)

PV has exactly **one cryptographic signer**: `mask_pii`, which HMAC-signs the `sanitized_ref`
(proof-of-masking). The openFDA background is **not signed** — it carries only an `authoritative` flag
meaning "returned by a live lookup, not fabricated" (anti-fabrication, P0-4), so there is no second
signing domain. Signer and verifier are Lambdas in the same deployment/account that share a trust
boundary; the per-deploy secret binds the masking proof to its genuine minter. Absent the secret, every
sign/verify fails closed — the assistant never proceeds on an unproven value.

**Why GA-2 domain-split does not apply here:** the financial-aid agent splits its two *signing* keys
(de-identification vs the signed source-of-record COA) because it has two signers. PV has one. The
relevant follow-on is different: if openFDA background is later **cryptographically signed** (so a
downstream consumer can verify it is unaltered from the API), that introduces a second domain and would
then warrant a separate key. Until then, one key is correct, not a shortcut.

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
