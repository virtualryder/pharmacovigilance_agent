"""The CDK stacks synthesize and carry the governance controls (P0-2/P0-5/P0-6/P0-7/P0-12 + Gate-B).

Uses aws_cdk.assertions (pure Python; no CDK CLI, no AWS). Skipped automatically when aws-cdk-lib is
not installed (CI installs it)."""
import json
import pathlib
import sys

import pytest

aws_cdk = pytest.importorskip("aws_cdk")
from aws_cdk.assertions import Template, Match  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cdk"))

from app import stage_lambda_bundle  # noqa: E402
from pv_stacks.data_stack import DataStack  # noqa: E402
from pv_stacks.compute_stack import ComputeStack  # noqa: E402
from pv_stacks.workflow_stack import WorkflowStack  # noqa: E402
from pv_stacks.identity_stack import IdentityStack  # noqa: E402


def _stacks(profile="sandbox-demo", kms="aws-managed"):
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "d", prefix="pv-test", retention_profile=profile, kms_mode=kms)
    compute = ComputeStack(app, "c", prefix="pv-test", asset_dir=asset, data=data)
    workflow = WorkflowStack(app, "w", prefix="pv-test", compute=compute, data=data)
    identity = IdentityStack(app, "i", prefix="pv-test")
    return data, compute, workflow, identity


DATA, COMPUTE, WORKFLOW, IDENTITY = _stacks()
T_DATA, T_COMPUTE = Template.from_stack(DATA), Template.from_stack(COMPUTE)
T_WORKFLOW, T_IDENTITY = Template.from_stack(WORKFLOW), Template.from_stack(IDENTITY)


# ── data: retention profiles (P0-12) + sanitized store (P0-1) ────────────────

