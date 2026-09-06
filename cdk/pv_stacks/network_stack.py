"""NetworkStack (Gate-B B1) — private networking + locked egress for the governed pipeline.

`network_mode=private` places every governed tool Lambda in private (isolated) subnets and forces ALL
egress through AWS Network Firewall with a DENY-BY-DEFAULT domain allowlist — the only permitted
external destination is the openFDA drug-event API (api.fda.gov) (the pipeline's single sanctioned external dependency).
AWS-service traffic never leaves the AWS network: gateway endpoints (S3, DynamoDB) + interface
endpoints (Secrets Manager, Step Functions, Comprehend, Bedrock runtime, CloudWatch Logs, KMS, STS,
SSM, CloudWatch metrics, Cognito)
serve it privately, so a compromised tool cannot exfiltrate case data to an arbitrary host.

Topology (per AZ):  app (ISOLATED) --0.0.0.0/0--> firewall endpoint --> firewall subnet --> NAT
                    (public) --> IGW; return-path symmetry: public route tables send the app CIDRs
                    back through the firewall endpoint. Firewall endpoint ids are resolved per-AZ with
                    a DescribeFirewall custom resource (CloudFormation's attr list is not AZ-ordered —
                    the documented pitfall)."""
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2, aws_iam as iam, aws_networkfirewall as nfw, custom_resources as cr
from constructs import Construct

# The single sanctioned external dependency (docs/DATA-SOURCE-POLICY.md).
ALLOWED_DOMAINS = [".api.fda.gov"]


