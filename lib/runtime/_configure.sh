#!/usr/bin/env bash
# Configure the AgentCore Runtime for an agent. Usage: _configure.sh <agent_dir>
SELF="$(cd "$(dirname "$0")" && pwd)"; export MSYS_NO_PATHCONV=1   # Git-Bash: keep "/ben-.../gateway-url" a parameter NAME, not a Windows path (found 2026-09-02: GATEWAY_SSM_PARAM became C:/Program Files/Git/...)
AGENT="$(cd "${1:?usage: _configure.sh <agent_dir>}" && pwd)"; cd "$SELF"; source "$SELF/_env.sh"
[ -f "$STATE" ] || { echo "spine-state not found ($STATE). Deploy the spine first (lib/engine/deploy.sh)."; exit 1; }
source "$STATE"   # DISCOVERY, CLIENT_ID, GW_URL
# RT-4 / #233: RESTRICT RUNTIME INVOCATION TO THE GATEWAY.
# RT-4, REVISED 2026-09-09 by the first gate run that got far enough to execute it.
#
# The original form set `allowedWorkloadConfiguration` unconditionally whenever a gateway ARN was
# present, to close R4-2: "a token that could reach the gateway could ALSO reach the runtime
# directly, past the gateway Cedar interceptor."
#
# It works - and it makes the agent unreachable. AWS restricts the runtime to workloads in the
# request identity chain, and the only allowed workload type is an AgentCore GATEWAY. In this
# architecture the gateway is DOWNSTREAM of the runtime: its targets are the tool Lambdas built
# from the manifest, and the runtime is not one of them. Nothing here ever invokes the runtime
# through the gateway, so with the restriction on, the runtime has no permitted invoker at all.
# Live on 2026-09-09 every proof that drives the agent got:
#     {"code": -32001, "message": "Transaction token required: authorizer has
#      AllowedWorkloadConfiguration configured"}
# and four gate checks failed for that one reason (G111, kill-switch, budget, guardrail-assessed).
#
# The repository had already reached the right conclusion and RT-4 contradicted it. MATURITY.yaml,
# recording the fourth external review: "Partly right: R4-2 - the runtime own model calls are
# IAM+guardrail governed, not gateway governed (claim fixed); DIRECT RUNTIME INVOCATION BY A JWT
# HOLDER IS THE DESIGNED ENTRY." The platform README says the same at line 138.
#
# Where R4-2 is actually mitigated: at the GATEWAY, by Cedar. The exposure worth worrying about is
# the opposite direction - a token holder calling the gateway directly and skipping the agent's
# masking step - and mask_before_assess / mask_before_draft / mask_before_overpayment /
# mask_before_redetermine forbid exactly that unless context.input.deidentified == true, with
# consent, budget, entitlement and service-window gates beside them in the perimeter profile.
# Those are enforced on every tool call whoever makes it. That is the control; this was not.
#
# So the restriction is OPT-IN, and off by default. It stays in the tree because it is correct for
# a deployment where the runtime IS exposed as a gateway target - the shape AWS built the field
# for. Turning it on without that topology breaks the agent, which is why the default changed
# rather than the code being deleted.
#
# FAIL LOUD ON THE OPT-IN, not on the default: RT4_GATEWAY_ONLY=1 with no GW_ARN refuses, because
# asking for the restriction and silently not getting it is the failure this half of the control
# exists to prevent.
#   https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore-control/update-agent-runtime.html
#   https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html#deploy-agent-allowed-workload
# --- RT4-BLOCK-START ---   (tests/test_runtime_gateway_only.py lifts between these markers)
if [ "${RT4_GATEWAY_ONLY:-0}" = "1" ]; then
  if [ -z "${GW_ARN:-}" ]; then
    echo "REFUSED: RT4_GATEWAY_ONLY=1 but no GW_ARN in $STATE. Asking for the gateway-only runtime posture and silently not getting it is worse than not asking: deploy the spine first, or unset RT4_GATEWAY_ONLY."; exit 1
  fi
  WORKLOAD=",\"allowedWorkloadConfiguration\":{\"hostingEnvironments\":[{\"arn\":\"$GW_ARN\"}]}"
  echo "rt4_gateway_only=$GW_ARN (OPT-IN: the runtime will accept ONLY calls whose identity chain includes this gateway - correct only if the runtime is a gateway TARGET)"
else
  WORKLOAD=""
  echo "rt4_gateway_only=OFF (default) - a pool JWT holder invokes the runtime directly, which is the designed entry (MATURITY.yaml, fourth review R4-2). R4-2 is mitigated at the GATEWAY by the Cedar mask/consent/budget/entitlement policies, not here."
fi
# --- RT4-BLOCK-END ---
ACJSON="{\"customJWTAuthorizer\":{\"discoveryUrl\":\"$DISCOVERY\",\"allowedClients\":[\"$CLIENT_ID\"]$WORKLOAD}}"
echo "runtime=$RUNTIME_NAME"
# EXECUTION ROLE AS IaC (third external review, 2026-09-05; RT-3 port from benefits): never let the toolkit
# auto-create the role (AWS: CLI-generated policies are for development/testing). The compute stack exports
# the least-privilege role; resolve it from the stack output (or RUNTIME_ROLE_ARN) and REFUSE without it.
PREFIX="$(printf '%s' "$SSM_PARAM" | sed 's#^/##; s#-[^-/]*/.*$##')"   # strip the pack suffix + path
if [ -z "${RUNTIME_ROLE_ARN:-}" ]; then
  RUNTIME_ROLE_ARN="$(aws cloudformation describe-stacks --stack-name "$PREFIX-compute" \
    --query "Stacks[0].Outputs[?OutputKey=='RuntimeExecutionRoleArn'].OutputValue" --output text 2>/dev/null)"
fi
if [ -z "$RUNTIME_ROLE_ARN" ] || [ "$RUNTIME_ROLE_ARN" = "None" ]; then
  echo "REFUSED: no RuntimeExecutionRoleArn output on $PREFIX-compute (deploy the compute stack first, or set RUNTIME_ROLE_ARN). The CLI-generated role is not permitted."; exit 1
fi
echo "execution_role=$RUNTIME_ROLE_ARN"
"$AC" configure -c -e agent.py -n "$RUNTIME_NAME" -rf requirements.txt -ecr auto --disable-memory -ac "$ACJSON" -rha Authorization \
  --execution-role "$RUNTIME_ROLE_ARN" 2>&1 | tail -40
echo "CONFIGURE_EXIT=${PIPESTATUS[0]}"
