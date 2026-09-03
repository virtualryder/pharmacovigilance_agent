"""Phase 110 — full transparency: the runtime binds ONE correlation set (session.id, tenant, case_id,
requester) as Strands trace_attributes, OTEL baggage, and Bedrock `requestMetadata` on every Converse
call; the workflow carries the execution ARN to every Lambda; every PV tool handler emits one
`aegis.call` log line with the keys. Offline: the AgentCore/Strands SDKs are stubbed at import."""
import json
import pathlib
import sys
import types

import boto3
from botocore.stub import Stubber

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_agent(monkeypatch):
    for name in ("bedrock_agentcore", "bedrock_agentcore.runtime", "strands", "strands.models",
                 "strands.tools", "strands.tools.mcp", "mcp", "mcp.client", "mcp.client.streamable_http"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["bedrock_agentcore.runtime"].BedrockAgentCoreApp = lambda: types.SimpleNamespace(entrypoint=lambda f: f, run=lambda: None)
    sys.modules["strands"].Agent = object
    sys.modules["strands.models"].BedrockModel = object
    sys.modules["strands.tools.mcp"].MCPClient = object
    sys.modules["mcp.client.streamable_http"].streamablehttp_client = object
    sys.path.insert(0, str(ROOT / "lib" / "runtime"))
    sys.modules.pop("agent", None)
    import agent
    return agent


def test_runtime_correlation_and_request_metadata_hook(monkeypatch):
    agent = _load_agent(monkeypatch)
    ctx = types.SimpleNamespace(session_id="rt-sess-1")
    corr = agent._correlation(ctx, "sp-a", "C-1", "cw-a")
    assert corr == {"session.id": "rt-sess-1", "case_id": "C-1", "requester": "cw-a", "tenant": "sp-a"}
    assert "tenant" not in agent._correlation(ctx, None, "C-1", "cw-a")           # silo: no tenant tag
    # the runtime's session-tenant mirror honours the tenant_<id> group (what Cognito access tokens carry)
    import base64
    tok = "h." + base64.urlsafe_b64encode(json.dumps({"cognito:groups": ["pv_reviewer", "tenant_sp-b"]}).encode()).decode().rstrip("=") + ".s"
    assert agent._session_tenant(tok) == "sp-b"
    assert agent._meta_value("a b;c<d>") == "a b_c_d_" and len(agent._meta_value("x" * 300)) == 256
    # the boto session injects requestMetadata on Converse - the model-invocation log row is tagged
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x"); monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")
    sess = agent._bedrock_session(corr)
    c = sess.client("bedrock-runtime")
    st = Stubber(c)
    st.add_response("converse",
                    {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}, "stopReason": "end_turn",
                     "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}},
                    {"modelId": "m", "messages": [{"role": "user", "content": [{"text": "q"}]}],
                     "requestMetadata": {"tenant": "sp-a", "session_id": "rt-sess-1", "case_id": "C-1",
                                         "requester": "cw-a", "governed_by": "aegis"}})
    st.activate()
    assert c.converse(modelId="m", messages=[{"role": "user", "content": [{"text": "q"}]}])["stopReason"] == "end_turn"
    st.assert_no_pending_responses()


def test_workflow_carries_execution_arn_and_gateway_schema_has_trace_field():
    import aws_cdk
    from aws_cdk.assertions import Template
    sys.path.insert(0, str(ROOT / "cdk"))
    from app import stage_lambda_bundle
    from pv_stacks.data_stack import DataStack
    from pv_stacks.compute_stack import ComputeStack
    from pv_stacks.workflow_stack import WorkflowStack
    from pv_stacks.identity_stack import IdentityStack
    from pv_stacks.gateway_stack import GatewayStack
    app = aws_cdk.App()
    data = DataStack(app, "d5", prefix="pv-obs", retention_profile="sandbox-demo")
    compute = ComputeStack(app, "c5", prefix="pv-obs", asset_dir=stage_lambda_bundle(), data=data)
    workflow = WorkflowStack(app, "w5", prefix="pv-obs", compute=compute, data=data)
    identity = IdentityStack(app, "i5", prefix="pv-obs")
    gateway = GatewayStack(app, "g5", prefix="pv-obs", compute=compute, identity=identity)
    from pv_stacks.observability_stack import ObservabilityStack
    obs = ObservabilityStack(app, "o5", prefix="pv-obs", compute=compute, workflow=workflow, data=data,
                             gateway=gateway, model_logging=True)
    obs_off = ObservabilityStack(app, "o6", prefix="pv-obs2", compute=compute, workflow=workflow, data=data)
    oj = json.dumps(Template.from_stack(obs).to_json())
    # Bedrock model-invocation logging (account-level, opt-in) with a CloudWatch group + S3 large-data
    # bucket + bedrock.amazonaws.com role; gateway vended request logs via CloudWatch Logs delivery
    assert "putModelInvocationLoggingConfiguration" in oj and "deleteModelInvocationLoggingConfiguration" in oj
    assert "/aws/bedrock/modelinvocations/pv-obs" in oj and "textDataDeliveryEnabled" in oj
    assert '"LogType": "APPLICATION_LOGS"' in oj and "/aws/vendedlogs/bedrock-agentcore/gateway/pv-obs" in oj
    oj2 = json.dumps(Template.from_stack(obs_off).to_json())
    assert "putModelInvocationLoggingConfiguration" not in oj2 and "AWS::Logs::DeliverySource" not in oj2
    wj = json.dumps(Template.from_stack(workflow).to_json())
    # (the definition is a JSON string inside the template, so match the two tokens, not the pair)
    assert wj.count("__aegis_execution.$") == 14 and wj.count("$$.Execution.Id") == 14   # every Lambda-backed state (14 in PV), silo too
    assert "__aegis_trace" in json.dumps(Template.from_stack(gateway).to_json())


def test_every_pv_handler_emits_one_aegis_call_line(monkeypatch, capsys):
    """Each governed tool handler is instrumented: one structured line, keys present, no argument values."""
    import telemetry
    import workflow_guards
    monkeypatch.setenv("TENANT_ID", "agency-1"); monkeypatch.delenv("MULTITENANT", raising=False)
    ctx = types.SimpleNamespace(aws_request_id="r-1", invoked_function_arn="arn:aws:lambda:us-east-1:123456789012:function:g")
    workflow_guards.handler({"guard": "extracted", "fields": {"suspect_product": "atorvastatin"},
                             "__aegis_execution": "arn:aws:states:us-east-1:123456789012:execution:sm:e9"}, ctx)
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    calls = [l for l in lines if l.get("aegis") == "call"]
    assert len(calls) == 1 and calls[0]["tool"] == "workflow_guards"
    assert calls[0]["execution_arn"].endswith(":e9") and calls[0]["request_id"] == "r-1" and calls[0]["tenant"] == "agency-1"
    assert "atorvastatin" not in json.dumps(calls[0]) and calls[0]["arg_keys"] == ["fields", "guard"]
    assert telemetry.current() == {}
    # every handler module in the bundle is decorated
    import inspect
    for mod in ("mask_pii", "ingest_case", "workflow_guards", "intake_icsr", "assess_seriousness",
                "pv_core", "openfda_lookup", "detect_duplicate", "record_causality", "write_audit", "request_signoff",
                "signoff_register", "finalize_signoff", "approve_signoff"):
        m = __import__(mod)
        assert getattr(m.handler, "__wrapped__", None) is not None, "%s.handler is not instrumented" % mod