def test_worm_bucket_object_lock_default_profile():
    T_DATA.has_resource_properties("AWS::S3::Bucket", Match.object_like({
        "ObjectLockEnabled": True,
        "ObjectLockConfiguration": Match.object_like({
            "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}}}),
    }))


def test_production_profile_is_compliance_mode():
    d, *_ = _stacks(profile="production-reference")
    Template.from_stack(d).has_resource_properties("AWS::S3::Bucket", Match.object_like({
        "ObjectLockConfiguration": Match.object_like({
            "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 2555}}}),
    }))


def test_unknown_profile_refused():
    with pytest.raises(ValueError):
        _stacks(profile="whatever")


def test_sanitized_artifacts_table_with_ttl():
    T_DATA.has_resource_properties("AWS::DynamoDB::Table", Match.object_like({
        "TableName": "pv-test-sanitized-artifacts",
        "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
    }))


def test_audit_ledger_retained_with_pitr():
    T_DATA.has_resource("AWS::DynamoDB::Table", Match.object_like({
        "DeletionPolicy": "Retain",
        "Properties": Match.object_like({
            "TableName": "pv-test-audit-ledger",
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True}})}))


# ── compute: explicit IAM (P0-5) + tamper deny + exact-ARN outputs (P0-7) ────

def test_audit_writer_has_explicit_tamper_deny():
    tpl = json.dumps(T_COMPUTE.to_json())
    assert "s3:BypassGovernanceRetention" in tpl and '"Effect": "Deny"' in tpl.replace("'", '"')


def test_exact_arn_outputs_exist():
    outs = T_COMPUTE.to_json().get("Outputs", {})
    for k in ("MaskArn", "AssessArn", "WriteAuditArn", "GuardsArn", "OpenfdaArn", "DuplicateArn"):
        assert k in outs, f"exact-ARN output {k} missing (P0-7)"


# ── compute: single signing secret is a Secrets Manager resource, no plaintext ─

def test_signing_secret_provisioned_and_no_plaintext():
    tpl = T_COMPUTE.to_json()
    types = [r["Type"] for r in tpl.get("Resources", {}).values()]
    assert types.count("AWS::SecretsManager::Secret") >= 1
    s = json.dumps(tpl)
    assert "PROVENANCE_SECRET_ARN" in s
    assert '"PROVENANCE_SECRET"' not in s, "plaintext signing secret must not appear in the template"


# ── workflow: deterministic controller shape + DuplicateHold (P0-2) ──────────

def _controller_definition():
    tpl = T_WORKFLOW.to_json()
    for r in tpl["Resources"].values():
        if r["Type"] == "AWS::StepFunctions::StateMachine":
            parts = r["Properties"]["DefinitionString"]["Fn::Join"][1]
            return json.loads("".join(p if isinstance(p, str) else "ARN" for p in parts))
    raise AssertionError("no state machine in workflow stack")


def test_controller_pipeline_order_and_fail_closed_choices():
    doc = _controller_definition()
    state, visited = doc["StartAt"], []
    while state and len(visited) < 40:
        visited.append(state)
        st = doc["States"][state]
        state = st["Choices"][0]["Next"] if st["Type"] == "Choice" else st.get("Next")
    expected = ["Extract", "GuardExtracted", "ExtractedOk",
                "LookupBackground", "GuardBackground", "BackgroundOk",
                "MaskPii", "GuardDeidentified", "DeidentifiedOk",
                "AssessSeriousness", "GuardRulesExecuted", "RulesOk",
                "DetectDuplicate", "GuardDuplicate", "NotDuplicate",
                "DraftNarrative", "DraftOk", "AuditIntent", "HumanSignoff", "Finalize", "FinalizeOk", "Committed"]
    assert visited == expected, f"happy path deviates from the regulated sequence: {visited}"
    # G1 / G2: a guardrail-blocked draft and a refused finalize both route to ManualReview, never onward
    for choice in ("DraftOk", "FinalizeOk"):
        assert doc["States"][choice]["Default"] == "ManualReview"
    # pre-hold guard Choices fail closed to ManualReview
    for choice in ("ExtractedOk", "BackgroundOk", "DeidentifiedOk", "RulesOk"):
        assert doc["States"][choice]["Default"] == "ManualReview"
    # a detected duplicate routes to the terminal DuplicateHold work queue (never onward)
    assert doc["States"]["NotDuplicate"]["Default"] == "DuplicateHold"
    assert doc["States"]["DuplicateHold"]["Type"] == "Succeed"
    # the human gate is a real waitForTaskToken pause
    assert "waitForTaskToken" in doc["States"]["HumanSignoff"]["Resource"]


def test_masking_precedes_assessment_and_draft():
    """PHI de-identification must happen before the seriousness assessment and the narrative draft."""
    doc = _controller_definition()
    order = []
    state, seen = doc["StartAt"], 0
    while state and seen < 40:
        order.append(state)
        st = doc["States"][state]
        state = st["Choices"][0]["Next"] if st["Type"] == "Choice" else st.get("Next")
        seen += 1
    assert order.index("MaskPii") < order.index("AssessSeriousness") < order.index("DraftNarrative")


def test_workflow_state_carries_no_raw_or_masked_content():
    """R3-2: execution input is {case_id, requester, case_ref, ...}; intake+mask receive case_ref; the
    assessor/drafter receive only the signed sanitized_ref (load text server-side). No raw `source` and
    no `masked_case` may appear in the state machine definition."""
    asl = json.dumps(T_WORKFLOW.to_json())
    assert "$.source" not in asl, "raw source must never enter Step Functions state"
    assert "masked_case" not in asl, "masked content must not cross state (server-side store only)"
    assert "case_ref" in asl
    tpl = json.dumps(T_COMPUTE.to_json())
    assert "ingest-case" in tpl                      # the one door for raw content
    assert '"CASE_TABLE"' in tpl                     # encrypted pass-by-reference store wired


# ── identity: no users, no passwords (P0-6) ──────────────────────────────────

def test_identity_creates_no_users_and_no_passwords():
    tpl = T_IDENTITY.to_json()
    types = [r["Type"] for r in tpl.get("Resources", {}).values()]
    assert "AWS::Cognito::UserPoolUser" not in types
    assert "ChangeMe" not in json.dumps(tpl)


def test_no_default_password_anywhere_in_any_template():
    for t in (T_DATA, T_COMPUTE, T_WORKFLOW, T_IDENTITY):
        assert "ChangeMe" not in json.dumps(t.to_json())


# ── Gate-B B1: private networking + locked egress (openFDA only) ─────────────

def test_network_stack_locked_egress_and_vpc_lambdas():
    from pv_stacks.network_stack import NetworkStack, ALLOWED_DOMAINS
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    net = NetworkStack(app, "nn", prefix="pv-net")
    data = DataStack(app, "nd", prefix="pv-net", retention_profile="pilot")
    compute = ComputeStack(app, "nc", prefix="pv-net", asset_dir=asset, data=data, network=net)

    nt = Template.from_stack(net).to_json()
    blob = json.dumps(nt)
    types = [r["Type"] for r in nt["Resources"].values()]
    assert "AWS::NetworkFirewall::Firewall" in types
    assert "AWS::NetworkFirewall::RuleGroup" in types
    assert ".api.fda.gov" in blob and ALLOWED_DOMAINS == [".api.fda.gov"]
    assert '"GeneratedRulesType": "ALLOWLIST"' in blob
    assert types.count("AWS::EC2::VPCEndpoint") >= 9

    ct = Template.from_stack(compute).to_json()
    fns = [r for r in ct["Resources"].values() if r["Type"] == "AWS::Lambda::Function"]
    assert fns and all("VpcConfig" in f["Properties"] for f in fns)


def test_tenant_pinned_into_every_function_env():
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "td", prefix="pv-ten", retention_profile="pilot")
    compute = ComputeStack(app, "tc", prefix="pv-ten", asset_dir=asset, data=data, tenant="pv-example-sponsor")
    fns = [r for r in Template.from_stack(compute).to_json()["Resources"].values()
           if r["Type"] == "AWS::Lambda::Function"]
    assert fns and all(
        f["Properties"]["Environment"]["Variables"].get("TENANT_ID") == "pv-example-sponsor" for f in fns)


def test_default_mode_lambdas_have_no_vpc():
    fns = [r for r in T_COMPUTE.to_json()["Resources"].values() if r["Type"] == "AWS::Lambda::Function"]
    assert fns and all("VpcConfig" not in f["Properties"] for f in fns)


# ── Gate-B B3: pilot identity — REQUIRED software MFA, threat protection ─────

def test_pilot_identity_requires_software_mfa_and_threat_protection():
    app = aws_cdk.App()
    i = IdentityStack(app, "ip", prefix="pv-idp", identity_mode="pilot")
    tpl = Template.from_stack(i).to_json()
    pools = [r for r in tpl["Resources"].values() if r["Type"] == "AWS::Cognito::UserPool"]
    assert len(pools) == 1
    p = pools[0]["Properties"]
    assert p["MfaConfiguration"] == "ON"
    assert p["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]
    assert p["UserPoolAddOns"]["AdvancedSecurityMode"] == "ENFORCED"
    assert p.get("AdminCreateUserConfig", {}).get("AllowAdminCreateUserOnly") is True
    types = [r["Type"] for r in tpl["Resources"].values()]
    assert "AWS::Cognito::UserPoolUser" not in types


def test_unknown_identity_mode_refused():
    with pytest.raises(ValueError):
        IdentityStack(aws_cdk.App(), "ix", prefix="pv-x", identity_mode="prod")


# ── Gate-B: customer-managed KMS reaches secrets, env, logs, SNS ─────────────

def test_customer_managed_kms_covers_secrets_env_logs_and_sns():
    from pv_stacks.observability_stack import ObservabilityStack
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "kd", prefix="pv-kms", retention_profile="pilot", kms_mode="customer-managed")
    compute = ComputeStack(app, "kc", prefix="pv-kms", asset_dir=asset, data=data)
    workflow = WorkflowStack(app, "kw", prefix="pv-kms", compute=compute, data=data)
    obs = ObservabilityStack(app, "ko", prefix="pv-kms", compute=compute, workflow=workflow, data=data)

    d = json.dumps(Template.from_stack(data).to_json())
    assert '"AWS::KMS::Key"' in d and '"EnableKeyRotation": true' in d

    ct = Template.from_stack(compute).to_json()
    res = ct.get("Resources", {})
    for lid, r in res.items():
        if r["Type"] == "AWS::SecretsManager::Secret":
            assert "KmsKeyId" in r["Properties"], f"{lid} must use the customer-managed key"
    fns = [r for r in res.values() if r["Type"] == "AWS::Lambda::Function"]
    lgs = [r for r in res.values() if r["Type"] == "AWS::Logs::LogGroup"]
    assert fns and len(lgs) >= len(fns)
    for r in fns:
        assert "KmsKeyArn" in r["Properties"]
    for r in lgs:
        assert "KmsKeyId" in r["Properties"]
    ot = Template.from_stack(obs).to_json()
    topics = [r for r in ot.get("Resources", {}).values() if r["Type"] == "AWS::SNS::Topic"]
    assert topics and all("KmsMasterKeyId" in t["Properties"] for t in topics)


def test_aws_managed_mode_has_no_cmk():
    s = json.dumps(T_COMPUTE.to_json())
    assert '"AWS::KMS::Key"' not in s


# ── observability: alarms + dashboard exist and page via SNS ─────────────────

def test_observability_stack_alarms_and_dashboard():
    from pv_stacks.observability_stack import ObservabilityStack
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "od", prefix="pv-obs", retention_profile="sandbox-demo")
    compute = ComputeStack(app, "oc", prefix="pv-obs", asset_dir=asset, data=data)
    workflow = WorkflowStack(app, "ow", prefix="pv-obs", compute=compute, data=data)
    obs = ObservabilityStack(app, "oo", prefix="pv-obs", compute=compute, workflow=workflow)
    tpl = Template.from_stack(obs)
    types = [r["Type"] for r in tpl.to_json().get("Resources", {}).values()]
    assert types.count("AWS::CloudWatch::Alarm") >= 4
    assert "AWS::CloudWatch::Dashboard" in types
    assert "AWS::SNS::Topic" in types
    s = json.dumps(tpl.to_json())
    assert s.count("AlarmActions") >= 4


# ── gateway: AgentCore/Cedar attachment covers the PV tool set + ENFORCE ─────

def _gateway_stack():
    from pv_stacks.gateway_stack import GatewayStack
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "gd", prefix="pv-gw", retention_profile="sandbox-demo")
    compute = ComputeStack(app, "gc", prefix="pv-gw", asset_dir=asset, data=data)
    identity = IdentityStack(app, "gi", prefix="pv-gw")
    return Template.from_stack(GatewayStack(app, "gg", prefix="pv-gw", compute=compute, identity=identity))