class NetworkStack(cdk.Stack):
    # AZs every interface endpoint this VPC needs is offered in (us-east-1, checked 2026-09-05 with
    # `aws ec2 describe-vpc-endpoint-services`): cognito-idp is offered ONLY in us-east-1b/1c/1d, while
    # comprehend / bedrock-runtime / states / secretsmanager / logs / kms / sts / ssm / monitoring are in
    # every AZ. The benefits pack's earlier pin (1a + 1b) failed its Tier-1 live gate on the cognito-idp
    # endpoint ("does not support the availability zone of the subnet"). Override per account/region with
    # -c vpc_azs=<az>,<az>; AZ-name-to-physical mapping differs per account, so re-check the cognito-idp
    # AZ list when deploying elsewhere. (PAR-1 port from benefits, 2026-09-06.)
    DEFAULT_AZS = ("us-east-1b", "us-east-1c")

    def __init__(self, scope: Construct, cid: str, *, prefix: str, bedrock_principals=(), azs=(), **kw):
        super().__init__(scope, cid, **kw)
        self.azs = [a for a in (azs or self.DEFAULT_AZS) if a]

        # Live-run find (valb): the per-AZ DescribeFirewall response field is an Fn::GetAtt ATTRIBUTE
        # NAME, so the AZ must be a synth-time LITERAL — an env-agnostic stack's symbolic AZ tokens
        # produce an invalid template. AZs are therefore pinned explicitly (us-east-1 deployment path).
        self.vpc = ec2.Vpc(
            self, "Vpc", vpc_name=f"{prefix}-net",
            availability_zones=list(self.azs), nat_gateways=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="firewall", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=28),
                ec2.SubnetConfiguration(name="app", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
            ])
        app_sel = ec2.SubnetSelection(subnet_group_name="app")

        # ── deny-by-default egress: allowlist ONLY the openFDA API ───────────────
        rule_group = nfw.CfnRuleGroup(
            self, "EgressAllowlist", capacity=100, type="STATEFUL",
            rule_group_name=f"{prefix}-egress-allowlist",
            rule_group=nfw.CfnRuleGroup.RuleGroupProperty(
                rules_source=nfw.CfnRuleGroup.RulesSourceProperty(
                    rules_source_list=nfw.CfnRuleGroup.RulesSourceListProperty(
                        generated_rules_type="ALLOWLIST",
                        targets=ALLOWED_DOMAINS,
                        target_types=["TLS_SNI", "HTTP_HOST"]))),
            description="Gate-B locked egress: openFDA drug-event API is the ONLY sanctioned external destination")
        policy = nfw.CfnFirewallPolicy(
            self, "EgressPolicy", firewall_policy_name=f"{prefix}-egress-policy",
            firewall_policy=nfw.CfnFirewallPolicy.FirewallPolicyProperty(
                stateless_default_actions=["aws:forward_to_sfe"],
                stateless_fragment_default_actions=["aws:forward_to_sfe"],
                stateful_rule_group_references=[
                    nfw.CfnFirewallPolicy.StatefulRuleGroupReferenceProperty(
                        resource_arn=rule_group.attr_rule_group_arn)]))
        firewall = nfw.CfnFirewall(
            self, "EgressFirewall", firewall_name=f"{prefix}-egress",
            firewall_policy_arn=policy.attr_firewall_policy_arn,
            vpc_id=self.vpc.vpc_id,
            subnet_mappings=[nfw.CfnFirewall.SubnetMappingProperty(subnet_id=s.subnet_id)
                             for s in self.vpc.select_subnets(subnet_group_name="firewall").subnets],
            description="Inline egress firewall for the governed tool Lambdas")

        # ── per-AZ firewall endpoint ids (DescribeFirewall; attr list is not AZ-ordered) ──
        def _endpoint_for(az, i):
            res = cr.AwsCustomResource(
                self, f"FwEndpoint{i}",
                on_create=cr.AwsSdkCall(
                    service="NetworkFirewall", action="describeFirewall",
                    parameters={"FirewallArn": firewall.attr_firewall_arn},
                    physical_resource_id=cr.PhysicalResourceId.of(f"{prefix}-fw-ep-{az}")),
                on_update=cr.AwsSdkCall(
                    service="NetworkFirewall", action="describeFirewall",
                    parameters={"FirewallArn": firewall.attr_firewall_arn},
                    physical_resource_id=cr.PhysicalResourceId.of(f"{prefix}-fw-ep-{az}")),
                policy=cr.AwsCustomResourcePolicy.from_statements([iam.PolicyStatement(
                    actions=["network-firewall:DescribeFirewall"], resources=["*"])]))
            res.node.add_dependency(firewall)
            return res.get_response_field(f"FirewallStatus.SyncStates.{az}.Attachment.EndpointId")

        app_subnets = self.vpc.select_subnets(subnet_group_name="app").subnets
        public_subnets = self.vpc.select_subnets(subnet_group_name="public").subnets
        for i, az in enumerate(self.vpc.availability_zones):
            vpce = _endpoint_for(az, i)
            for s in app_subnets:
                if s.availability_zone == az:   # app default route -> firewall endpoint (never straight out)
                    ec2.CfnRoute(self, f"AppEgress{i}", route_table_id=s.route_table.route_table_id,
                                 destination_cidr_block="0.0.0.0/0", vpc_endpoint_id=vpce)
            for s in public_subnets:
                if s.availability_zone == az:   # return-path symmetry: public -> app goes back through the firewall
                    for a in app_subnets:
                        if a.availability_zone == az:
                            ec2.CfnRoute(self, f"ReturnPath{i}", route_table_id=s.route_table.route_table_id,
                                         destination_cidr_block=a.ipv4_cidr_block, vpc_endpoint_id=vpce)

        # ── AWS traffic stays on the AWS network ─────────────────────────────
        self.vpc.add_gateway_endpoint("S3Ep", service=ec2.GatewayVpcEndpointAwsService.S3, subnets=[app_sel])
        self.vpc.add_gateway_endpoint("DdbEp", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB, subnets=[app_sel])
        self.endpoints = {}
        for name, svc in (("SecretsEp", ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER),
                          ("SfnEp", ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS),
                          ("ComprehendEp", ec2.InterfaceVpcEndpointAwsService.COMPREHEND),
                          ("BedrockEp", ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME),
                          ("LogsEp", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS),
                          ("KmsEp", ec2.InterfaceVpcEndpointAwsService.KMS),
                          ("StsEp", ec2.InterfaceVpcEndpointAwsService.STS),
                          # Live-found L9 in the benefits Tier-1 gate (2026-09-06): EVERY governed tool reads
                          # the kill switch from Parameter Store before doing anything else, and the budget
                          # meter publishes to CloudWatch metrics. Without ssm + monitoring endpoints every
                          # tool hangs to its Lambda timeout in private mode and the gateway surfaces a 500.
                          # tests/test_cdk_stacks.py derives the required endpoint set from the boto3
                          # clients in the deployed Lambda bundle.
                          ("SsmEp", ec2.InterfaceVpcEndpointAwsService.SSM),
                          ("MonitoringEp", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_MONITORING),
                          # the approval/requester verifier fetches the Cognito JWKS at cold start; the
                          # firewall allowlist admits only the sanctioned external API, so the JWKS fetch
                          # must be served privately. private_dns_enabled makes the public cognito-idp
                          # hostname resolve to the endpoint inside the VPC.
                          ("CognitoIdpEp", ec2.InterfaceVpcEndpointAwsService("cognito-idp"))):
            self.endpoints[name] = self.vpc.add_interface_endpoint(
                name, service=svc, subnets=app_sel, private_dns_enabled=True)

        # ── Bedrock runtime endpoint POLICY (enforcement-perimeter review, 2026-09-05; PAR-1 port) ──
        # Network-layer half of the perimeter INSIDE the pack's own VPC: the bedrock-runtime interface
        # endpoint accepts inference calls ONLY from the governed drafter role (the compute stack's
        # core-tools Lambda, whose IAM allow additionally requires a guardrail on every call) and any
        # -c approved_bedrock_principals. Anything else in these subnets that obtains Bedrock
        # credentials is refused at the endpoint - the in-VPC counterpart of the org SCP under org/.
        # aws:PrincipalArn resolves to the ROLE arn for a role session, so a caller-chosen session name
        # cannot satisfy it. Converse / ConverseStream authorize as InvokeModel / ...WithResponseStream.
        # L12: the EXACT pinned drafter role (see compute_stack.drafter_role_name) - no wildcard, no case guess.
        from .compute_stack import drafter_role_name
        drafter_role_pattern = f"arn:aws:iam::{self.account}:role/{drafter_role_name(prefix)}"
        self.bedrock_endpoint_principals = [drafter_role_pattern] + [p for p in bedrock_principals if p]
        self.endpoints["BedrockEp"].add_to_policy(iam.PolicyStatement(
            sid="GovernedDrafterOnly", effect=iam.Effect.ALLOW,
            principals=[iam.AnyPrincipal()],
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:ApplyGuardrail"],
            resources=["*"],
            conditions={"ArnLike": {"aws:PrincipalArn": self.bedrock_endpoint_principals},
                        "StringEquals": {"aws:PrincipalAccount": self.account}}))

        # ── the governed Lambdas' security group: egress 443 only ────────────
        self.lambda_sg = ec2.SecurityGroup(
            self, "LambdaSg", vpc=self.vpc, allow_all_outbound=False,
            security_group_name=f"{prefix}-tools",
            description="Governed tool Lambdas - egress 443 only; destinations constrained by the firewall allowlist")
        self.lambda_sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443),
                                       "TLS only; Network Firewall enforces the domain allowlist")

        cdk.CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        cdk.CfnOutput(self, "AllowedEgressDomains", value=",".join(ALLOWED_DOMAINS))
        cdk.CfnOutput(self, "FirewallArn", value=firewall.attr_firewall_arn)
