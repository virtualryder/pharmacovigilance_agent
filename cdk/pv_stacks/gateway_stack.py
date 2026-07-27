"""GatewayStack (GA-1, Review-2) — the AgentCore/Gateway/Cedar attachment as IaC.

Closes the reviewer's biggest gap: the authorization control plane no longer depends on
post-deployment shell steps. A custom-resource provider (cdk/gateway_provider/handler.py) executes
the proven engine sequence — policy engine → MCP gateway (CUSTOM_JWT via the identity pool) → SSM
discovery param → one target per governed tool Lambda (exact ARNs, never names) → every Cedar policy
(gateway ARN injected into forbids) → ENFORCE — and reverses it all on stack delete.

Targets are built AT SYNTH from the manifest (single source of truth), so the gateway tool schemas
can never drift from the tools the agent actually ships. Provider IAM is scoped to the AgentCore
control plane + the one SSM param + PassRole of the one gateway role."""
import json
import os
import pathlib
import re

import aws_cdk as cdk
import yaml
from aws_cdk import aws_iam as iam, aws_lambda as lambda_, custom_resources as cr
from constructs import Construct

REPO = pathlib.Path(__file__).resolve().parents[2]


def _targets_from_manifest(compute):
    """name → (lambda_arn token, MCP tool schemas) straight from the manifest."""
    m = yaml.safe_load((REPO / "agents" / "pharmacovigilance" / "manifest.yaml").read_text(encoding="utf-8"))
    fn_by_target = {
        "intake-icsr": compute.intake, "openfda-lookup": compute.lookup,
        "mask-pii": compute.mask, "assess-seriousness": compute.assess,
        "detect-duplicate": compute.duplicate, "record-causality": compute.causality,
        "pv-core": compute.core, "write-audit": compute.write_audit,
        "request-signoff": compute.request_signoff,
    }
    out = []
    for t in m["tools"]:
        fn = fn_by_target.get(t["target"])
        if fn is None:
            raise ValueError(f"manifest target {t['target']!r} has no compute function mapped (GA-1)")
        tools = []
        for tool in t["mcp_tools"]:
            props = {k: {"type": v["type"], "description": v.get("description", "")}
                     for k, v in (tool.get("input") or {}).items()}
            tools.append({"name": tool["name"], "description": tool["description"],
                          "inputSchema": {"type": "object", "properties": props,
                                          "required": tool.get("required", [])}})
        out.append({"name": t["target"], "lambda_arn": fn.function_arn, "tools": tools})
    return out


def _policies():
    """Every shipped .cedar, gateway ARN normalized to the runtime placeholder."""
    pols = []
    for p in sorted((REPO / "policies").glob("*.cedar")):
        body = p.read_text(encoding="utf-8")
        body = re.sub(r'AgentCore::Gateway::"arn:aws:bedrock-agentcore:[^"]+"',
                      'AgentCore::Gateway::"__GATEWAY_ARN__"', body)
        mode = "IGNORE_ALL_FINDINGS" if "IGNORE_ALL_FINDINGS" in body else "FAIL_ON_ANY_FINDINGS"
        pols.append({"name": p.stem, "definition": body, "validation_mode": mode})
    return pols


class GatewayStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, prefix: str, compute, identity, **kw):
        super().__init__(scope, cid, **kw)

        gw_role = iam.Role(self, "GatewayRole",
                           assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
                           description="AgentCore gateway execution role - invoke ONLY the governed tool Lambdas")
        for t in _targets_from_manifest(compute):
            pass  # arns granted below via explicit list (single evaluation)
        targets = _targets_from_manifest(compute)
        gw_role.add_to_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[t["lambda_arn"] for t in targets]))   # exact ARNs only (P0-7)
        # Live-run find (the val2 mystery failure): CreateGateway VALIDATES that the gateway role can
        # read + evaluate its policy engine; without these, creation fails with AccessDenied.
        gw_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:GetPolicyEngine", "bedrock-agentcore:GetPolicy",
                     "bedrock-agentcore:ListPolicies", "bedrock-agentcore:EvaluatePolicies",
                     # the CreateGateway permission check is a FAMILY (AuthorizeAction,
                     # PartiallyAuthorizeActions, ...) — grant the family, resource-scoped
                     "bedrock-agentcore:*Authorize*"],
            resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:policy-engine/*",
                       f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:policy-engine/*/policy/*",
                       # Live-run find #2: the check also authorizes against the GATEWAY resource itself
                       f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:gateway/*"]))

        # Live-run find #3: a FIXED function name re-created right after deletion makes CloudFormation's
        # async custom-resource invoke stall (stale name resolution). Let CDK generate a unique name.
        provider_fn = lambda_.Function(
            self, "AttachmentProvider",
            runtime=lambda_.Runtime.PYTHON_3_12, memory_size=256, timeout=cdk.Duration.minutes(15),
            code=lambda_.Code.from_asset(str(pathlib.Path(__file__).resolve().parents[1] / "gateway_provider")),
            handler="handler.handler")
        ssm_param = f"/{prefix}-pharmacovigilance/gateway-url"
        provider_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:*"],   # control-plane CRUD for engine/gateway/target/policy
            resources=["*"]))
        provider_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:PutParameter", "ssm:DeleteParameter"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{ssm_param}"]))
        provider_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["iam:PassRole"], resources=[gw_role.role_arn]))

        discovery = (f"https://cognito-idp.{self.region}.amazonaws.com/"
                     f"{identity.pool.user_pool_id}/.well-known/openid-configuration")
        attachment = cdk.CustomResource(
            self, "AgentCoreAttachment", service_token=provider_fn.function_arn,
            properties={
                "EngineName": f"{prefix.replace(chr(45), chr(95))}_pv_authz",   # API: ^[A-Za-z][A-Za-z0-9_]*$ (live-run find)
                "EngineDesc": "Deny-by-default Cedar authz for the pharmacovigilance ICSR agent (IaC-attached)",
                "GatewayName": f"{prefix}-pv-gw",
                "GatewayDesc": "Pharmacovigilance governed tool gateway (IaC-attached)",
                "GatewayRoleArn": gw_role.role_arn,
                "AuthorizerConfigJson": json.dumps({"customJWTAuthorizer": {
                    "discoveryUrl": discovery,
                    "allowedClients": [identity.client.user_pool_client_id]}}),
                "SsmParam": ssm_param,
                "TargetsJson": json.dumps(targets, default=str),
                "PoliciesJson": json.dumps(_policies()),
                "Enforcement": "ENFORCE",
            })

        cdk.CfnOutput(self, "GatewayUrl", value=attachment.get_att_string("GatewayUrl"))
        cdk.CfnOutput(self, "GatewayArn", value=attachment.get_att_string("GatewayArn"))
        cdk.CfnOutput(self, "PolicyEngineId", value=attachment.get_att_string("PolicyEngineId"))
        cdk.CfnOutput(self, "Enforcement", value=attachment.get_att_string("Enforcement"))
        cdk.CfnOutput(self, "SsmDiscoveryParam", value=ssm_param)