T_GATEWAY = _gateway_stack()


def test_attachment_covers_every_manifest_tool_and_enforce():
    tpl = T_GATEWAY.to_json()
    props = next(r["Properties"] for r in tpl["Resources"].values()
                 if r["Type"] == "AWS::CloudFormation::CustomResource")

    def _tokjson(v):
        if isinstance(v, dict) and "Fn::Join" in v:
            return json.loads("".join(x if isinstance(x, str) else "ARN" for x in v["Fn::Join"][1]))
        return json.loads(v)

    targets = _tokjson(props["TargetsJson"])
    names = {t["name"] for t in targets}
    assert names == {"intake-icsr", "openfda-lookup", "mask-pii", "assess-seriousness",
                     "detect-duplicate", "record-causality", "pv-core", "write-audit", "request-signoff"}
    all_tools = [tool["name"] for t in targets for tool in t["tools"]]
    assert "finalize_submission" in all_tools and "request_signoff" in all_tools and "commit_causality" in all_tools
    for t in targets:
        for tool in t["tools"]:
            assert "access_token" not in tool["inputSchema"]["properties"]
    policies = _tokjson(props["PoliciesJson"])
    assert {p["name"] for p in policies} == {
        "pv_reviewer_permit", "mask_before_assess", "mask_before_causality",
        "mask_before_draft", "no_self_submit", "no_self_causality_commit"}
    assert all("__GATEWAY_ARN__" in p["definition"] for p in policies if p["name"].startswith("no_self"))
    assert props["Enforcement"] == "ENFORCE"


def test_gateway_role_invokes_only_exact_lambda_arns():
    s = json.dumps(T_GATEWAY.to_json())
    assert "lambda:InvokeFunction" in s
    assert "starts_with" not in s and ":function:*" not in s


# ── governed-core 1.9.0 parity (ported from benefits 2026-09-03): multi-tenant, kill switch, budget ──
def test_data_stack_per_tenant_naming_is_physically_separate():
    """Hybrid multi-tenant: each tenant's DataStack yields its OWN tenant-scoped tables (physical
    separation, not a shared table with a tenant key). Silo (no tenant) keeps the base names."""
    app = aws_cdk.App()
    # create every stack BEFORE any synth (Template.from_stack synthesizes the app)
    a = DataStack(app, "da", prefix="pv-test", tenant="sp-oakland")
    b = DataStack(app, "db", prefix="pv-test", tenant="sp-alameda")
    silo = DataStack(app, "ds", prefix="pv-test")
    Template.from_stack(a).has_resource_properties("AWS::DynamoDB::Table",
        Match.object_like({"TableName": "pv-test-sp-oakland-audit-ledger"}))
    Template.from_stack(b).has_resource_properties("AWS::DynamoDB::Table",
        Match.object_like({"TableName": "pv-test-sp-alameda-audit-ledger"}))
    Template.from_stack(silo).has_resource_properties("AWS::DynamoDB::Table",
        Match.object_like({"TableName": "pv-test-audit-ledger"}))
    # per-tenant WORM vault gets a predictable, tenant-scoped name (so IAM can scope to <prefix>-*-worm-*)
    assert "pv-test-sp-oakland-worm-" in json.dumps(Template.from_stack(a).to_json())


def test_tenant_interceptor_wired_into_compute_and_gateway():
    """Phase 107 (hybrid multi-tenant): the gateway REQUEST interceptor Lambda exists with MULTITENANT set,
    the gateway attachment carries its ARN (passRequestHeaders -> it sees the validated JWT), the gateway
    role may invoke it, and every tool schema carries the reserved HMAC-signed tenant fields."""
    from pv_stacks.gateway_stack import GatewayStack
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "d2", prefix="pv-mt", retention_profile="sandbox-demo")
    compute = ComputeStack(app, "c2", prefix="pv-mt", asset_dir=asset, data=data, multitenant=True)
    identity = IdentityStack(app, "i2", prefix="pv-mt", tenants=("sp-oakland", "sp-alameda"))
    gateway = GatewayStack(app, "g2", prefix="pv-mt", compute=compute, identity=identity, multitenant=True)
    gateway_silo = GatewayStack(app, "g3", prefix="pv-mt", compute=compute, identity=identity)  # before synth
    tc, tg = Template.from_stack(compute), Template.from_stack(gateway)
    tc.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "FunctionName": "pv-mt-tenant-interceptor",
        "Handler": "tenant_interceptor.handler",
        "Environment": {"Variables": Match.object_like({"MULTITENANT": "1"})},
    }))
    gw = json.dumps(tg.to_json())
    assert "InterceptorLambdaArn" in gw, "gateway attachment does not carry the interceptor ARN"
    assert "__aegis_tenant" in gw and "__aegis_tenant_sig" in gw, \
        "tool schemas are missing the reserved signed-tenant fields"
    # per-tenant identity: one tenant_<id> Cognito group per tenant (membership is what the access token carries)
    ti = Template.from_stack(identity)
    ti.has_resource_properties("AWS::Cognito::UserPoolGroup", Match.object_like({"GroupName": "tenant_sp-oakland"}))
    ti.has_resource_properties("AWS::Cognito::UserPoolGroup", Match.object_like({"GroupName": "tenant_sp-alameda"}))
    # phase 108: require_tenant attaches ONLY in multi-tenant deployments (silo would forbid everything)
    assert "require_tenant" in gw and "custom:tenant" in gw
    assert "require_tenant" not in json.dumps(Template.from_stack(gateway_silo).to_json())
    # multi-tenant mirror grants: the shared Lambdas reach EVERY tenant's store, scoped to the prefix
    cj = json.dumps(tc.to_json())
    assert "table/pv-mt-*-case-store" in cj and "table/pv-mt-*-audit-ledger" in cj
    assert "arn:aws:s3:::pv-mt-*-worm-*" in cj
    # (the gateway role's invoke grant and the attachment both reference the interceptor ARN via the
    #  same cross-stack export token, so InterceptorLambdaArn being present proves the wiring)


