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
                "DraftNarrative", "AuditIntent", "HumanSignoff", "Finalize", "Committed"]
    assert visited == expected, f"happy path deviates from the regulated sequence: {visited}"
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
