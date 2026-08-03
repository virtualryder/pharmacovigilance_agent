"""IdentityStack — federation-ready Cognito, NO built-in users (P0-6), Gate-B pilot posture (B3).

Production identity is a federated enterprise IdP (Okta / Entra ID / Ping) through this pool — see
docs/IdP-Federation-Reference.md. This stack deliberately creates ZERO users and ships ZERO passwords;
sandbox demo users exist only in the legacy shell path behind an explicit SANDBOX_IDENTITY=1
acknowledgment.

Gate-B (`identity_mode="pilot"`): MFA becomes REQUIRED (software token only — no SMS, which is not
phishing-resistant and drags in an SNS role), Cognito threat protection (advanced security) is
ENFORCED, and an enterprise OIDC IdP can be attached AS IaC (issuer/client id via context, client
secret via a Secrets Manager dynamic reference — never plaintext in the template). Federated users
land in the SAME pool and hit the SAME deny-by-default Cedar policies as native operators."""
import aws_cdk as cdk
from aws_cdk import aws_cognito as cognito
from constructs import Construct


class IdentityStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, prefix: str,
                 identity_mode: str = "sandbox", federation: dict | None = None, **kw):
        super().__init__(scope, cid, **kw)
        if identity_mode not in ("sandbox", "pilot"):
            raise ValueError(f"unknown identity_mode {identity_mode!r}; choose sandbox or pilot")
        pilot = identity_mode == "pilot"

        self.pool = cognito.UserPool(
            self, "Pool", user_pool_name=f"{prefix}-identity",
            self_sign_up_enabled=False,
            # Gate-B: REQUIRED software-token MFA for every operator; sandbox keeps OPTIONAL so the
            # disposable validation loop stays scriptable. SMS is disabled in both modes.
            mfa=cognito.Mfa.REQUIRED if pilot else cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            # Threat protection. Replaces the deprecated `advanced_security_mode`,
            # which CDK now warns will be removed in the next major release AND
            # which hard-fails synth on some 2.2xx versions with "you cannot enable
            # Advanced Security when feature plan is not Plus" - i.e. the HARDENED
            # posture was the one that would not synthesize. Feature plan is now
            # set explicitly so the pilot posture is version-stable.
            feature_plan=(cognito.FeaturePlan.PLUS if pilot
                          else cognito.FeaturePlan.ESSENTIALS),
            standard_threat_protection_mode=(
                cognito.StandardThreatProtectionMode.FULL_FUNCTION if pilot
                else cognito.StandardThreatProtectionMode.NO_ENFORCEMENT),
            password_policy=cognito.PasswordPolicy(
                min_length=14, require_lowercase=True, require_uppercase=True,
                require_digits=True, require_symbols=True),
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Gate-B: enterprise OIDC federation as IaC. The client SECRET arrives as a Secrets Manager
        # DYNAMIC REFERENCE (resolved by CloudFormation at deploy) — it never appears in the template.
        self.federated_idp = None
        fed = federation or {}
        if fed.get("issuer_url") and fed.get("client_id"):
            secret_arn = fed.get("client_secret_arn") or ""
            client_secret = (cdk.SecretValue.secrets_manager(secret_arn).unsafe_unwrap()
                             if secret_arn else "")
            self.federated_idp = cognito.UserPoolIdentityProviderOidc(
                self, "EnterpriseIdp", user_pool=self.pool,
                name=f"{prefix}-enterprise",
                issuer_url=fed["issuer_url"], client_id=fed["client_id"],
                client_secret=client_secret,
                scopes=["openid", "email", "profile"],
                attribute_mapping=cognito.AttributeMapping(
                    email=cognito.ProviderAttribute.other("email")),
            )
            # Hosted-UI domain so the OIDC round-trip has an endpoint (prefix must be dns-safe).
            self.pool.add_domain("FederationDomain", cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"{prefix}-pv".replace("_", "-")))

        providers = [cognito.UserPoolClientIdentityProvider.COGNITO]
        if self.federated_idp is not None:
            providers.append(cognito.UserPoolClientIdentityProvider.custom(self.federated_idp.provider_name))
        self.client = self.pool.add_client(
            "GatewayClient", user_pool_client_name=f"{prefix}-gw",
            auth_flows=cognito.AuthFlow(user_srp=True),   # no USER_PASSWORD_AUTH in the CDK path
            generate_secret=False,
            supported_identity_providers=providers,
        )
        if self.federated_idp is not None:
            self.client.node.add_dependency(self.federated_idp)

        cognito.CfnUserPoolGroup(self, "ReviewerGroup", user_pool_id=self.pool.user_pool_id,
                                 group_name="pv_reviewer",
                                 description="Qualified pharmacovigilance reviewers (Cedar role group)")
        cdk.CfnOutput(self, "UserPoolId", value=self.pool.user_pool_id)
        cdk.CfnOutput(self, "ClientId", value=self.client.user_pool_client_id)
        cdk.CfnOutput(self, "IdentityMode", value=identity_mode)
        cdk.CfnOutput(self, "FederationNote",
                      value=("Enterprise OIDC IdP attached as IaC; map IdP groups to pv_reviewer "
                             "(docs/IdP-Federation-Reference.md)." if self.federated_idp is not None else
                             "No users are created by IaC; federate the enterprise IdP "
                             "(docs/IdP-Federation-Reference.md) or create operators out-of-band."))