def test_multitenant_audit_routing_wired_through_compute_and_workflow():
    """governed-core 1.6.0: per-tenant ledger/WORM/approvals routing. Compute hands the evidence writer
    the exact per-tenant vault template; the workflow threads the HMAC-signed tenant pair into EVERY
    Lambda payload (the Step Functions hop has no interceptor). Silo templates carry neither."""
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "d4", prefix="pv-mt", retention_profile="sandbox-demo")
    compute = ComputeStack(app, "c4", prefix="pv-mt", asset_dir=asset, data=data, multitenant=True)
    workflow = WorkflowStack(app, "w4", prefix="pv-mt", compute=compute, data=data, multitenant=True)
    tc, tw = Template.from_stack(compute), Template.from_stack(workflow)
    tc.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "FunctionName": "pv-mt-write-audit",
        "Environment": {"Variables": Match.object_like({
            "MULTITENANT": "1",
            "WORM_BUCKET_TEMPLATE": Match.object_like({"Fn::Join": Match.any_value()})})},
    }))
    assert "pv-mt-{tenant}-worm-" in json.dumps(tc.to_json())
    # every tenant-verifying Lambda can read the signing secret (found missing live on pv-mt2):
    # a role policy granting secretsmanager:GetSecretValue on the SigningSecret for each of these
    roles_with_secret = set()
    for name, res in tc.to_json()["Resources"].items():
        if res["Type"] != "AWS::IAM::Policy":
            continue
        doc = json.dumps(res["Properties"]["PolicyDocument"])
        if "secretsmanager:GetSecretValue" in doc and "SigningSecret" in doc:
            roles_with_secret.update(json.dumps(res["Properties"]["Roles"]).split('"Ref": "')[1:])
    roles_with_secret = {r.split('"')[0] for r in roles_with_secret}
    assert len(roles_with_secret) >= 13, roles_with_secret     # 7 original readers + 6 multi-tenant verifiers
    fn_roles = {res["Properties"]["FunctionName"]: json.dumps(res["Properties"]["Role"])
                for res in tc.to_json()["Resources"].values() if res["Type"] == "AWS::Lambda::Function"}
    for fname in ("pv-mt-ingest-case", "pv-mt-intake-icsr", "pv-mt-write-audit",
                  "pv-mt-request-signoff", "pv-mt-signoff-register", "pv-mt-finalize", "pv-mt-mask-pii"):
        assert any(r in fn_roles[fname] for r in roles_with_secret), f"{fname} cannot read the signing secret"
    wj = json.dumps(tw.to_json())
    # every LambdaInvoke payload (incl. the waitForTaskToken sign-off register) carries the signed pair
    # 14 Lambda-backed states: Extract, LookupBackground, MaskPii, AssessSeriousness, DetectDuplicate, 5 guards,
    # DraftNarrative, AuditIntent, HumanSignoff (waitForTaskToken), Finalize -> each carries the pair exactly once
    assert wj.count("__aegis_tenant.$") == 14 and wj.count("__aegis_tenant_sig.$") == 14, wj.count("__aegis_tenant.$")
    silo = json.dumps(T_WORKFLOW.to_json()) + json.dumps(T_COMPUTE.to_json())
    assert "__aegis_tenant" not in silo and "WORM_BUCKET_TEMPLATE" not in silo


def test_kill_switch_wired_into_every_lambda_and_the_controller_has_sod(monkeypatch):
    """Task 127 (governed-core 1.8.0): ONE SSM parameter per deployment under the gateway-discovery root;
    EVERY governed Lambda (incl. the interceptor and the controller) reads it (KILL_SWITCH_PARAMS + an
    ssm:GetParameter grant scoped to that parameter); ONLY the two controller functions may write it;
    the controller is two functions (engage / disengage) behind AWS_IAM function URLs with one
    managed policy each (IAM separation of duties); the interceptor may write DENIED records to the
    ledger + vault; -c global_kill_switch adds the platform-wide parameter to every reader."""
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "d4", prefix="pv-ks", retention_profile="sandbox-demo")
    compute = ComputeStack(app, "c4", prefix="pv-ks", asset_dir=asset, data=data, multitenant=True,
                           global_kill_switch="/aegis/kill-switch")
    t = Template.from_stack(compute)
    t.has_resource_properties("AWS::SSM::Parameter", Match.object_like({
        "Name": "/pv-ks-pharmacovigilance/kill-switch", "Type": "String",
        "Value": '{"engaged": false, "actor": "", "reason": "", "at": 0}'}))
    fns = t.find_resources("AWS::Lambda::Function")
    names = {v["Properties"]["FunctionName"] for v in fns.values()}
    assert {"pv-ks-kill-switch-engage", "pv-ks-kill-switch-disengage", "pv-ks-tenant-interceptor"} <= names
    for v in fns.values():
        env = v["Properties"]["Environment"]["Variables"]
        assert env["KILL_SWITCH_PARAMS"] == "/pv-ks-pharmacovigilance/kill-switch,/aegis/kill-switch", v["Properties"]["FunctionName"]
        assert env["KILL_SWITCH_TTL_SECONDS"] == "15"
    for mode in ("engage", "disengage"):
        t.has_resource_properties("AWS::Lambda::Function", Match.object_like({
            "FunctionName": f"pv-ks-kill-switch-{mode}", "Handler": "kill_switch_control.handler",
            "Environment": {"Variables": Match.object_like({"KILL_SWITCH_MODE": mode,
                                                            "KILL_SWITCH_PARAM": "/pv-ks-pharmacovigilance/kill-switch"})}}))
        t.has_resource_properties("AWS::IAM::ManagedPolicy", Match.object_like({
            "ManagedPolicyName": f"pv-ks-killswitch-{mode}",
            "PolicyDocument": Match.object_like({"Statement": [Match.object_like({
                "Action": ["lambda:InvokeFunctionUrl", "lambda:InvokeFunction"],   # both: Lambda dev guide, urls-auth
                "Condition": {"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"},
                              "Bool": {"lambda:InvokedViaFunctionUrl": "true"}}})]})}))
    urls = t.find_resources("AWS::Lambda::Url")
    assert len(urls) == 2 and all(u["Properties"]["AuthType"] == "AWS_IAM" for u in urls.values())
    # ssm:PutParameter on the switch appears in EXACTLY the two controller roles; GetParameter everywhere
    pols = json.dumps(t.find_resources("AWS::IAM::Policy"))
    assert pols.count('"ssm:PutParameter"') == 2
    assert pols.count("ReadKillSwitch") == len(fns)
    # the interceptor can write the DENIED evidence: base ledger transact + vault put, mirrored per tenant
    ipol = [p for p in t.find_resources("AWS::IAM::Policy").values()
            if any("TenantInterceptor" in r.get("Ref", "") for r in p["Properties"]["Roles"])]
    ij = json.dumps(ipol)
    assert "dynamodb:TransactWriteItems" in ij and "s3:PutObject" in ij and "table/pv-ks-*-audit-ledger" in ij
    outs = t.to_json()["Outputs"]
    assert {"KillSwitchParameter", "KillSwitchEngageUrl", "KillSwitchDisengageUrl",
            "KillSwitchEngagePolicyArn", "KillSwitchDisengagePolicyArn"} <= set(outs)
    # silo / no global switch: exactly the deployment's own parameter
    app2 = aws_cdk.App()                                       # a fresh app: the first one is already synthesized
    data2 = DataStack(app2, "d5", prefix="pv-ks2", retention_profile="sandbox-demo")
    c2 = Template.from_stack(ComputeStack(app2, "c5", prefix="pv-ks2", asset_dir=asset, data=data2))
    for v in c2.find_resources("AWS::Lambda::Function").values():
        assert v["Properties"]["Environment"]["Variables"]["KILL_SWITCH_PARAMS"] == "/pv-ks2-pharmacovigilance/kill-switch"


