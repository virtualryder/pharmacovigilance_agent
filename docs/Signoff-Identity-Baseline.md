# Sign-off identity baseline (P0-5)

The human sign-off gate is only as strong as the identity behind the approval. This note records how the
gate establishes *who* requested a commit and *who* approved it, and the enterprise-identity baseline an
adopter is expected to stand up around it.

## What the code enforces

Both sides of the separation-of-duties gate derive identity from a **cryptographically validated Cognito
access token**, never from a name in the request body:

- `request_signoff` and `approve_signoff` call `lib/controls/identity.py`, which performs full
  **RS256 / JWKS** signature verification against the Cognito issuer's published keys (pure standard
  library — no crypto package on the Lambda path, the same primitive used by the governed connector), then
  checks the standard access-token claims: issuer, `token_use = access`, `client_id`, expiry, and
  membership in the reviewer group (`cognito:groups`).
- The **requester** stored for the case is the verified username from the requester's token; the
  **approver** is the verified username from the approver's token.
- **Separation of duties** is enforced on those verified usernames: the approver must differ from the
  requester. A request to approve that carries a forged, expired, wrong-issuer, wrong-client, or
  out-of-group token — or a bare `{"approver":"dr_x"}` string with no token at all — is rejected and
  written to the hash-chained evidence ledger as a `DENIED` event.
- Approval is **single-use** (a compare-and-swap on the pending record) and only then releases the Step
  Functions task token that lets the workflow finalize.

Because identity is taken from the signature-verified token, a caller cannot approve as someone else by
asserting their name, and cannot self-approve by relabelling the requester.

## The enterprise-identity baseline the adopter owns

The reference ships with built-in Cognito test users so the demo is self-contained. In a real deployment
the adopter federates their corporate IdP into the same pool (see `IdP-Federation-Reference.md` and
`lib/engine/deploy_federation.sh`) and layers on the controls a regulated approval workflow requires. The
platform pattern stays unchanged — only the identity provider and its policies change.

- **Federation, not local accounts.** Corporate identities arrive from Okta / Entra / Ping / any OIDC or
  SAML IdP; group membership maps to the agent's reviewer role via the pre-token-generation mapper, so the
  same `<role>_permit` and the same sign-off gate authorize federated users unchanged, and deny-by-default
  still holds for anyone whose groups map to nothing.
- **MFA is mandatory for approvers.** Enforce phishing-resistant MFA (FIDO2/WebAuthn where possible) in the
  IdP's conditional-access policy for the reviewer/approver groups. The gate consumes the resulting token;
  the strength of that token is an IdP-side policy the adopter sets.
- **Short token lifetimes.** Keep access-token TTLs short (minutes, not hours) so a captured token has a
  small window; the verifier already rejects expired tokens. Pair with refresh-token rotation and
  revocation on the IdP side.
- **Step-up auth for the final commit.** Treat the approval of a consequential action as a high-assurance
  operation: require a fresh re-authentication / step-up (a recent `auth_time`, or an ACR/AMR claim the
  approval path checks) so an idle session cannot silently approve. The verifier is the natural place to
  add an `auth_time`/ACR check once the adopter's IdP emits it.
- **Group / entitlement mapping is reviewed.** Which IdP groups confer the approver entitlement, and the
  exact group-to-role mapping, is a security decision the adopter owns and reviews — not a default.
- **Lifecycle and revocation.** SCIM / JIT provisioning and, critically, *de-provisioning*: when an
  approver leaves or changes role, their group membership is removed at the IdP and the next token no
  longer passes the group check. Short token lifetimes bound the residual access until then.

## Validation

Unit tests (`tests/test_signoff_identity.py`) cover the claims gate (issuer / token_use / client / expiry /
group), the RS256/JWKS signature primitive (a pre-signed fixture verifies; a tampered one does not), and
the handlers (a spoofed approver string is rejected; a verified approver equal to the requester is denied
for SoD; a verified, different approver succeeds and releases the task token). The live governance demo
exercises the same path end-to-end in Cedar ENFORCE: the reviewer requests with their token, a spoofed
approver string is rejected, the requester cannot self-approve, a different qualified person's token
approves, the workflow finalizes only then, and the approval is single-use.
