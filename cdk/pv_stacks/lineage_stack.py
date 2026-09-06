"""LineageStack (#168) - capture EVERY API call in the account, into WORM custody.

The pack's ObservabilityStack already captures the AGENT'S OWN activity: the AgentCore gateway request
log, one `aegis.call` line per governed Lambda invocation, the Step Functions execution history, the
Bedrock model-invocation log (tagged tenant/session/case), and CloudTrail S3 DATA events on the WORM
evidence vault. What it did NOT have was an ACCOUNT-WIDE net: proof that EVERY AWS API call in the
account - not just the ones the agent's own log sources emit - is captured and joinable into one
lineage. That is the gap the 2026-09-03 platform review raised ("governs only gateway activity, not
the whole account") and what this stack closes.

It provisions ONE account trail that records:
  * EVERY management API call (ReadWriteType.ALL), multi-region, including global-service events
    (IAM / STS / CloudFront), so control-plane calls in any region are captured;
  * S3 object DATA events (all buckets) - the evidence-vault writes and every other object write;
  * Lambda InvokeFunction DATA events (all functions) - every governed tool invocation as AWS saw it,
    independent of whether the tool's own `aegis.call` line was emitted (that independence is what the
    coverage proof checks: a tool invoked but unaudited, or audited but not actually invoked, is an
    orphan and FAILS the proof).

LOG-FILE VALIDATION is on: CloudTrail writes a hash-chained digest so a missing or altered log file is
detectable. Delivery is to BOTH CloudWatch Logs (so `scripts/lineage_proof.py` can query the capture
with Logs Insights and join it by principal + correlation id) AND an S3 bucket with OBJECT LOCK, so the
capture itself is WORM - immutable for the retention window.

Scope + teardown. Account-level capture has cost and is a singleton-ish concern, so it is OPT-IN:
  -c capture_all=1                turn it on
  -c capture_retention_days=N     Object-Lock retention (default 1, enough to prove WORM in a gate)
  -c capture_lock_mode=GOVERNANCE (default) | COMPLIANCE
GOVERNANCE mode lets a privileged teardown bypass-delete the sandbox bucket (s3:BypassGovernanceRetention);
production uses COMPLIANCE + a real retention window, where not even root can delete within the window.
The bucket is NOT auto-delete (auto-delete cannot bypass a lock); teardown empties it with the bypass
permission and then removes it.

DynamoDB data-plane calls are captured by a dedicated, stronger source - the hash-chained WORM audit
ledger - which the lineage proof joins alongside this trail.

BEDROCK COVERAGE (enforcement-perimeter review, 2026-09-05). Bedrock's core inference calls -
InvokeModel, InvokeModelWithResponseStream, Converse, ConverseStream - are recorded by CloudTrail as
MANAGEMENT events (AWS: "Monitor Amazon Bedrock API calls using CloudTrail"), so this trail ALREADY
captures every direct model invocation in the account by ANY principal, in WORM custody with file
validation. That is the account-boundary net the model-invocation log alone cannot be: invocation
logging is a mutable account setting (anyone with bedrock:DeleteModelInvocationLoggingConfiguration
turns it off), is per-region, and is not WORM. The remaining Bedrock data-plane operations are DATA
events that CloudTrail does not log by default, so this trail selects every documented Bedrock data
resource type - ApplyGuardrail (Guardrail), InvokeAgent (AgentAlias), InvokeInlineAgent (InlineAgent),
InvokeFlow (FlowAlias), Retrieve / RetrieveAndGenerate (KnowledgeBase), StartAsyncInvoke /
InvokeModelWithBidirectionalStream (Model + AsyncInvoke) - plus the AgentCore Gateway and Runtime data
planes (AWS::BedrockAgentCore::Gateway / Runtime / RuntimeEndpoint), so a bypass through ANY of those
surfaces - and every invocation of the governed agent runtime itself - is captured too. The ObservabilityStack raises the `bedrock-perimeter-bypass` alarm from this capture
whenever a principal outside the approved allowlist invokes Bedrock (detective); PREVENTION at the
account boundary is the org SCP + VPC-endpoint policy shipped under org/ (see org/README.md).
"""
import aws_cdk as cdk
from aws_cdk import (aws_cloudtrail as cloudtrail, aws_logs as logs, aws_s3 as s3)
from constructs import Construct