def test_budget_meter_alarms_and_usd_ceiling_are_wired():
    """Task 128 (governed-core 1.9.0): ONE <prefix>-budgets table; every governed Lambda carries the meter
    env (caps from the manifest budget: block, pinned price table with its version, deployment dimension);
    the interceptor may only READ the meter, the drafter may UPDATE it + publish Aegis/Budget metrics;
    per-tenant 60/85/100 % alarms exist; with -c budget_usd the AWS Budgets USD ceiling exists with an
    APPLY_IAM_POLICY action (deny bedrock:* on the drafter role, automatic approval) + the budget-breach
    function subscribed to the ops topic with permission to invoke the kill-switch engage URL."""
    from pv_stacks.observability_stack import ObservabilityStack
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    prices = json.dumps({"price_version": "test-2026-09-03", "models": {"anthropic.claude-sonnet-4-5": {"input_per_m": 3, "output_per_m": 15}}})
    data = DataStack(app, "d6", prefix="pv-bg", retention_profile="sandbox-demo")
    compute = ComputeStack(app, "c6", prefix="pv-bg", asset_dir=asset, data=data, multitenant=True,
                           budget={"monthly_token_cap": 5000000, "cap_behavior": "hard", "monthly_usd": 25.5, "prices_json": prices})
    workflow = WorkflowStack(app, "w6", prefix="pv-bg", compute=compute, data=data, multitenant=True)
    obs = ObservabilityStack(app, "o6", prefix="pv-bg", compute=compute, workflow=workflow, data=data,
                             tenants=("sp-a", "sp-b"), budget_usd=25.5, runtime_role_name="AmazonBedrockAgentCoreSDKRuntime-x")
    tc, to = Template.from_stack(compute), Template.from_stack(obs)
    tc.has_resource_properties("AWS::DynamoDB::Table", Match.object_like({
        "TableName": "pv-bg-budgets", "KeySchema": [{"AttributeName": "budget_key", "KeyType": "HASH"}],
        "BillingMode": "PAY_PER_REQUEST"}))
    for v in tc.find_resources("AWS::Lambda::Function").values():
        env = v["Properties"]["Environment"]["Variables"]
        assert env["BUDGET_CAP_TOKENS"] == "5000000" and env["BUDGET_CAP_USD_MICRO"] == "25500000", v["Properties"]["FunctionName"]
        assert env["BUDGET_BEHAVIOR"] == "hard" and env["BUDGET_DEPLOYMENT"] == "pv-bg" and env["BUDGET_RESERVE_TOKENS"] == "4000"
        assert json.loads(env["BUDGET_PRICES_JSON"])["price_version"] == "test-2026-09-03"
    pols = tc.find_resources("AWS::IAM::Policy")
    def _role_pols(marker):
        return json.dumps([p for p in pols.values() if any(marker in r.get("Ref", "") for r in p["Properties"]["Roles"])])
    ij, cj = _role_pols("TenantInterceptor"), _role_pols("CoreTools")
    assert "pv-bg-budgets" not in ij or "dynamodb:UpdateItem" not in ij.split("Budgets")[0]   # interceptor: read-only meter
    assert "cloudwatch:PutMetricData" in cj and "Aegis/Budget" in cj
    # the drafter refuses on the workflow hop -> its DENIED records need the append-only ledger grant
    # (mirrored per tenant), never Update/Delete on a ledger (mt6 sweep finding, 2026-09-03)
    assert "dynamodb:TransactWriteItems" in cj and "table/pv-bg-*-audit-ledger" in cj and "s3:PutObject" in cj
    for stmt in (st for p in pols.values() if any("CoreTools" in r.get("Ref", "") for r in p["Properties"]["Roles"])
                 for st in p["Properties"]["PolicyDocument"]["Statement"]):
        res = json.dumps(stmt.get("Resource"))
        if "audit-ledger" in res or "AuditLedger" in res:
            acts = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
            assert not {"dynamodb:UpdateItem", "dynamodb:DeleteItem"} & set(acts), stmt
    # alarms: 3 thresholds x 2 metrics x 2 tenants
    alarms = to.find_resources("AWS::CloudWatch::Alarm")
    names = {a["Properties"].get("AlarmName", "") for a in alarms.values()}
    assert {f"pv-bg-budget-{t}-{m}-{p}" for t in ("sp-a", "sp-b") for m in ("TokensUsedPct", "UsdUsedPct") for p in (60, 85, 100)} <= names
    # AWS Budgets USD ceiling + action + breach function
    to.has_resource_properties("AWS::Budgets::Budget", Match.object_like({"Budget": Match.object_like({
        "BudgetName": "pv-bg-bedrock-usd-ceiling", "BudgetType": "COST", "TimeUnit": "MONTHLY",
        "BudgetLimit": {"Amount": 25.5, "Unit": "USD"}, "CostFilters": {"Service": ["Amazon Bedrock"]}})}))
    to.has_resource_properties("AWS::Budgets::BudgetsAction", Match.object_like({
        "ActionType": "APPLY_IAM_POLICY", "ApprovalModel": "AUTOMATIC", "NotificationType": "ACTUAL",
        "ActionThreshold": {"Type": "PERCENTAGE", "Value": 100}}))
    oj = json.dumps(to.to_json())
    assert "AmazonBedrockAgentCoreSDKRuntime-x" in oj and "bedrock:InvokeModel" in oj and '"Effect": "Deny"' in oj.replace("\\", "")
    to.has_resource_properties("AWS::Lambda::Function", Match.object_like({"FunctionName": "pv-bg-budget-breach"}))
    to.has_resource_properties("AWS::SNS::Subscription", Match.object_like({"Protocol": "lambda"}))
    assert "lambda:InvokeFunctionUrl" in oj
    # without -c budget_usd: no Budgets resources, token alarms only
    app2 = aws_cdk.App()
    d2 = DataStack(app2, "d7", prefix="pv-bg2", retention_profile="sandbox-demo")
    c2 = ComputeStack(app2, "c7", prefix="pv-bg2", asset_dir=asset, data=d2, budget={"monthly_token_cap": 10, "prices_json": prices})
    w2 = WorkflowStack(app2, "w7", prefix="pv-bg2", compute=c2, data=d2)
    o2 = Template.from_stack(ObservabilityStack(app2, "o7", prefix="pv-bg2", compute=c2, workflow=w2, data=d2))
    assert not o2.find_resources("AWS::Budgets::Budget")
    assert "pv-bg2-budget-default-TokensUsedPct-100" in {a["Properties"].get("AlarmName", "") for a in o2.find_resources("AWS::CloudWatch::Alarm").values()}


