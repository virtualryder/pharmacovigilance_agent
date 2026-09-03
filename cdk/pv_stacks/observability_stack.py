"""ObservabilityStack (GA-6, Review-2) — minimum production-operations signals as IaC.

Dashboards + alarms an operations team can actually run the pilot with. Sources are service metrics
(no app instrumentation required) plus metric filters staged for the custom security signals. SNS is
the pager seam (subscribe email/PagerDuty at deploy)."""
import aws_cdk as cdk
from aws_cdk import (aws_budgets as budgets, aws_cloudtrail as cloudtrail, aws_cloudwatch as cw,
                     aws_cloudwatch_actions as cwa, aws_iam as iam, aws_kms as kms, aws_lambda as lambda_,
                     aws_logs as logs, aws_s3 as s3, aws_sns as sns, aws_sns_subscriptions as subs,
                     custom_resources as cr)

# The budget-breach function (task 128, B4): an SNS notification from AWS Budgets naming the USD ceiling
# engages the deployment's kill switch through the IAM-authenticated engage URL. Its role is the
# IAM-verified actor the controller records, so the WORM ledger shows "engaged by the budget breach
# function, reason: AWS Budgets <name> ACTUAL > 100%". Inline so the stack has no extra asset.
_BREACH_CODE = r"""
import json, os, boto3, urllib.request
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

def handler(event, context):
    url, name = os.environ["KILL_SWITCH_ENGAGE_URL"], os.environ["BUDGET_NAME"]
    out = []
    for rec in event.get("Records", []):
        msg = (rec.get("Sns") or {}).get("Message") or ""
        subj = (rec.get("Sns") or {}).get("Subject") or ""
        if name not in msg and name not in subj:
            out.append({"skipped": True, "subject": subj[:120]}); continue
        body = json.dumps({"reason": "AWS Budgets %s: USD ceiling threshold reached - automatic containment (%s)" % (name, subj[:100])})
        req = AWSRequest(method="POST", url=url, data=body, headers={"content-type": "application/json"})
        SigV4Auth(boto3.Session().get_credentials(), "lambda", os.environ.get("AWS_REGION", "us-east-1")).add_auth(req)
        r = urllib.request.Request(url, data=body.encode(), headers=dict(req.headers), method="POST")
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                out.append({"engaged": True, "status": resp.status, "body": resp.read()[:300].decode("utf-8", "replace")})
        except Exception as exc:  # 409 = already engaged is fine
            out.append({"engaged": False, "error": str(exc)[:300]})
    print(json.dumps({"aegis": "budget_breach", "results": out}))
    return out
"""
from constructs import Construct


class ObservabilityStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, prefix: str, compute, workflow,
                 data=None, gateway=None, model_logging: bool = False, tenants=("default",),
                 budget_usd: float = 0.0, runtime_role_name: str = "", **kw):
        super().__init__(scope, cid, **kw)
        self._transparency(prefix, gateway, model_logging)
        # Gate-B: ops alarms may carry case ids — under customer-managed KMS the topic is CMK-encrypted.
        # Imported key reference (see compute_stack): cloudwatch.amazonaws.com is pre-authorized in
        # the DataStack key policy so alarms can publish to the encrypted topic.
        cmk = None
        if data is not None and getattr(data, "cmk", None) is not None:
            cmk = kms.Key.from_key_arn(self, "DataCmk", data.cmk.key_arn)
        topic = sns.Topic(self, "Alarms", topic_name=f"{prefix}-ops-alarms", master_key=cmk)

        def alarm(name, metric, threshold=0, eval_periods=1, desc=""):
            a = cw.Alarm(self, name, metric=metric, threshold=threshold,
                         evaluation_periods=eval_periods,
                         comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                         treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                         alarm_description=desc)
            a.add_alarm_action(cwa.SnsAction(topic))
            return a

        sm = workflow.controller
        # ── workflow health ──────────────────────────────────────────────────
        alarm("WorkflowFailed", sm.metric_failed(period=cdk.Duration.minutes(5)),
              desc="Determination workflow execution FAILED — investigate; cases are not being processed.")
        alarm("WorkflowTimedOut", sm.metric_timed_out(period=cdk.Duration.minutes(5)),
              desc="Execution timed out (approval older than the 24h gate?) — approval backlog or stuck state.")
        alarm("WorkflowThrottled", sm.metric_throttled(period=cdk.Duration.minutes(5)),
              desc="Executions throttled — quota pressure.")

        # ── control-plane Lambda health (the governance-critical functions) ──
        for label, fn in (("Mask", compute.mask), ("Guards", compute.guards),
                          ("Finalize", compute.finalize), ("WriteAudit", compute.write_audit),
                          ("Lookup", compute.lookup)):
            alarm(f"{label}Errors", fn.metric_errors(period=cdk.Duration.minutes(5)),
                  desc=f"{label} Lambda errors — a governance-critical function is failing "
                       f"({'masking' if label == 'Mask' else 'audit trail' if label == 'WriteAudit' else 'pipeline'} impact; fail-closed but investigate).")

        # ── R3-3 security metrics: guard failures ARE security signals ───────
        # workflow_guards emits EMF (Pharmacovigilance/Governance :: GuardFailed{Guard}) on every evaluation.
        # A nonzero sum means forged/tampered/missing evidence hit a guard — page immediately.
        guard_failed = cw.Metric(namespace="Pharmacovigilance/Governance", metric_name="GuardFailed",
                                 statistic="Sum", period=cdk.Duration.minutes(5))
        alarm("GuardFailures", guard_failed,
              desc="A workflow guard REFUSED evidence (forged sanitized_ref, tampered HUD provenance, "
                   "spoofed boolean, or source-down). Security signal - triage per THREAT-MODEL.md; "
                   "repeated failures may indicate an active forgery attempt.")

        # ── dashboard: security · workflow · ops ─────────────────────────────
        dash = cw.Dashboard(self, "Dashboard", dashboard_name=f"{prefix}-operations")
        dash.add_widgets(
            cw.GraphWidget(title="Workflow: started / succeeded / failed / timed-out", width=12,
                           left=[sm.metric_started(), sm.metric_succeeded(),
                                 sm.metric_failed(), sm.metric_timed_out()]),
            cw.GraphWidget(title="Governance Lambdas: errors", width=12,
                           left=[compute.mask.metric_errors(), compute.guards.metric_errors(),
                                 compute.write_audit.metric_errors(), compute.finalize.metric_errors()]),
        )
        dash.add_widgets(
            cw.GraphWidget(title="SECURITY: guard failures (forged/tampered evidence refused)", width=12,
                           left=[guard_failed]),
            cw.GraphWidget(title="Sign-off gate: pending approvals (finalize invocations)", width=12,
                           left=[compute.finalize.metric_invocations(),
                                 compute.signoff_register.metric_invocations()]),
        )
        dash.add_widgets(
            cw.GraphWidget(title="Governance Lambdas: duration p95", width=12,
                           left=[compute.mask.metric_duration(statistic="p95"),
                                 compute.core.metric_duration(statistic="p95"),
                                 compute.lookup.metric_duration(statistic="p95")]),
            cw.GraphWidget(title="HUD lookup: invocations vs errors (source availability)", width=12,
                           left=[compute.lookup.metric_invocations(), compute.lookup.metric_errors()]),
        )

        # ---- Evidence-store data events (observability parity 2026-08-29) ------------------------
        # A data-only trail on the agent's WORM vault: the audit ledger proves what the gateway
        # wrote; these object-level events independently prove nobody ELSE touched the evidence.
        # Management events are NONE (the platform evidence trail owns those + DynamoDB data events
        # for all tables), so this trail bills only per data event — cents at pilot volume.
        if data is not None and getattr(data, "worm_bucket", None) is not None:
            evidence_trail = cloudtrail.Trail(
                self, "WormDataEvents", trail_name=f"{prefix}-worm-data-events",
                management_events=cloudtrail.ReadWriteType.NONE,
                include_global_service_events=False, is_multi_region_trail=False)
            evidence_trail.add_event_selector(
                cloudtrail.DataResourceType.S3_OBJECT,
                [f"{data.worm_bucket.bucket_arn}/"],
                read_write_type=cloudtrail.ReadWriteType.ALL)
            cdk.CfnOutput(self, "EvidenceTrailArn", value=evidence_trail.trail_arn)

        # ── task 128: per-tenant budget alarms (B3) ──────────────────────────
        # The meter (governed-core budget.py) publishes Aegis/Budget TokensUsedPct / UsdUsedPct per
        # Tenant+Deployment on every commit; 60 / 85 / 100 % alarms go to the same ops topic.
        for t in tenants:
            for pct in (60, 85, 100):
                for metric_name in ("TokensUsedPct",) + (("UsdUsedPct",) if budget_usd > 0 else ()):
                    m = cw.Metric(namespace="Aegis/Budget", metric_name=metric_name, statistic="Maximum",
                                  period=cdk.Duration.minutes(1),
                                  dimensions_map={"Tenant": t, "Deployment": prefix})
                    a = cw.Alarm(self, f"Budget{metric_name}{pct}{t.replace('-', '')}", metric=m,
                                 threshold=pct, evaluation_periods=1,
                                 comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                                 treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                                 alarm_name=f"{prefix}-budget-{t}-{metric_name}-{pct}",
                                 alarm_description=f"Tenant {t} has used >= {pct}% of its period budget ({metric_name}). "
                                                   f"At 100% with cap_behavior=hard the tenant is refused at the runtime and the gateway.")
                    a.add_alarm_action(cwa.SnsAction(topic))

        # ── task 128: the USD backstop (B4) — AWS Budgets on Amazon Bedrock spend ───────────────
        # NOT real-time (AWS: budgets are "updated up to three times a day ... 8-12 hours after the
        # previous update"); it is the account-level ceiling that holds even if a meter is bypassed.
        # At 100 % ACTUAL: (1) a budget ACTION attaches a DENY bedrock:InvokeModel* policy to the roles
        # that call Bedrock (automatic approval), and (2) the notification reaches the ops topic, where the
        # budget-breach function ENGAGES THE KILL SWITCH through its IAM-authenticated URL - so the stop
        # is audited in the WORM ledger with the breach function's role as the IAM-verified actor.
        if budget_usd > 0:
            deny = iam.ManagedPolicy(
                self, "BudgetDenyBedrock", managed_policy_name=f"{prefix}-budget-deny-bedrock",
                description="Attached by the AWS Budgets action at 100% of the monthly USD ceiling: no model calls.",
                statements=[iam.PolicyStatement(effect=iam.Effect.DENY,
                                                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                                                         "bedrock:Converse", "bedrock:ConverseStream"],
                                                resources=["*"])])
            target_roles = [compute.core.role.role_name] + ([runtime_role_name] if runtime_role_name else [])
            exec_role = iam.Role(
                self, "BudgetsActionRole", assumed_by=iam.ServicePrincipal("budgets.amazonaws.com"),
                description="Lets AWS Budgets attach/detach the deny policy on the Bedrock-calling roles.")
            exec_role.add_to_policy(iam.PolicyStatement(
                actions=["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
                resources=[f"arn:aws:iam::{self.account}:role/{r}" for r in target_roles]))
            self.usd_budget = budgets.CfnBudget(
                self, "UsdCeiling",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name=f"{prefix}-bedrock-usd-ceiling", budget_type="COST", time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=budget_usd, unit="USD"),
                    cost_filters={"Service": ["Amazon Bedrock"]}),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type="ACTUAL", comparison_operator="GREATER_THAN", threshold=100,
                            threshold_type="PERCENTAGE"),
                        subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="SNS", address=topic.topic_arn)]),
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type="FORECASTED", comparison_operator="GREATER_THAN", threshold=100,
                            threshold_type="PERCENTAGE"),
                        subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="SNS", address=topic.topic_arn)]),
                ])
            topic.add_to_resource_policy(iam.PolicyStatement(
                actions=["sns:Publish"], resources=[topic.topic_arn],
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")]))
            action = budgets.CfnBudgetsAction(
                self, "UsdCeilingAction", budget_name=self.usd_budget.ref, action_type="APPLY_IAM_POLICY",
                approval_model="AUTOMATIC", execution_role_arn=exec_role.role_arn, notification_type="ACTUAL",
                action_threshold=budgets.CfnBudgetsAction.ActionThresholdProperty(type="PERCENTAGE", value=100),
                definition=budgets.CfnBudgetsAction.DefinitionProperty(
                    iam_action_definition=budgets.CfnBudgetsAction.IamActionDefinitionProperty(
                        policy_arn=deny.managed_policy_arn, roles=target_roles)),
                subscribers=[budgets.CfnBudgetsAction.SubscriberProperty(type="SNS", address=topic.topic_arn)])
            action.add_dependency(self.usd_budget)
            # the breach function: any notification on the ops topic that names the USD ceiling budget
            # engages the deployment's kill switch (SigV4 to the engage URL with its own role).
            breach = lambda_.Function(
                self, "BudgetBreach", function_name=f"{prefix}-budget-breach",
                runtime=lambda_.Runtime.PYTHON_3_12, handler="index.handler", timeout=cdk.Duration.seconds(30),
                code=lambda_.Code.from_inline(_BREACH_CODE),
                environment={"KILL_SWITCH_ENGAGE_URL": compute.kill_switch_urls["engage"].url,
                             "BUDGET_NAME": f"{prefix}-bedrock-usd-ceiling"})
            breach.add_to_role_policy(iam.PolicyStatement(
                actions=["lambda:InvokeFunctionUrl", "lambda:InvokeFunction"],
                resources=[compute.kill_switch_fns["engage"].function_arn],
                conditions={"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"},
                            "Bool": {"lambda:InvokedViaFunctionUrl": "true"}}))
            topic.add_subscription(subs.LambdaSubscription(breach))
            cdk.CfnOutput(self, "UsdCeilingBudgetName", value=f"{prefix}-bedrock-usd-ceiling")
            cdk.CfnOutput(self, "UsdCeilingActionId", value=action.attr_action_id)
            cdk.CfnOutput(self, "BudgetDenyPolicyArn", value=deny.managed_policy_arn)
            cdk.CfnOutput(self, "BudgetBreachFunction", value=breach.function_name)

        cdk.CfnOutput(self, "AlarmTopicArn", value=topic.topic_arn,
                      description="Subscribe ops email / PagerDuty here.")
        cdk.CfnOutput(self, "DashboardName", value=f"{prefix}-operations")

    # ── Phase 110: full transparency — every model invocation + every gateway request ────────────
    def _transparency(self, prefix, gateway, model_logging):
        """Bedrock MODEL INVOCATION LOGGING (the exact Converse request/response bodies, tagged by the
        runtime's requestMetadata: tenant / session_id / case_id) + the AgentCore GATEWAY's vended
        request logs (CloudWatch Logs delivery, log type APPLICATION_LOGS). The runtime's spans and
        logs are AgentCore-managed (/aws/bedrock-agentcore/runtimes/<agent>-<endpoint>, aws/spans).
        Sources: Bedrock model-invocation logging + AgentCore observability configuration docs."""
        self.model_log_group = None
        if model_logging:
            lg = logs.LogGroup(self, "ModelInvocationLogs", log_group_name=f"/aws/bedrock/modelinvocations/{prefix}",
                               retention=logs.RetentionDays.ONE_YEAR, removal_policy=cdk.RemovalPolicy.DESTROY)
            big = s3.Bucket(self, "ModelInvocationLargeData", block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                            encryption=s3.BucketEncryption.S3_MANAGED, enforce_ssl=True,
                            removal_policy=cdk.RemovalPolicy.DESTROY, auto_delete_objects=True)
            big.add_to_resource_policy(iam.PolicyStatement(
                actions=["s3:PutObject"], resources=[f"{big.bucket_arn}/*"],
                principals=[iam.ServicePrincipal("bedrock.amazonaws.com")],
                conditions={"StringEquals": {"aws:SourceAccount": self.account}}))
            role = iam.Role(self, "ModelInvocationLogRole", assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com", conditions={"StringEquals": {"aws:SourceAccount": self.account}}))
            role.add_to_policy(iam.PolicyStatement(actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                                                   resources=[lg.log_group_arn, f"{lg.log_group_arn}:log-stream:*"]))
            cfg = {"loggingConfig": {
                "cloudWatchConfig": {"logGroupName": lg.log_group_name, "roleArn": role.role_arn,
                                     "largeDataDeliveryS3Config": {"bucketName": big.bucket_name}},
                "textDataDeliveryEnabled": True, "imageDataDeliveryEnabled": False,
                "embeddingDataDeliveryEnabled": False, "videoDataDeliveryEnabled": False}}
            put = cr.AwsSdkCall(service="bedrock", action="putModelInvocationLoggingConfiguration",
                                parameters=cfg, physical_resource_id=cr.PhysicalResourceId.of(f"{prefix}-model-logging"))
            res = cr.AwsCustomResource(
                self, "ModelInvocationLogging", on_create=put, on_update=put,
                on_delete=cr.AwsSdkCall(service="bedrock", action="deleteModelInvocationLoggingConfiguration"),
                policy=cr.AwsCustomResourcePolicy.from_statements([
                    iam.PolicyStatement(actions=["bedrock:PutModelInvocationLoggingConfiguration",
                                                 "bedrock:DeleteModelInvocationLoggingConfiguration"], resources=["*"]),
                    iam.PolicyStatement(actions=["iam:PassRole"], resources=[role.role_arn])]))
            res.node.add_dependency(role)
            self.model_log_group = lg
            cdk.CfnOutput(self, "ModelInvocationLogGroup", value=lg.log_group_name)
            cdk.CfnOutput(self, "ModelInvocationLargeDataBucket", value=big.bucket_name)

        self.gateway_log_group = None
        if gateway is not None and getattr(gateway, "gateway_arn", None):
            glg = logs.LogGroup(self, "GatewayRequestLogs", log_group_name=f"/aws/vendedlogs/bedrock-agentcore/gateway/{prefix}",
                                retention=logs.RetentionDays.ONE_YEAR, removal_policy=cdk.RemovalPolicy.DESTROY)
            src = logs.CfnDeliverySource(self, "GatewayLogSource", name=f"{prefix}-gateway-logs",
                                         resource_arn=gateway.gateway_arn, log_type="APPLICATION_LOGS")
            dst = logs.CfnDeliveryDestination(self, "GatewayLogDestination", name=f"{prefix}-gateway-logs",
                                              destination_resource_arn=glg.log_group_arn)
            dlv = logs.CfnDelivery(self, "GatewayLogDelivery", delivery_source_name=src.name,
                                   delivery_destination_arn=dst.attr_arn)
            dlv.add_dependency(src)
            dlv.add_dependency(dst)
            self.gateway_log_group = glg
            cdk.CfnOutput(self, "GatewayRequestLogGroup", value=glg.log_group_name)
