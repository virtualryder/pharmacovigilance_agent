"""ComputeStack (PV) — the governed tool Lambdas with explicit least-privilege IAM (P0-5/P0-7).

One function per manifest tool target, from a single staged asset bundle (tools + shared controls).
IAM is explicit and minimal per function: the audit writer can only PutItem the ledger + PutObject the
vault (with an explicit Deny on mutation/bypass); mask_pii can only Comprehend-detect + write the
sanitized store; the assessor/guards/drafter only read the sanitized store; the drafter only invokes
Bedrock. Exact ARNs are exported — nothing downstream discovers by name (P0-7).

PV vs the financial-aid port: single-key provenance (no GA-2 domain split), openFDA needs no API key
(public), and there is no pass-by-reference case store (the pipeline passes de-identified content bound
by the signed sanitized_ref).

governed-core 1.9.0 parity with benefits (2026-09-03): hybrid multi-tenant routing (1.6), correlation
(1.7), the Kill Switch (1.8) and the per-tenant budget meter (1.9) are wired here exactly as in the
benefits pack; the only differences are the PV tool set, the reviewer group, the SSM root
(/<prefix>-pharmacovigilance/) and the state-machine name."""
import aws_cdk as cdk
from aws_cdk import (aws_dynamodb as ddb, aws_ec2 as ec2, aws_iam as iam, aws_kms as kms,
                     aws_lambda as lambda_, aws_logs as logs, aws_secretsmanager as sm, aws_ssm as ssm)
from constructs import Construct

RUNTIME = lambda_.Runtime.PYTHON_3_12


class ComputeStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, prefix: str, asset_dir: str, data,
                 provenance_secret: str = "", network=None, tenant: str = "",
                 guardrail_id: str = "", guardrail_version: str = "1",
                 identity=None, approvals_client_id: str = "", multitenant: bool = False,
                 global_kill_switch: str = "", budget: dict = None, **kw):
        super().__init__(scope, cid, **kw)
        code = lambda_.Code.from_asset(asset_dir)
        cmk = None
        if getattr(data, "cmk", None) is not None:
            cmk = kms.Key.from_key_arn(self, "DataCmk", data.cmk.key_arn)

        # ── Kill Switch (task 127, governed-core 1.8.0) ──────────────────────
        # ONE SSM Parameter Store flag per deployment, under the same root as the gateway-discovery
        # parameter (/<prefix>-pharmacovigilance/*) so the Runtime's existing ssm:GetParameter grant covers
        # it. Every governed Lambda (incl. the gateway interceptor) and the Runtime read it FIRST,
        # fail-closed, with a 15 s in-process TTL cache (time-to-effect <= TTL; Parameter Store stays
        # far under its 40 TPS default). Optional -c global_kill_switch=/aegis/kill-switch adds the
        # platform-wide parameter: engaged if EITHER is engaged. Only the two controller functions
        # below may write the deployment parameter (see their roles) - nothing else in this app holds
        # ssm:PutParameter on it, and the CloudTrail PutParameter event names the true principal.
        ks_name = f"/{prefix}-pharmacovigilance/kill-switch"
        self.kill_switch_param = ssm.StringParameter(
            self, "KillSwitchParam",
            parameter_name=ks_name,
            string_value='{"engaged": false, "actor": "", "reason": "", "at": 0}',
            description="PV pack Kill Switch (containment). engaged=true => every agent action "
                        "is refused: gateway interceptor 403 + WORM DENIED record, tool Lambdas refuse, "
                        "Runtime refuses. Change ONLY via the engage/disengage function URLs "
                        "(IAM-verified actor, separation of duties). docs/ops/KILL-SWITCH.md")
        kill_params = [ks_name]
        kill_param_arns = [self.kill_switch_param.parameter_arn]
        if global_kill_switch:
            kill_params.append(global_kill_switch)
            kill_param_arns.append(f"arn:aws:ssm:{self.region}:{self.account}:parameter{global_kill_switch}")

        # ── Budget meter (task 128, governed-core 1.9.0) ────────────────────
        # ONE DynamoDB table per deployment: <tenant>#<YYYY-MM> -> used / tokens_in / tokens_out /
        # usd_micro (+ optional per-tenant cap overrides written by an operator with one PutItem).
        # The deployment DEFAULTS come from the agent manifest's budget: block (B5: one place to set the
        # number) and -c budget_usd=<dollars>/month; the pinned price table (lib/model_prices.json) is
        # passed inline so every commit records which price_version produced the USD figure.
        budget = budget or {}
        self.budgets_table = ddb.Table(
            self, "Budgets", table_name=f"{prefix}-budgets",
            partition_key=ddb.Attribute(name="budget_key", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST, encryption_key=cmk,
            encryption=ddb.TableEncryption.CUSTOMER_MANAGED if cmk else ddb.TableEncryption.AWS_MANAGED,
            removal_policy=cdk.RemovalPolicy.DESTROY)
        budget_env = {
            "BUDGET_TABLE": self.budgets_table.table_name,
            "BUDGET_CAP_TOKENS": str(int(budget.get("monthly_token_cap") or 0)),
            "BUDGET_CAP_USD_MICRO": str(int(round(float(budget.get("monthly_usd") or 0) * 1_000_000))),
            "BUDGET_BEHAVIOR": str(budget.get("cap_behavior") or "hard"),
            "BUDGET_RESERVE_TOKENS": str(int(budget.get("reserve_tokens") or 4000)),
            "BUDGET_PRICES_JSON": budget.get("prices_json") or "",
            "BUDGET_DEPLOYMENT": prefix,
        }

        common_env = {
            **budget_env,
            "KILL_SWITCH_PARAMS": ",".join(kill_params),
            "KILL_SWITCH_TTL_SECONDS": "15",
            "AUDIT_TABLE": data.audit_table.table_name,
            "WORM_BUCKET": data.worm_bucket.bucket_name,
            # The pinned governed-core evidence writer reads AUDIT_BUCKET
            # (governed_core/controls/evidence.py: _env("AUDIT_BUCKET") or
            # "evidence-worm-<acct>-<region>"). Without this alias the WORM mirror
            # silently no-ops with worm_error=NoSuchBucket (the same defect fixed on
            # benefits, cdee12c). WORM_BUCKET kept for anything reading the old name.
            "AUDIT_BUCKET": data.worm_bucket.bucket_name,
            "SANITIZED_TABLE": data.sanitized_table.table_name,
            "PENDING_TABLE": data.pending_table.table_name,
            "CASE_TABLE": data.case_table.table_name,   # R3-2 pass-by-reference store
        }
        # Gate-B B5: the deployment's pinned tenant (one sponsor per isolated deployment). Tenant identity
        # is DERIVED from this env, never from a request body (lib/controls/tenancy.py).
        if tenant:
            common_env["TENANT_ID"] = tenant
        # Hybrid multi-tenant (phase 107): tenant is derived per request from the gateway interceptor's
        # HMAC-signed injection (never the pinned env); MULTITENANT=1 makes the routing fail-closed.
        if multitenant:
            common_env["MULTITENANT"] = "1"
            # governed-core 1.6.0: the CANONICAL evidence writer routes the WORM copy to the acting
            # tenant's OWN Object Lock vault. The template is the exact per-tenant DataStack naming
            # (<prefix>-<tenant>-worm-<account>), so infra and runtime cannot drift.
            common_env["WORM_BUCKET_TEMPLATE"] = f"{prefix}-{{tenant}}-worm-{self.account}"
        # Per-deploy signing secret (P0-1). DEFAULT: a generated AWS Secrets Manager secret referenced
        # by ARN — never plaintext in the template. A context-supplied plaintext secret remains available
        # for disposable sandbox validation ONLY.
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
            # Observability review 2026-08-29: the log group is now UNCONDITIONAL —
            # 1-year retention must not be a side effect of the kms switch. CMK
            # encryption still applies only when a customer-managed key exists.
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
                environment_encryption=cmk, log_group=log_group,
                tracing=lambda_.Tracing.ACTIVE,   # X-Ray on every governed tool (obs review 2026-08-29)
                **net,
            )
            if cmk is not None:
                cmk.grant_decrypt(f)
            # Kill switch: READ the switch parameter(s) and nothing else in Parameter Store.
            f.add_to_role_policy(iam.PolicyStatement(
                sid="ReadKillSwitch", actions=["ssm:GetParameter"], resources=kill_param_arns))
            return f

        # PV governed tool set (manifest targets).
        # Hybrid multi-tenant ingestion boundary (governed-core 1.6.0): ingest is NOT a gateway tool
        # (direct IAM invocation by the intake integration), so there is no interceptor to derive the
        # tenant. In multi-tenant mode it derives the tenant from a VERIFIED Cognito access token of a
        # tenant member (RS256/JWKS, pool + client checked) and mints the signed pair the workflow
        # carries. Same identity env as approve_signoff; unused in silo mode.
        ingest_env = ({"POOL_ID": identity.pool.user_pool_id,
                       "CLIENT_ID": approvals_client_id or identity.client.user_pool_client_id,
                       "REVIEWER_GROUP": "pv_reviewer"}
                      if (multitenant and identity is not None) else None)
        self.ingest = fn("ingest-case", "ingest_case", env=ingest_env)   # R3-2: the only door for raw content
        self.intake = fn("intake-icsr", "intake_icsr")
        self.lookup = fn("openfda-lookup", "openfda_lookup")        # public egress; no API key
        self.mask = fn("mask-pii", "mask_pii")
        self.assess = fn("assess-seriousness", "assess_seriousness")
        self.duplicate = fn("detect-duplicate", "detect_duplicate")
        self.causality = fn("record-causality", "record_causality")
        # Guardrail-pinned drafting (G1 parity): pv_core already honors GUARDRAIL_ID/VERSION like
        # benefits_core; supplying the platform guardrail makes every narrative generation
        # guardrail-assessed (fail-closed on intervention).
        core_env = {}
        if guardrail_id:
            core_env = {"GUARDRAIL_ID": guardrail_id, "GUARDRAIL_VERSION": guardrail_version}
        self.core = fn("core-tools", "pv_core", env=core_env, timeout=60)  # draft_narrative (Bedrock)
        self.write_audit = fn("write-audit", "write_audit")
        self.request_signoff = fn("request-signoff", "request_signoff")
        self.signoff_register = fn("signoff-register", "signoff_register")
        self.finalize = fn("finalize", "finalize_signoff")
        self.guards = fn("workflow-guards", "workflow_guards")
        # Phase 107: the AgentCore Gateway REQUEST interceptor - derives the tenant from the VALIDATED JWT
        # and injects it HMAC-signed for the targets (a pass-through in silo mode).
        self.tenant_interceptor = fn("tenant-interceptor", "tenant_interceptor")
        # approve-signoff (G2, 2026-08-29): the human approver's OUT-OF-BAND door — verifies a
        # Cognito ACCESS token (RS256/JWKS), enforces separation of duties, consumes the single-use
        # approval (PENDING -> CONSUMED + recorded approver), and only then releases the task token.
        # It is deliberately NOT a gateway target (not an agent tool). The finalize shadow refuses
        # any approval that did not come through here, so this is now the ONLY working approve path.
        self.approve_signoff = None
        if identity is not None:
            self.approve_signoff = fn("approve-signoff", "approve_signoff", env={
                "POOL_ID": identity.pool.user_pool_id,
                "CLIENT_ID": approvals_client_id or identity.client.user_pool_client_id,
                "REVIEWER_GROUP": "pv_reviewer",
            })

        # Kill Switch controller (task 127): TWO functions from ONE governed-core module, each behind
        # its own Lambda FUNCTION URL with AuthType AWS_IAM. Lambda puts the IAM-verified caller into
        # requestContext.authorizer.iam.userArn for AWS_IAM URLs (AWS Lambda dev guide, "Invoking
        # function URLs"), so the actor recorded in the parameter + the WORM ledger is never
        # self-declared, and separation of duties on release is enforced on that identity. IAM SoD:
        # two managed policies (engage-only / disengage-only) grant lambda:InvokeFunctionUrl on ONE
        # function each - the runbook assigns them to different roles.
        self.kill_switch_fns = {}
        self.kill_switch_urls = {}
        self.kill_switch_policies = {}
        for mode in ("engage", "disengage"):
            f = fn(f"kill-switch-{mode}", "kill_switch_control",
                   env={"KILL_SWITCH_MODE": mode, "KILL_SWITCH_PARAM": ks_name})
            f.add_to_role_policy(iam.PolicyStatement(
                sid="WriteKillSwitch", actions=["ssm:PutParameter"],
                resources=[self.kill_switch_param.parameter_arn]))
            # state changes are COMMITTED / DENIED records in the BASE ledger + vault (platform scope)
            data.audit_table.grant(f, "dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:TransactWriteItems")
            data.worm_bucket.grant_put(f)
            url = f.add_function_url(auth_type=lambda_.FunctionUrlAuthType.AWS_IAM)
            pol = iam.ManagedPolicy(
                self, f"KillSwitch{mode.title()}Policy",
                managed_policy_name=f"{prefix}-killswitch-{mode}",
                description=f"Grants ONLY lambda:InvokeFunctionUrl on the {mode} function of the "
                            f"{prefix} Kill Switch (AWS_IAM function URL). Assign to a different "
                            f"role than the other mode (separation of duties).",
                # Lambda dev guide, "Control access to function URLs": a same-account principal needs BOTH
                # lambda:InvokeFunctionUrl AND lambda:InvokeFunction in its identity policy (found live on
                # ben-mt5: URL-only => 403 at the front door). lambda:InvokedViaFunctionUrl=true keeps
                # this grant usable ONLY through the URL (not a direct Invoke), so the IAM-verified caller
                # context is always present.
                statements=[iam.PolicyStatement(
                    sid=f"{mode.title()}KillSwitch",
                    actions=["lambda:InvokeFunctionUrl", "lambda:InvokeFunction"],
                    resources=[f.function_arn],
                    conditions={"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"},
                                "Bool": {"lambda:InvokedViaFunctionUrl": "true"}})])
            self.kill_switch_fns[mode], self.kill_switch_urls[mode], self.kill_switch_policies[mode] = f, url, pol
        # The gateway interceptor writes a DENIED record for every refused call into the ACTING
        # tenant's ledger + vault (mirror grants below in multi-tenant mode), base stores in silo mode.
        data.audit_table.grant(self.tenant_interceptor, "dynamodb:PutItem", "dynamodb:GetItem",
                               "dynamodb:TransactWriteItems")
        data.worm_bucket.grant_put(self.tenant_interceptor)
        # Budget meter grants (least privilege): the interceptor only READS the meter (check); the drafter
        # (server-side Bedrock call) READS + UPDATES it (commit) and publishes the Aegis/Budget metrics.
        # The Runtime's exec role is granted the same by lib/runtime/_obs_setup.sh (it is created by the
        # AgentCore toolkit, outside this app).
        self.budgets_table.grant(self.tenant_interceptor, "dynamodb:GetItem")
        self.budgets_table.grant(self.core, "dynamodb:GetItem", "dynamodb:UpdateItem")
        self.core.add_to_role_policy(iam.PolicyStatement(
            sid="BudgetMetrics", actions=["cloudwatch:PutMetricData"], resources=["*"],
            conditions={"StringEquals": {"cloudwatch:namespace": "Aegis/Budget"}}))
        # The drafter refuses on the WORKFLOW hop (no interceptor in front of a Step Functions task), so
        # its budget / kill-switch refusals must land as DENIED records too: the same append-only ledger
        # grant the interceptor has (Put + Get head + TransactWrite; no Update/Delete). Found on the
        # mt6 sweep: the first refusal logged `stored: false` (AccessDenied on GetItem) - fixed here.
        data.audit_table.grant(self.core, "dynamodb:PutItem", "dynamodb:GetItem",
                               "dynamodb:TransactWriteItems")
        data.worm_bucket.grant_put(self.core)

        # ── explicit least-privilege wiring ──────────────────────────────────
        # Signing secret: readable ONLY by the minter (mask_pii) + the verifiers (assess/causality/
        # duplicate/core/guards). openFDA provenance is signed by the lookup, so it reads it too.
        if self.signing_secret is not None:
            for f in (self.mask, self.assess, self.causality, self.duplicate,
                      self.core, self.guards, self.lookup, self.tenant_interceptor):   # interceptor SIGNS the tenant
                self.signing_secret.grant_read(f)
            # Hybrid multi-tenant (governed-core 1.6.0): EVERY Lambda that routes a store VERIFIES the
            # HMAC-signed tenant pair first, so every one of them is a verifier and needs the key
            # (ingest also SIGNS the pair the workflow carries). Found live 2026-09-02 on benefits (ben-mt2): the
            # audit writer, intake and the sign-off Lambdas had no read grant, so verification failed
            # and they refused fail-closed (TenantError) - correct behavior, missing grant.
            if multitenant:
                for f in (self.ingest, self.intake, self.write_audit, self.request_signoff,
                          self.signoff_register, self.finalize, self.approve_signoff):
                    if f is not None:
                        self.signing_secret.grant_read(f)
        # R3-2 case store: ingest WRITES raw content; intake + mask READ it (the only two consumers of
        # raw text). Nothing else touches raw content; only opaque refs cross Step Functions state.
        data.case_table.grant(self.ingest, "dynamodb:PutItem")
        data.case_table.grant(self.intake, "dynamodb:GetItem")
        data.case_table.grant(self.mask, "dynamodb:GetItem")
        data.pending_table.grant(self.signoff_register, "dynamodb:PutItem")
        data.pending_table.grant_read_write_data(self.finalize)
        if self.approve_signoff is not None:
            # approve path: read + consume the pending row, release the token, write DENIED/APPROVED
            # evidence. SendTaskSuccess is scoped to this deployment's controller by NAME (a
            # constructed ARN, not a cross-stack ref — workflow deploys after compute).
            data.pending_table.grant(self.approve_signoff, "dynamodb:GetItem", "dynamodb:UpdateItem")
            self.approve_signoff.add_to_role_policy(iam.PolicyStatement(
                actions=["states:SendTaskSuccess", "states:SendTaskFailure"],
                resources=[f"arn:aws:states:{self.region}:{self.account}:"
                           f"stateMachine:{prefix}-icsr-workflow"]))
            data.audit_table.grant(self.approve_signoff, "dynamodb:PutItem",
                                   "dynamodb:GetItem", "dynamodb:TransactWriteItems")
            data.worm_bucket.grant_put(self.approve_signoff)
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
        if guardrail_id:
            # Converse with guardrailConfig requires ApplyGuardrail on the specific guardrail.
            self.core.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock:ApplyGuardrail"],
                resources=[f"arn:aws:bedrock:{self.region}:{self.account}:guardrail/{guardrail_id}"]))
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

        # ── Hybrid multi-tenant (phase 107/109) ─────────────────────────────
        # The SAME least-privilege actions, mirrored onto EVERY tenant's own store inside this
        # deployment prefix (<prefix>-<tenant>-<logical>). Stores are routed per request by
        # tenancy.route_store from the interceptor-injected, signed tenant; grants never widen past
        # the prefix, and the audit tamper DENY is mirrored onto every tenant's ledger + vault.
        if multitenant:
            def _tbl(logical):
                base = f"arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-*-{logical}"
                return [base, f"{base}/index/*"]
            worm = [f"arn:aws:s3:::{prefix}-*-worm-*", f"arn:aws:s3:::{prefix}-*-worm-*/*"]

            def _mt(fn, resources, *actions):
                fn.add_to_role_policy(iam.PolicyStatement(actions=list(actions), resources=resources))
            RW = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan",
                  "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                  "dynamodb:BatchWriteItem", "dynamodb:ConditionCheckItem", "dynamodb:DescribeTable"]
            AUD = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:TransactWriteItems"]
            _mt(self.ingest, _tbl("case-store"), "dynamodb:PutItem")
            _mt(self.intake, _tbl("case-store"), "dynamodb:GetItem")
            _mt(self.mask, _tbl("case-store"), "dynamodb:GetItem")
            _mt(self.signoff_register, _tbl("pending-approvals"), "dynamodb:PutItem")
            _mt(self.finalize, _tbl("pending-approvals"), *RW)
            _mt(self.mask, _tbl("sanitized-artifacts"), "dynamodb:PutItem")
            _mt(self.core, _tbl("sanitized-artifacts"), "dynamodb:PutItem")   # narrative pass-by-ref
            for f in (self.core, self.guards, self.assess):
                _mt(f, _tbl("sanitized-artifacts"), "dynamodb:GetItem")
            for f in (self.write_audit, self.request_signoff, self.finalize, self.tenant_interceptor, self.core):
                _mt(f, _tbl("audit-ledger"), *AUD)
                _mt(f, worm, "s3:PutObject", "s3:Abort*")
            if self.approve_signoff is not None:
                _mt(self.approve_signoff, _tbl("pending-approvals"), "dynamodb:GetItem", "dynamodb:UpdateItem")
                _mt(self.approve_signoff, _tbl("audit-ledger"), *AUD)
                _mt(self.approve_signoff, worm, "s3:PutObject", "s3:Abort*")
            self.write_audit.add_to_role_policy(iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["dynamodb:DeleteItem", "dynamodb:UpdateItem",
                         "s3:DeleteObject", "s3:DeleteObjectVersion",
                         "s3:PutObjectRetention", "s3:PutObjectLegalHold",
                         "s3:BypassGovernanceRetention"],
                resources=_tbl("audit-ledger") + worm))

        for name, f in {
            "IngestArn": self.ingest, "IntakeArn": self.intake, "OpenfdaArn": self.lookup, "MaskArn": self.mask,
            "AssessArn": self.assess, "DuplicateArn": self.duplicate, "CausalityArn": self.causality,
            "CoreArn": self.core, "WriteAuditArn": self.write_audit,
            "RequestSignoffArn": self.request_signoff, "GuardsArn": self.guards,
        }.items():
            cdk.CfnOutput(self, name, value=f.function_arn)   # exact ARNs (P0-7)
        cdk.CfnOutput(self, "BudgetsTableName", value=self.budgets_table.table_name,
                      description="Per-tenant meter: <tenant>#<YYYY-MM>; PutItem cap_tokens / cap_usd_micro / behavior to override one tenant")
        cdk.CfnOutput(self, "KillSwitchParameter", value=ks_name)
        for mode in ("engage", "disengage"):
            cdk.CfnOutput(self, f"KillSwitch{mode.title()}Url", value=self.kill_switch_urls[mode].url,
                          description=f"POST {{reason}} with SigV4 (AWS_IAM) to {mode} the Kill Switch; GET = status")
            cdk.CfnOutput(self, f"KillSwitch{mode.title()}PolicyArn",
                          value=self.kill_switch_policies[mode].managed_policy_arn)
        if self.approve_signoff is not None:
            cdk.CfnOutput(self, "ApproveSignoffArn", value=self.approve_signoff.function_arn,
                          description="The ONLY working approve path: verifies the approver's Cognito "
                                      "access token, enforces SoD, consumes the single-use approval.")