# -- RT-3 (port from benefits, 2026-09-06): runtime execution role as IaC ----------------------------

def test_runtime_execution_role_is_iac_least_privilege_with_mandatory_guardrail():
    """The AgentCore runtime must NOT run on the CLI-generated role (AWS: development/testing only). The
    compute stack exports an IaC execution role: the documented runtime policy scoped to this deployment +
    region + runtime name, the runtime's governance needs (SSM, budget meter, ApplyGuardrail), a
    SourceAccount/SourceArn-conditioned trust and - with a guardrail - the mandatory-guardrail condition."""
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "dg", prefix="pv-test", retention_profile="sandbox-demo", kms_mode="aws-managed")
    compute = ComputeStack(app, "cg", prefix="pv-test", asset_dir=asset, data=data, guardrail_id="gr-abc123")
    t = Template.from_stack(compute)
    roles = {k: v for k, v in t.find_resources("AWS::IAM::Role").items()
             if v["Properties"].get("RoleName") == "pv-test-agentcore-runtime"}
    assert len(roles) == 1, "IaC runtime execution role missing"
    role = next(iter(roles.values()))["Properties"]
    trust = json.dumps(role["AssumeRolePolicyDocument"])
    assert "bedrock-agentcore.amazonaws.com" in trust and "aws:SourceAccount" in trust and "aws:SourceArn" in trust
    pols = json.dumps([v for v in t.find_resources("AWS::IAM::Policy").values()
                       if "RuntimeExecutionRole" in json.dumps(v["Properties"].get("Roles"))])
    for needle in ("ecr:BatchGetImage", "ecr:GetAuthorizationToken", "/aws/bedrock-agentcore/runtimes/",
                   "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "workload-identity/pv_runtime_agent-*",
                   "xray:PutTraceSegments", "Aegis/Budget", "ssm:GetParameter", "-pharmacovigilance/*",
                   "dynamodb:UpdateItem", "bedrock:ApplyGuardrail", "bedrock:InvokeModelWithResponseStream",
                   "bedrock:GuardrailIdentifier"):
        assert needle in pols, f"runtime execution role policy is missing {needle}"
    assert '"bedrock-agentcore:*"' not in pols and '"Action": "*"' not in pols
    t.has_output("RuntimeExecutionRoleArn", {})
    # the drafter carries the same mandatory-guardrail condition
    core = json.dumps([v for v in t.find_resources("AWS::IAM::Policy").values()
                       if "CoreTools" in json.dumps(v["Properties"].get("Roles"))])
    assert "DrafterBedrockGuardrailRequired" in core and "bedrock:GuardrailIdentifier" in core


def test_runtime_execution_role_without_guardrail_has_no_guardrail_condition():
    """Sandbox without a guardrail: the runtime role must still exist (IaC, never CLI) but not carry a
    guardrail condition its calls could not satisfy."""
    pols = json.dumps([v for v in T_COMPUTE.find_resources("AWS::IAM::Policy").values()
                       if "RuntimeExecutionRole" in json.dumps(v["Properties"].get("Roles"))])
    assert "bedrock:InvokeModel" in pols and "bedrock:GuardrailIdentifier" not in pols and "ApplyGuardrail" not in pols
    T_COMPUTE.has_resource_properties("AWS::IAM::Role", Match.object_like({"RoleName": "pv-test-agentcore-runtime"}))


# -- PAR-1 (port from benefits, 2026-09-06): #168 capture-all trail + WORM lineage --------------------

def test_capture_trail_selects_bedrock_and_agentcore_data_events():
    """The account capture trail (#168) must use ADVANCED selectors that record management events (where
    CloudTrail logs InvokeModel / Converse) PLUS every documented Bedrock data-plane resource type and the
    AgentCore Gateway, so a bypass through ApplyGuardrail / InvokeAgent / RetrieveAndGenerate / async or
    bidirectional invokes / the gateway is captured in WORM custody too. Basic EventSelectors must be gone
    (a trail cannot carry both)."""
    from pv_stacks.lineage_stack import LineageStack
    app = aws_cdk.App()
    t = Template.from_stack(LineageStack(app, "lp", prefix="pv-ptest"))
    trails = t.find_resources("AWS::CloudTrail::Trail")
    assert len(trails) == 1
    props = next(iter(trails.values()))["Properties"]
    assert "EventSelectors" not in props, "basic EventSelectors must be removed when advanced selectors are used"
    sel = props["AdvancedEventSelectors"]
    types = set()
    mgmt = False
    for s in sel:
        fields = {f["Field"]: f["Equals"] for f in s["FieldSelectors"]}
        if fields.get("eventCategory") == ["Management"]:
            mgmt = True
        for rt in fields.get("resources.type", []):
            types.add(rt)
        assert len(fields.get("resources.type", [])) <= 1, "one resources.type per advanced selector"
    assert mgmt, "management events (InvokeModel / Converse live here) must be selected"
    for rt in ("AWS::S3::Object", "AWS::Lambda::Function", "AWS::Bedrock::Model", "AWS::Bedrock::AsyncInvoke",
               "AWS::Bedrock::Guardrail", "AWS::Bedrock::KnowledgeBase", "AWS::Bedrock::AgentAlias",
               "AWS::Bedrock::InlineAgent", "AWS::Bedrock::FlowAlias",
               "AWS::BedrockAgentCore::Gateway", "AWS::BedrockAgentCore::Runtime", "AWS::BedrockAgentCore::RuntimeEndpoint"):
        assert rt in types, f"capture trail no longer selects data events for {rt}"
    assert props.get("IsMultiRegionTrail") is True and props.get("EnableLogFileValidation") is True
    # live-rejected by CloudTrail (2026-09-05): never let it back in
    assert "AWS::Bedrock::Prompt" not in types


# ── 2026-09-05 controls (PAR-1 port from benefits, 2026-09-06) ────────────────────────────────

