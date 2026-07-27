"""DataStack — audit ledger, sanitized-artifacts store (P0-1), WORM evidence vault with configurable
retention profiles incl. COMPLIANCE (P0-12), optional customer-managed KMS."""
import aws_cdk as cdk
from aws_cdk import aws_dynamodb as ddb, aws_iam as iam, aws_kms as kms, aws_s3 as s3
from constructs import Construct

# P0-12 retention profiles (docs/RETENTION-PROFILES.md). GOVERNANCE/1d is SANDBOX ONLY.
RETENTION_PROFILES = {
    "sandbox-demo": (s3.ObjectLockMode.GOVERNANCE, cdk.Duration.days(1)),
    "pilot": (s3.ObjectLockMode.GOVERNANCE, cdk.Duration.days(90)),
    "production-reference": (s3.ObjectLockMode.COMPLIANCE, cdk.Duration.days(2555)),  # 7y schedule ref
}


class DataStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, prefix: str,
                 retention_profile: str = "sandbox-demo", kms_mode: str = "aws-managed", **kw):
        super().__init__(scope, cid, **kw)
        if retention_profile not in RETENTION_PROFILES:
            raise ValueError(f"unknown retention_profile {retention_profile!r}; "
                             f"choose one of {sorted(RETENTION_PROFILES)}")
        mode, days = RETENTION_PROFILES[retention_profile]

        self.cmk = None
        enc_ddb = ddb.TableEncryption.AWS_MANAGED
        if kms_mode == "customer-managed":
            self.cmk = kms.Key(self, "Cmk", alias=f"{prefix}-data",
                               enable_key_rotation=True,
                               removal_policy=cdk.RemovalPolicy.RETAIN)
            enc_ddb = ddb.TableEncryption.CUSTOMER_MANAGED
            # Gate-B: consumer stacks (compute/observability) use this key via an IMPORTED reference
            # (avoids a cross-stack policy cycle), so the SERVICE principals that must use the key on
            # the customer's behalf are pre-authorized HERE, scoped by encryption context / source:
            # CloudWatch Logs (CMK-encrypted Lambda log groups) and CloudWatch alarms -> SNS.
            self.cmk.add_to_resource_policy(iam.PolicyStatement(
                principals=[iam.ServicePrincipal(f"logs.{self.region}.amazonaws.com")],
                actions=["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*",
                         "kms:GenerateDataKey*", "kms:Describe*"],
                resources=["*"],
                conditions={"ArnLike": {"kms:EncryptionContext:aws:logs:arn":
                                        f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/{prefix}-*"}}))
            self.cmk.add_to_resource_policy(iam.PolicyStatement(
                principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com")],
                actions=["kms:Decrypt", "kms:GenerateDataKey*"],
                resources=["*"]))

        # Append-only audit ledger (hash-chained by lib/controls/evidence.py; IAM denies mutation).
        self.audit_table = ddb.Table(
            self, "AuditLedger", table_name=f"{prefix}-audit-ledger",
            partition_key=ddb.Attribute(name="audit_id", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=enc_ddb, encryption_key=self.cmk,
            point_in_time_recovery=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,   # evidence outlives the stack
        )

        # P0-1: sanitized-artifacts store (transient working data; TTL-expired).
        self.sanitized_table = ddb.Table(
            self, "SanitizedArtifacts", table_name=f"{prefix}-sanitized-artifacts",
            partition_key=ddb.Attribute(name="artifact_id", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=enc_ddb, encryption_key=self.cmk,
            time_to_live_attribute="expires_at",
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # R3-2 pass-by-reference case store: the raw adverse-event source lives HERE (encrypted, TTL'd)
        # and ONLY an opaque case_ref travels through Step Functions state — the workflow engine never
        # becomes a sensitive-data repository.
        self.case_table = ddb.Table(
            self, "CaseStore", table_name=f"{prefix}-case-store",
            partition_key=ddb.Attribute(name="case_ref", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=enc_ddb, encryption_key=self.cmk,
            time_to_live_attribute="expires_at",
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # Sign-off pending-approvals table (the register/approve path needs it).
        self.pending_table = ddb.Table(
            self, "PendingApprovals", table_name=f"{prefix}-pending-approvals",
            partition_key=ddb.Attribute(name="case_id", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=enc_ddb, encryption_key=self.cmk,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # WORM evidence vault (Object Lock; retention per profile).
        self.worm_bucket = s3.Bucket(
            self, "WormVault",
            object_lock_enabled=True,
            object_lock_default_retention=(
                s3.ObjectLockRetention.governance(days) if mode == s3.ObjectLockMode.GOVERNANCE
                else s3.ObjectLockRetention.compliance(days)),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=(s3.BucketEncryption.KMS if self.cmk else s3.BucketEncryption.S3_MANAGED),
            encryption_key=self.cmk,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Exact identifiers as outputs (P0-7: consumers use these, never name discovery).
        cdk.CfnOutput(self, "AuditTableName", value=self.audit_table.table_name)
        cdk.CfnOutput(self, "AuditTableArn", value=self.audit_table.table_arn)
        cdk.CfnOutput(self, "SanitizedTableName", value=self.sanitized_table.table_name)
        cdk.CfnOutput(self, "WormBucketName", value=self.worm_bucket.bucket_name)
        cdk.CfnOutput(self, "RetentionProfile", value=retention_profile)
