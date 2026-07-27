"""ComputeStack (PV) — the governed tool Lambdas with explicit least-privilege IAM (P0-5/P0-7).

One function per manifest tool target, from a single staged asset bundle (tools + shared controls).
IAM is explicit and minimal per function: the audit writer can only PutItem the ledger + PutObject the
vault (with an explicit Deny on mutation/bypass); mask_pii can only Comprehend-detect + write the
sanitized store; the assessor/guards/drafter only read the sanitized store; the drafter only invokes
Bedrock. Exact ARNs are exported — nothing downstream discovers by name (P0-7).

PV vs the financial-aid port: single-key provenance (no GA-2 domain split), openFDA needs no API key
(public), and there is no pass-by-reference case store (the pipeline passes de-identified content bound
by the signed sanitized_ref)."""
import aws_cdk as cdk
from aws_cdk import (aws_ec2 as ec2, aws_iam as iam, aws_kms as kms, aws_lambda as lambda_,
                     aws_logs as logs, aws_secretsmanager as sm)
from constructs import Construct

RUNTIME = lambda_.Runtime.PYTHON_3_12


class ComputeStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, prefix: str, asset_dir: str, data,
                 provenance_secret: str = "", network=None, tenant: str = "", **kw):
        super().__init__(scope, cid, **kw)
        code = lambda_.Code.from_asset(asset_dir)
        # Gate-B (customer-managed KMS): when the DataStack used kms=customer-managed, the SAME CMK
        # protects this stack's secrets, Lambda env vars, and log groups. Imported by ARN so grants land
        # on the FUNCTION ROLES here (avoids a cross-stack key-policy cycle).
        cmk = None
        if getattr(data, "cmk", None) is not None:
            cmk = kms.Key.from_key_arn(self, "DataCmk", data.cmk.key_arn)
        common_env = {
            "AUDIT_TABLE": data.audit_table.table_name,
            "WORM_BUCKET": data.worm_bucket.bucket_name,
            "SANITIZED_TABLE": data.sanitized_table.table_name,
            "PENDING_TABLE": data.pending_table.table_name,
            "CASE_TABLE": data.case_table.table_name,   # R3-2 pass-by-reference store
        }
        # Gate-B B5: the deployment's pinned tenant (one PHA/sponsor per isolated deployment).
        if tenant:
            common_env["TENANT_ID"] = tenant
        # Per-deploy signing secret (P0-1). DEFAULT: a generated AWS Secrets Manager secret referenced by
        # ARN — never plaintext in the template. A context-supplied plaintext secret is available for
        # disposable sandbox validation ONLY. Single key (PV provenance is single-domain).
        self.signing_secret = None
        if provenance_secret:
            common_env["PROVENANCE_SECRET"] = provenance_secret   # sandbox-only path
        else:
            self.signing_secret = sm.Secret(
                self, "SigningSecret", secret_name=f"{prefix}/provenance-signing",
                description="HMAC key: signs mask_pii sanitized-artifact refs + openFDA provenance (rotate via new version; consumers re-read on cold start)",
                generate_secret_string=sm.SecretStringGenerator(password_length=64, exclude_punctuation=True),
                encryption_key=cmk)
            common_env["PROVENANCE_SECRET_ARN"] = self.signing_secret.secret_arn

        def fn(name, handler_module, env=None, timeout=30):
            log_group = None
            if cmk is not None:
                log_group = logs.LogGroup(
                    self, name.replace("-", " ").title().replace(" ", "") + "Logs",
                    log_group_name=f"/aws/lambda/{prefix}-{name}",
                    encryption_key=cmk, retention=logs.RetentionDays.ONE_YEAR,
                    removal_policy=cdk.RemovalPolicy.DESTROY)
            net = {}
            if network is not None:
                net = dict(vpc=network.vpc,
                           vpc_subnets=ec2.SubnetSelection(subnet_group_name="app"),
                           security_groups=[network.lambda_sg])
            f = lambda_.Function(
                self, name.replace("-", " ").title().replace(" ", ""),
                function_name=f"{prefix}-{name}", runtime=RUNTIME, code=code,
                handler=f"{handler_module}.handler",
                timeout=cdk.Duration.seconds(timeout), memory_size=256,
                environment={**common_env, **(env or {})},
                environment_encryption=cmk, log_group=log_group, **net,
            )
            if cmk is not None:
                cmk.grant_decrypt(f)
            return f

        # PV governed tool set (manifest targets).
        self.ingest = fn("ingest-case", "ingest_case")   # R3-2: the only door for raw content
        self.intake = fn("intake-icsr", "intake_icsr")
        self.lookup = fn("openfda-lookup", "openfda_lookup")        # public egress; no API key
        self.mask = fn("mask-pii", "mask_pii")
        self.assess = fn("assess-seriousness", "assess_seriousness")
        self.duplicate = fn("detect-duplicate", "detect_duplicate")
        self.causality = fn("record-causality", "record_causality")
        self.core = fn("core-tools", "pv_core", timeout=60)         # draft_narrative (Bedrock)
        self.write_audit = fn("write-audit", "write_audit")
        self.request_signoff = fn("request-signoff", "request_signoff")
        self.signoff_register = fn("signoff-register", "signoff_register")
        self.finalize = fn("finalize", "finalize_signoff")
        self.guards = fn("workflow-guards", "workflow_guards")

        # ── explicit least-privilege wiring ──────────────────────────────────
        # Signing secret: readable ONLY by the minter (mask_pii) + the verifiers (assess/causality/
        # duplicate/core/guards). openFDA provenance is signed by the lookup, so it reads it too.
        if self.signing_secret is not None:
            for f in (self.mask, self.assess, self.causality, self.duplicate,
                      self.core, self.guards, self.lookup):
                self.signing_secret.grant_read(f)
        # R3-2 case store: ingest WRITES raw content; intake + mask READ it (the only two consumers of
        # raw text). Nothing else touches raw content; only opaque refs cross Step Functions state.
        data.case_table.grant(self.ingest, "dynamodb:PutItem")
        data.case_table.grant(self.intake, "dynamodb:GetItem")
        data.case_table.grant(self.mask, "dynamodb:GetItem")
        data.pending_table.grant(self.signoff_register, "dynamodb:PutItem")
        data.pending_table.grant_read_write_data(self.finalize)
        # masking: detect PII + write the sanitized store (PutItem only)
        self.mask.add_to_role_policy(iam.PolicyStatement(
            actions=["comprehend:DetectPiiEntities"], resources=["*"]))
        data.sanitized_table.grant(self.mask, "dynamodb:PutItem")
        # R3-2: the drafter also WRITES the sanitized store — it persists the CIOMS narrative under a
        # signed ref so the narrative text never crosses Step Functions state (draft output pass-by-ref).
        data.sanitized_table.grant(self.core, "dynamodb:PutItem")
        # sanitized-store readers (content channel)
        for f in (self.core, self.guards, self.assess):
            data.sanitized_table.grant(f, "dynamodb:GetItem")
        # drafter: Bedrock only
        self.core.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"], resources=["*"]))
        # audit writer: append-only + WORM put, with explicit tamper Deny
        data.audit_table.grant(self.write_audit, "dynamodb:PutItem",
                               "dynamodb:GetItem", "dynamodb:TransactWriteItems")
        data.worm_bucket.grant_put(self.write_audit)
        self.write_audit.add_to_role_policy(iam.PolicyStatement(
            effect=iam.Effect.DENY,
            actions=["dynamodb:DeleteItem", "dynamodb:UpdateItem",
                     "s3:DeleteObject", "s3:DeleteObjectVersion",
                     "s3:PutObjectRetention", "s3:PutObjectLegalHold",
                     "s3:BypassGovernanceRetention"],
            resources=[data.audit_table.table_arn,
                       data.worm_bucket.bucket_arn, f"{data.worm_bucket.bucket_arn}/*"]))
        # request_signoff records INTENT evidence + starts the sign-off machine
        data.audit_table.grant(self.request_signoff, "dynamodb:PutItem",
                               "dynamodb:GetItem", "dynamodb:TransactWriteItems")
        data.worm_bucket.grant_put(self.request_signoff)
        # finalize: writes the COMMITTED evidence + the exactly-once FINAL# marker (conditional put)
        data.audit_table.grant(self.finalize, "dynamodb:PutItem",
                               "dynamodb:GetItem", "dynamodb:TransactWriteItems")
        data.worm_bucket.grant_put(self.finalize)

        for name, f in {
            "IntakeArn": self.intake, "OpenfdaArn": self.lookup, "MaskArn": self.mask,
            "AssessArn": self.assess, "DuplicateArn": self.duplicate, "CoreArn": self.core,
            "WriteAuditArn": self.write_audit, "RequestSignoffArn": self.request_signoff,
            "GuardsArn": self.guards,
        }.items():
            cdk.CfnOutput(self, name, value=f.function_arn)   # exact ARNs (P0-7)