def _perimeter_stacks(kms="aws-managed", lock_days=0, capture=True):
    from pv_stacks.observability_stack import ObservabilityStack
    from pv_stacks.lineage_stack import LineageStack
    app = aws_cdk.App()
    asset = stage_lambda_bundle()
    data = DataStack(app, "dq", prefix="pv-qtest", retention_profile="sandbox-demo", kms_mode=kms)
    compute = ComputeStack(app, "cq", prefix="pv-qtest", asset_dir=asset, data=data)
    workflow = WorkflowStack(app, "wq", prefix="pv-qtest", compute=compute, data=data)
    lineage = LineageStack(app, "lq", prefix="pv-qtest") if capture else None
    obs = ObservabilityStack(app, "oq", prefix="pv-qtest", compute=compute, workflow=workflow, data=data,
                             model_logging=True, lineage=lineage, transparency_lock_days=lock_days,
                             runtime_role_name="AmazonBedrockAgentCoreSDKRuntime-x",
                             approved_bedrock_principals=("arn:aws:iam::111122223333:role/break-glass",))
    return data, compute, obs


def test_bedrock_perimeter_bypass_alarm_from_capture_trail():
    """DETECTIVE perimeter: with the capture trail present, two metric filters on its log group feed
    Aegis/Perimeter BedrockBypassInvocations - (a) assumed-role sessions whose ISSUING ROLE (not the
    caller-chosen session name) is outside the allowlist, (b) any IAM-user / root caller - and a >=1
    alarm goes to the ops topic. Without the trail there is nothing to read, so no alarm is claimed."""
    _, compute, obs = _perimeter_stacks()
    to = Template.from_stack(obs)
    filters = to.find_resources("AWS::Logs::MetricFilter")
    pats = [json.dumps(v["Properties"]["FilterPattern"]) for v in filters.values()]
    assert len(filters) == 2, f"expected two bypass metric filters, got {len(filters)}"
    joined = " ".join(pats)
    assert "bedrock.amazonaws.com" in joined and "InvokeModel" in joined and "RetrieveAndGenerate" in joined
    assert "sessionIssuer.arn" in joined, "the role filter must key on the issuing ROLE arn, not the session name"
    assert "userIdentity.arn" not in joined and "principalId" not in joined, "never key on a caller-chosen session name"
    assert "IAMUser" in joined and "Root" in joined
    assert "break-glass" in joined and "AmazonBedrockAgentCoreSDKRuntime-x" in joined
    to.has_resource_properties("AWS::CloudWatch::Alarm", Match.object_like({
        "AlarmName": "pv-qtest-bedrock-perimeter-bypass", "Namespace": "Aegis/Perimeter/pv-qtest",
        "MetricName": "BedrockBypassInvocations", "Threshold": 1,
        "ComparisonOperator": "GreaterThanOrEqualToThreshold"}))
    _, _, obs2 = _perimeter_stacks(capture=False)
    assert Template.from_stack(obs2).find_resources("AWS::Logs::MetricFilter") == {}


def test_model_invocation_logging_is_restore_aware():
    """Live-found L6 (benefits Tier-1 gate 2026-09-05): model-invocation logging is an ACCOUNT singleton and
    the old AwsCustomResource simply DELETED it on teardown, switching off the account's pre-existing
    config. The resource is now a Lambda-backed provider that snapshots the prior config to SSM on create
    and restores it on delete - it needs Get (snapshot) as well as Put/Delete, the SSM parameter, and
    PassRole scoped to the bedrock service."""
    _, _, obs = _perimeter_stacks()
    t = Template.from_stack(obs)
    t.resource_count_is("Custom::AegisModelInvocationLogging", 1)
    crs = t.find_resources("Custom::AegisModelInvocationLogging")
    props = list(crs.values())[0]["Properties"]
    assert props["SnapshotParameter"] == "/pv-qtest/model-logging/prior" and "LoggingConfig" in props
    pols = json.dumps(t.find_resources("AWS::IAM::Policy"))
    assert "bedrock:GetModelInvocationLoggingConfiguration" in pols and "bedrock:PutModelInvocationLoggingConfiguration" in pols
    assert "ssm:PutParameter" in pols and "model-logging/prior" in pols
    assert '"iam:PassedToService": "bedrock.amazonaws.com"' in pols
    assert "deleteModelInvocationLoggingConfiguration" not in json.dumps(t.to_json())


