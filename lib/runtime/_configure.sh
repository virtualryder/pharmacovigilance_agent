#!/usr/bin/env bash
# Configure the AgentCore Runtime for an agent. Usage: _configure.sh <agent_dir>
SELF="$(cd "$(dirname "$0")" && pwd)"; export MSYS_NO_PATHCONV=1   # Git-Bash: keep "/ben-.../gateway-url" a parameter NAME, not a Windows path (found 2026-09-02: GATEWAY_SSM_PARAM became C:/Program Files/Git/...)
AGENT="$(cd "${1:?usage: _configure.sh <agent_dir>}" && pwd)"; cd "$SELF"; source "$SELF/_env.sh"
[ -f "$STATE" ] || { echo "spine-state not found ($STATE). Deploy the spine first (lib/engine/deploy.sh)."; exit 1; }
source "$STATE"   # DISCOVERY, CLIENT_ID, GW_URL
ACJSON="{\"customJWTAuthorizer\":{\"discoveryUrl\":\"$DISCOVERY\",\"allowedClients\":[\"$CLIENT_ID\"]}}"
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
