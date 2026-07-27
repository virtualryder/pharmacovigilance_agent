"""ObservabilityStack (GA-6, Review-2) — minimum production-operations signals as IaC.

Dashboards + alarms an operations team can actually run the pilot with. Sources are service metrics
(no app instrumentation required) plus metric filters staged for the custom security signals. SNS is
the pager seam (subscribe email/PagerDuty at deploy)."""
import aws_cdk as cdk
from aws_cdk import aws_cloudwatch as cw, aws_cloudwatch_actions as cwa, aws_kms as kms, aws_sns as sns
from constructs import Construct


class ObservabilityStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, prefix: str, compute, workflow,
                 data=None, **kw):
        super().__init__(scope, cid, **kw)
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

        cdk.CfnOutput(self, "AlarmTopicArn", value=topic.topic_arn,
                      description="Subscribe ops email / PagerDuty here.")
        cdk.CfnOutput(self, "DashboardName", value=f"{prefix}-operations")