def test_invocation_log_store_is_regulated_data_under_production_settings():
    """The model-invocation store records EVERY caller's prompts (account setting), so under
    customer-managed KMS + model_log_lock_days>0 it must be CMK-encrypted (log group AND large-payload
    bucket, with the bedrock service granted use of the key), Object-Locked in COMPLIANCE mode, versioned,
    RETAINED and never auto-emptied. The sandbox default keeps the destroy/auto-delete shape."""
    data, _, obs = _perimeter_stacks(kms="customer-managed", lock_days=400)
    to, td = Template.from_stack(obs), Template.from_stack(data)
    to.has_resource_properties("AWS::S3::Bucket", Match.object_like({
        "ObjectLockEnabled": True,
        "ObjectLockConfiguration": Match.object_like({"Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 400}}}),
        "VersioningConfiguration": {"Status": "Enabled"},
        "BucketEncryption": Match.object_like({"ServerSideEncryptionConfiguration": [
            Match.object_like({"ServerSideEncryptionByDefault": Match.object_like({"SSEAlgorithm": "aws:kms"})})]})}))
    bucket = [v for v in to.find_resources("AWS::S3::Bucket").values() if v.get("Properties", {}).get("ObjectLockEnabled")][0]
    assert bucket.get("DeletionPolicy") == "Retain"
    assert to.find_resources("Custom::S3AutoDeleteObjects") == {}, "a locked regulated-data store must never be auto-emptied"
    lg = [v for v in to.find_resources("AWS::Logs::LogGroup").values()
          if "modelinvocations" in json.dumps(v.get("Properties", {}).get("LogGroupName"))][0]
    assert "KmsKeyId" in lg["Properties"] and lg.get("DeletionPolicy") == "Retain"
    assert "BedrockInvocationLogDelivery" in json.dumps(td.to_json()), "bedrock service must be granted the CMK for delivery"
    _, _, obs0 = _perimeter_stacks()
    t0 = Template.from_stack(obs0)
    assert t0.find_resources("Custom::S3AutoDeleteObjects") != {}
    assert not any(v.get("Properties", {}).get("ObjectLockEnabled") for v in t0.find_resources("AWS::S3::Bucket").values())


def test_bedrock_runtime_endpoint_policy_admits_only_the_governed_drafter():
    """NETWORK half of the perimeter inside the pack VPC: the bedrock-runtime interface endpoint carries
    a policy that allows inference ONLY from the pinned drafter role (+ approved principals), keyed on
    aws:PrincipalArn (the ROLE arn for sessions) and aws:PrincipalAccount."""
    from pv_stacks.network_stack import NetworkStack
    from pv_stacks.compute_stack import drafter_role_name
    app = aws_cdk.App()
    net = NetworkStack(app, "np", prefix="pv-ptest", bedrock_principals=("arn:aws:iam::111122223333:role/break-glass",))
    t = Template.from_stack(net)
    eps = t.find_resources("AWS::EC2::VPCEndpoint")
    bedrock = [v for v in eps.values() if "bedrock-runtime" in json.dumps(v["Properties"]["ServiceName"])]
    assert len(bedrock) == 1
    pol = json.dumps(bedrock[0]["Properties"]["PolicyDocument"])
    assert "GovernedDrafterOnly" in pol and "break-glass" in pol
    assert f":role/{drafter_role_name('pv-ptest')}\"" in pol and "*ServiceRole" not in pol
    roles = T_COMPUTE.find_resources("AWS::IAM::Role", {"Properties": {"RoleName": drafter_role_name("pv-test")}})
    assert len(roles) == 1, "the drafter role must carry the pinned physical name"
    fn = T_COMPUTE.find_resources("AWS::Lambda::Function", {"Properties": {"FunctionName": "pv-test-core-tools"}})
    assert list(fn.values())[0]["Properties"]["Role"]["Fn::GetAtt"][0] == list(roles)[0]
    T_COMPUTE.has_output("DrafterRoleArn", {})
    assert "aws:PrincipalArn" in pol and "aws:PrincipalAccount" in pol
    assert '"bedrock:InvokeModel"' in pol and '"bedrock:InvokeModelWithResponseStream"' in pol
    others = [v for v in eps.values() if "bedrock-runtime" not in json.dumps(v["Properties"]["ServiceName"])]
    assert all("PolicyDocument" not in v["Properties"] for v in others)


def test_private_vpc_azs_are_ones_every_endpoint_service_offers():
    """cognito-idp is offered only in us-east-1b/1c/1d (live-found in the benefits Tier-1 gate), so every
    subnet must sit in the vetted AZ set, every interface endpoint must span exactly those subnets, and
    -c vpc_azs overrides."""
    from pv_stacks.network_stack import NetworkStack
    t = Template.from_stack(NetworkStack(aws_cdk.App(), "naz0", prefix="pv-az"))
    azs = {v["Properties"]["AvailabilityZone"] for v in t.find_resources("AWS::EC2::Subnet").values()}
    assert azs == set(NetworkStack.DEFAULT_AZS), azs
    assert "us-east-1a" not in azs
    for v in t.find_resources("AWS::EC2::VPCEndpoint").values():
        if v["Properties"].get("VpcEndpointType") == "Interface":
            assert len(v["Properties"]["SubnetIds"]) == len(NetworkStack.DEFAULT_AZS)
    n2 = Template.from_stack(NetworkStack(aws_cdk.App(), "naz", prefix="pv-az2", azs=("us-east-1c", "us-east-1d")))
    assert {v["Properties"]["AvailabilityZone"] for v in n2.find_resources("AWS::EC2::Subnet").values()} == {"us-east-1c", "us-east-1d"}


# boto3 service name -> VPC endpoint ServiceName suffix. Every service a DEPLOYED Lambda talks to
# must have an endpoint in the private VPC; anything else hangs until the Lambda timeout.
_BOTO3_TO_ENDPOINT = {
    "ssm": "ssm", "cloudwatch": "monitoring", "logs": "logs", "kms": "kms", "sts": "sts",
    "secretsmanager": "secretsmanager", "stepfunctions": "states", "comprehend": "comprehend",
    "bedrock-runtime": "bedrock-runtime", "bedrock-agentcore": "bedrock-agentcore",
    "cognito-idp": "cognito-idp", "s3": "s3", "dynamodb": "dynamodb",
}


def _deployed_handler_modules():
    """The handler modules ComputeStack actually deploys (the `fn("name", "module")` calls)."""
    import re
    src = (ROOT / "cdk" / "pv_stacks" / "compute_stack.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(r'=\s*fn\(\s*"[^"]+",\s*"([A-Za-z0-9_]+)"', src)))


def _bundle_boto3_services(bundle_dir, roots):
    """boto3 client/resource service names reachable from `roots` through the bundle's own modules
    (transitive local imports), so an undeployed reference module does not inflate the set."""
    import ast
    bundle = pathlib.Path(bundle_dir)
    local = {p.stem for p in bundle.glob("*.py")}
    seen, todo, services = set(), list(roots), set()
    while todo:
        mod = todo.pop()
        if mod in seen or mod not in local:
            continue
        seen.add(mod)
        tree = ast.parse((bundle / f"{mod}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                todo.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                todo.append(node.module.split(".")[0])
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr in ("client", "resource")
                  and isinstance(node.func.value, ast.Name) and node.func.value.id == "boto3"
                  and node.args and isinstance(node.args[0], ast.Constant)):
                services.add(node.args[0].value)
    return services


def test_private_vpc_has_an_endpoint_for_every_service_the_deployed_lambdas_call():
    """Live-found L9 (benefits Tier-1 gate attempt 8, 2026-09-06): every governed tool reads the kill switch
    from SSM first and the budget meter publishes CloudWatch metrics, but the private VPC had no ssm or
    monitoring endpoint - every tool hung to its timeout and the gateway returned 500s. Derive the required
    endpoint set from the boto3 clients in the DEPLOYED bundle and assert each one exists."""
    from pv_stacks.network_stack import NetworkStack
    services = _bundle_boto3_services(stage_lambda_bundle(), _deployed_handler_modules())
    assert {"ssm", "cloudwatch", "dynamodb"} <= services, services
    unknown = services - set(_BOTO3_TO_ENDPOINT)
    assert not unknown, f"add these boto3 services to _BOTO3_TO_ENDPOINT: {unknown}"
    present = set()
    for v in Template.from_stack(NetworkStack(aws_cdk.App(), "nep", prefix="pv-ep")).find_resources("AWS::EC2::VPCEndpoint").values():
        name = v["Properties"]["ServiceName"]
        if isinstance(name, dict):
            name = "".join(x for x in name["Fn::Join"][1] if isinstance(x, str))
        present.add(name.rsplit(".", 1)[-1])
    missing = {_BOTO3_TO_ENDPOINT[s] for s in services} - present
    assert not missing, f"deployed Lambdas call services with no VPC endpoint (would hang in private mode): {missing}"


def test_cmk_logs_grant_covers_every_log_group_family():
    """Live-found (benefits Tier-1 gate, 2026-09-05): the CMK's CloudWatch-Logs grant covered only
    /aws/lambda/<prefix>-*, so the workflow controller log group (/aws/states/...) was refused the key under
    kms=customer-managed. Every log-group family the pack encrypts with the CMK must be in the grant."""
    d = DataStack(aws_cdk.App(), "dk", prefix="pv-ktest", retention_profile="sandbox-demo", kms_mode="customer-managed")
    keys = Template.from_stack(d).find_resources("AWS::KMS::Key")
    assert len(keys) == 1
    pol = json.dumps(next(iter(keys.values()))["Properties"]["KeyPolicy"])
    for fam in ("log-group:/aws/lambda/pv-ktest-*", "log-group:/aws/states/pv-ktest-*",
                "log-group:/aws/bedrock/modelinvocations/pv-ktest*", "log-group:/aws/cloudtrail/pv-ktest-*"):
        assert fam in pol, f"CMK logs grant does not cover {fam}"