class LineageStack(cdk.Stack):
    # Every Bedrock DATA-event resource type documented in "Monitor Amazon Bedrock API calls using
    # CloudTrail" (the core InvokeModel / Converse family are MANAGEMENT events, captured by the
    # management selector), plus the AgentCore Gateway data plane.
    DATA_RESOURCE_TYPES = (
        "AWS::S3::Object",
        "AWS::Lambda::Function",
        "AWS::Bedrock::Model",
        "AWS::Bedrock::AsyncInvoke",
        "AWS::Bedrock::Guardrail",
        "AWS::Bedrock::KnowledgeBase",
        "AWS::Bedrock::AgentAlias",
        "AWS::Bedrock::InlineAgent",
        "AWS::Bedrock::FlowAlias",
        "AWS::BedrockAgentCore::Gateway",
        "AWS::BedrockAgentCore::Runtime",
        "AWS::BedrockAgentCore::RuntimeEndpoint",
    )
    # Every value above was PROVED accepted by CloudTrail PutEventSelectors on 2026-09-05 (a throwaway
    # trail, one type at a time). AWS::Bedrock::Prompt - which a docs summary listed for RenderPrompt -
    # is REJECTED ("resources.type field value is not valid") and broke the first live deploy of this
    # selector set; it is deliberately absent. Re-probe before adding a type.

    @classmethod
    def advanced_event_selectors(cls):
        sel = [{"Name": "Management events (all, read + write)",
                "FieldSelectors": [{"Field": "eventCategory", "Equals": ["Management"]}]}]
        for rt in cls.DATA_RESOURCE_TYPES:
            sel.append({"Name": "Data events: %s" % rt,
                        "FieldSelectors": [{"Field": "eventCategory", "Equals": ["Data"]},
                                           {"Field": "resources.type", "Equals": [rt]}]})
        return sel

    def __init__(self, scope: Construct, cid: str, *, prefix: str,
                 retention_days: int = 1, lock_mode: str = "GOVERNANCE", **kw):
        super().__init__(scope, cid, **kw)

        mode = (lock_mode or "GOVERNANCE").upper()
        if mode not in ("GOVERNANCE", "COMPLIANCE"):
            mode = "GOVERNANCE"
        object_lock_mode = (s3.ObjectLockMode.COMPLIANCE if mode == "COMPLIANCE"
                            else s3.ObjectLockMode.GOVERNANCE)

        # WORM custody for the capture itself: versioned + Object Lock, default retention applied to
        # every delivered log file and digest. removal_policy=DESTROY, but NOT auto_delete_objects -
        # a locked object cannot be deleted by the auto-delete custom resource; teardown empties it
        # with s3:BypassGovernanceRetention (GOVERNANCE) and then the empty bucket is removed.
        worm = s3.Bucket(
            self, "CaptureWorm",
            bucket_name=f"{prefix}-capture-worm-{self.account}",
            versioned=True,
            object_lock_enabled=True,
            object_lock_default_retention=s3.ObjectLockRetention.governance(
                duration=cdk.Duration.days(retention_days)) if mode == "GOVERNANCE"
            else s3.ObjectLockRetention.compliance(duration=cdk.Duration.days(retention_days)),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # The account trail. The L2 construct manages the S3 bucket policy for cloudtrail.amazonaws.com,
        # the CloudWatch Logs group + delivery role. Management ALL + multi-region + global events +
        # file validation gives the account-wide management-plane net with tamper-evidence.
        ct_log_group = logs.LogGroup(
            self, "CaptureLogGroup",
            log_group_name=f"/aws/cloudtrail/{prefix}-capture-all",
            retention=logs.RetentionDays.ONE_YEAR,
            removal_policy=cdk.RemovalPolicy.DESTROY)

        trail = cloudtrail.Trail(
            self, "CaptureAll",
            trail_name=f"{prefix}-capture-all",
            bucket=worm,
            is_multi_region_trail=True,
            include_global_service_events=True,
            enable_file_validation=True,
            management_events=cloudtrail.ReadWriteType.ALL,
            send_to_cloud_watch_logs=True,
            cloud_watch_log_group=ct_log_group)

        # ADVANCED event selectors (a trail uses basic OR advanced selectors, never both): management
        # ALL, plus DATA events for every S3 object write (evidence vault + all buckets), every Lambda
        # invoke (every governed tool call as AWS recorded it, for the invoked-vs-audited coverage
        # check), every documented Bedrock data-plane resource type, and the AgentCore Gateway data
        # plane. One selector per resource type (CloudTrail allows a single resources.type per selector).
        cfn_trail = trail.node.default_child
        cfn_trail.add_property_deletion_override("EventSelectors")
        cfn_trail.add_property_override("AdvancedEventSelectors", self.advanced_event_selectors())

        self.trail = trail
        self.capture_log_group = ct_log_group
        self.worm_bucket = worm

        cdk.CfnOutput(self, "CaptureTrailArn", value=trail.trail_arn,
                      description="Account-wide capture trail (management ALL + S3/Lambda data events, "
                                  "multi-region, file-validation on).")
        cdk.CfnOutput(self, "CaptureLogGroupName", value=ct_log_group.log_group_name,
                      description="CloudWatch Logs group the lineage proof queries to join the capture.")
        cdk.CfnOutput(self, "CaptureWormBucket", value=worm.bucket_name,
                      description="Object-Lock (WORM) bucket holding the immutable capture; mode=%s, retention=%dd." % (mode, retention_days))
        cdk.CfnOutput(self, "CaptureLockMode", value=mode)