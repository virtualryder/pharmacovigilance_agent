"""GA-1 — CloudFormation custom-resource provider for the AgentCore control plane.

Executes, as IaC, the exact sequence the proven shell engine performed:
  policy engine → gateway (MCP, CUSTOM_JWT, policy engine LOG_ONLY) → SSM discovery param
  → gateway targets (one per governed tool Lambda) → Cedar policies (gateway ARN injected into
  forbids) → update gateway to ENFORCE.
Delete reverses: policies → targets → gateway → engine → SSM. Update = idempotent re-create of
policies + re-assert ENFORCE. Fail-loud: any control-plane error fails the stack operation."""
import json
import time
import urllib.request

import boto3


def _send(event, context, status, data=None, reason=""):
    body = json.dumps({
        "Status": status, "Reason": (reason or "ok")[:1000],
        "PhysicalResourceId": data.get("GatewayId", "agentcore-attachment") if data else "agentcore-attachment",
        "StackId": event["StackId"], "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"], "Data": data or {},
    }).encode()
    req = urllib.request.Request(event["ResponseURL"], data=body, method="PUT",
                                 headers={"Content-Type": ""})
    urllib.request.urlopen(req, timeout=30)


def _wait(fn, want, tries=40, delay=6):
    for _ in range(tries):
        if fn() == want:
            return
        time.sleep(delay)
    raise RuntimeError(f"resource did not reach {want}")


def _find_engine(cc, name):
    try:
        for e in cc.list_policy_engines().get("policyEngines", []):
            if e.get("name") == name:
                return e["policyEngineId"]
    except Exception:
        pass
    return None


def _create(cc, ssm, p, region, acct):
    # Live-run find: a failed CreateGateway can orphan the policy engine (created first). Reuse an
    # existing engine with our name instead of ConflictException-failing forever.
    engine_id = _find_engine(cc, p["EngineName"])
    if engine_id is None:
        engine_id = cc.create_policy_engine(name=p["EngineName"],
                                            description=p.get("EngineDesc", ""))["policyEngineId"]
    engine_arn = f"arn:aws:bedrock-agentcore:{region}:{acct}:policy-engine/{engine_id}"
    _wait(lambda: cc.get_policy_engine(policyEngineId=engine_id)["status"], "ACTIVE")

    authz = json.loads(p["AuthorizerConfigJson"])
    gw = cc.create_gateway(name=p["GatewayName"], roleArn=p["GatewayRoleArn"],
                           protocolType="MCP", authorizerType="CUSTOM_JWT",
                           authorizerConfiguration=authz,
                           policyEngineConfiguration={"arn": engine_arn, "mode": "LOG_ONLY"},
                           description=p.get("GatewayDesc", ""))
    gw_id = gw["gatewayId"]
    _wait(lambda: cc.get_gateway(gatewayIdentifier=gw_id)["status"], "READY")
    g = cc.get_gateway(gatewayIdentifier=gw_id)
    gw_arn, gw_url = g["gatewayArn"], g["gatewayUrl"]
    ssm.put_parameter(Name=p["SsmParam"], Type="String", Overwrite=True, Value=gw_url)

    last = None
    for t in json.loads(p["TargetsJson"]):
        last = cc.create_gateway_target(
            gatewayIdentifier=gw_id, name=t["name"],
            targetConfiguration={"mcp": {"lambda": {
                "lambdaArn": t["lambda_arn"],
                "toolSchema": {"inlinePayload": t["tools"]}}}},
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )["targetId"]
    if last:
        _wait(lambda: cc.get_gateway_target(gatewayIdentifier=gw_id, targetId=last)["status"], "READY")

    for pol in json.loads(p["PoliciesJson"]):
        definition = pol["definition"].replace("__GATEWAY_ARN__", gw_arn)
        pid = cc.create_policy(policyEngineId=engine_id, name=pol["name"],
                               definition={"cedar": {"statement": definition}},
                               validationMode=pol.get("validation_mode", "FAIL_ON_ANY_FINDINGS"))["policyId"]
        _wait(lambda: cc.get_policy(policyEngineId=engine_id, policyId=pid)["status"], "ACTIVE")

    cc.update_gateway(gatewayIdentifier=gw_id, name=p["GatewayName"], roleArn=p["GatewayRoleArn"],
                      protocolType="MCP", authorizerType="CUSTOM_JWT",
                      authorizerConfiguration=authz,
                      policyEngineConfiguration={"arn": engine_arn, "mode": "ENFORCE"})
    _wait(lambda: cc.get_gateway(gatewayIdentifier=gw_id)["status"], "READY")
    return {"GatewayId": gw_id, "GatewayArn": gw_arn, "GatewayUrl": gw_url,
            "PolicyEngineId": engine_id, "Enforcement": "ENFORCE"}


def _delete(cc, ssm, p, gw_id):
    # Live-run find: rollback of a FAILED create may target a gateway that was never created.
    # Delete must be tolerant of every absent resource (idempotent), never raise on not-found.
    try:
        eng = cc.get_gateway(gatewayIdentifier=gw_id)["policyEngineConfiguration"]["arn"].split("/")[-1]
    except Exception:
        # No gateway — but a failed create may have ORPHANED the engine; clean it by name.
        orphan = _find_engine(cc, p.get("EngineName", ""))
        if orphan:
            try:
                for pol in cc.list_policies(policyEngineId=orphan).get("policies", []):
                    cc.delete_policy(policyEngineId=orphan, policyId=pol["policyId"])
            except Exception:
                pass
            try:
                cc.delete_policy_engine(policyEngineId=orphan)
            except Exception:
                pass
        try:
            ssm.delete_parameter(Name=p.get("SsmParam", ""))
        except Exception:
            pass
        return   # nothing (else) was created
    if eng:
        for pol in cc.list_policies(policyEngineId=eng).get("policies", []):
            try:
                cc.delete_policy(policyEngineId=eng, policyId=pol["policyId"])
            except Exception:
                pass
    for t in cc.list_gateway_targets(gatewayIdentifier=gw_id).get("items", []):
        try:
            cc.delete_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"])
        except Exception:
            pass
    time.sleep(10)
    try:
        cc.delete_gateway(gatewayIdentifier=gw_id)
    except Exception:
        pass
    if eng:
        time.sleep(10)
        try:
            cc.delete_policy_engine(policyEngineId=eng)
        except Exception:
            pass
    try:
        ssm.delete_parameter(Name=p["SsmParam"])
    except Exception:
        pass


def handler(event, context):
    p = event.get("ResourceProperties", {})
    region = context.invoked_function_arn.split(":")[3]
    acct = context.invoked_function_arn.split(":")[4]
    cc = boto3.client("bedrock-agentcore-control", region_name=region)
    ssm = boto3.client("ssm", region_name=region)
    try:
        if event["RequestType"] == "Create":
            data = _create(cc, ssm, p, region, acct)
        elif event["RequestType"] == "Delete":
            _delete(cc, ssm, p, event.get("PhysicalResourceId", ""))
            data = {}
        else:  # Update: tear down and re-create attachment (policies/targets may have changed)
            _delete(cc, ssm, p, event.get("PhysicalResourceId", ""))
            data = _create(cc, ssm, p, region, acct)
        _send(event, context, "SUCCESS", data)
    except Exception as exc:  # fail loud — never a silent partial attachment
        _send(event, context, "FAILED", {}, reason=f"{type(exc).__name__}: {exc}")
        raise
