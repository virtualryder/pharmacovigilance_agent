# IdP Federation Reference — corporate SSO → Cognito → Cedar

The governed agent authorizes every tool call with **Cedar deny-by-default** at the AgentCore Gateway,
and the permit keys on the caller's `cognito:groups` claim (e.g. `... like "*aid_officer*"`). Out of the
box the deployment ships built-in Cognito test users so the demo is self-contained. This reference shows
how to swap those for **real corporate identities** from an external IdP (Okta, Microsoft Entra ID, Ping,
or any OIDC/SAML provider) — the single most common adopter requirement — **without changing a single
Cedar policy**.

## The one hard problem, and the fix

Cognito only fills `cognito:groups` from *native* Cognito groups. A user who federates in from Okta —
correctly a member of the `FinancialAidOfficers` group in Okta — arrives with **no** `cognito:groups`,
so the existing permit would deny them. Mapping the IdP group into the token is the whole job.

```
Okta / Entra ──(OIDC or SAML)──▶ Cognito user pool ──(pre-token-gen Lambda)──▶ access token
   groups claim                    attribute mapping        maps IdP groups          cognito:groups
   "FinancialAidOfficers"          → custom:idp_roles        → "aid_officer"          = ["aid_officer"]
                                                                                             │
                                        the SAME  <role>_permit  authorizes it ◀────────────┘
```

Two pieces do this, both in the repo:

1. **Attribute mapping** (in the Cognito IdP config) maps the IdP's group/role claim to a Cognito user
   attribute, `custom:idp_roles`.
2. **`lib/controls/idp_group_mapper.py`** — a Cognito **Pre-Token-Generation (V2_0)** Lambda — reads
   `custom:idp_roles`, maps each external group to the agent's Cedar role via a configurable `GROUP_MAP`,
   and overrides `cognito:groups` with the union of the user's native groups + the mapped roles. The
   existing `<role>_permit` then fires unchanged. A federated user whose groups map to nothing gets no
   role, so **deny-by-default still holds**.

`lib/engine/deploy_federation.sh` wires all of it (idempotent, optional, independent of the base deploy).

## Deploy it

Run the base spine first (`deploy.sh`) so the pool exists, then:

```bash
# OIDC (Okta / Entra / Ping / Auth0 …)
IDP_TYPE=oidc IDP_NAME=Okta \
  OIDC_ISSUER=https://<org>.okta.com \
  OIDC_CLIENT_ID=<app-client-id> OIDC_CLIENT_SECRET=<app-client-secret> \
  GROUPS_CLAIM=groups \
  GROUP_MAP='{"FinancialAidOfficers":"aid_officer","Approvers":"aid_officer"}' \
  CALLBACK_URL=https://your-app/callback LOGOUT_URL=https://your-app/logout \
  bash lib/engine/deploy_federation.sh agents/<slug>

# SAML (Entra ID / ADFS / Shibboleth …)
IDP_TYPE=saml IDP_NAME=Entra \
  SAML_METADATA_URL='https://login.microsoftonline.com/<tenant>/federationmetadata/2007-06/federationmetadata.xml?appid=<app>' \
  GROUPS_CLAIM='http://schemas.microsoft.com/ws/2008/06/identity/claims/groups' \
  GROUP_MAP='{"<entra-group-object-id>":"aid_officer"}' \
  bash lib/engine/deploy_federation.sh agents/<slug>

# Tear the federation back down (leaves the base pool + test users intact):
DESTROY=1 IDP_NAME=Okta bash lib/engine/deploy_federation.sh agents/<slug>
```

The script prints the hosted-UI sign-in URL. `STRICT=1` (the default in the script) means only the
agent's own role group is ever emitted, so a mis-mapped IdP group can never grant an unexpected role.

## What the adopter still owns

This is a **reference**, validated against your own IdP in your own account. You own: the IdP application
registration and consent; which IdP groups/claims carry role membership and the exact `GROUP_MAP`; the
security review of that claim-to-role mapping; SCIM/JIT provisioning and de-provisioning; MFA and
conditional-access policy on the IdP side; and the app's real callback/logout URLs. Anthropic's
accelerator owns the wiring pattern shown here — the attribute mapping, the pre-token-generation mapper,
and the fact that federated users land on the **same governed, deny-by-default Cedar policies** as the
built-in users. The mapper's deterministic logic is unit-tested (`tests/test_idp_mapper.py`).
