#!/usr/bin/env bash
# Grant the Runtime exec role ssm:GetParameter for gateway discovery. Usage: _obs_setup.sh <agent_dir>
SELF="$(cd "$(dirname "$0")" && pwd)"; export MSYS_NO_PATHCONV=1
AGENT="$(cd "${1:?usage: _obs_setup.sh <agent_dir>}" && pwd)"; cd "$SELF"; source "$SELF/_env.sh"
# The DEPLOYMENT's discovery parameter (spine-state, e.g. /ben-<env>-eligibility/gateway-url) overrides the
# manifest default (/ben-eligibility/...): found 2026-09-02 (mt4 gate) - the grant covered the manifest
# path while the runtime looked up the CDK one -> AccessDenied -> silent fallback to the GATEWAY_URL env.
[ -f "$STATE" ] && source "$STATE"
ACC="$(aws sts get-caller-identity --query Account --output text | tr -d '\r')"
# P0-7: use the EXACT runtime exec role (emitted at runtime deploy; export RUNTIME_EXEC_ROLE via _env.sh
# or the environment). NEVER discover the role by name prefix — a prefix match can silently attach a
# policy to the WRONG role. Fail-closed: if the exact role is not provided, skip the SSM grant loudly.
ROLE="${RUNTIME_EXEC_ROLE:-}"
SSM_ROOT="$(printf '%s' "$SSM_PARAM" | sed 's#/[^/]*$##')"   # /<root>/gateway-url -> /<root>
# task 128: the deployment prefix (ben-<env>) from the SSM root (/ben-<env>-eligibility) -> the budget meter
# table <prefix>-budgets (GetItem + the conditional UpdateItem) and the Aegis/Budget metrics namespace.
PREFIX="$(printf '%s' "$SSM_ROOT" | sed 's#^/##; s#-[^-/]*$##')"   # strip the pack suffix (-eligibility / -pharmacovigilance / -aid)
printf '%s' '{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["ssm:GetParameter"],"Resource":"arn:aws:ssm:'"$REGION"':'"$ACC"':parameter'"$SSM_ROOT"'/*"},
 {"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:UpdateItem"],"Resource":"arn:aws:dynamodb:'"$REGION"':'"$ACC"':table/'"$PREFIX"'-budgets"},
 {"Effect":"Allow","Action":["cloudwatch:PutMetricData"],"Resource":"*","Condition":{"StringEquals":{"cloudwatch:namespace":"Aegis/Budget"}}}]}' > ssm-pol.json
if [ -n "$ROLE" ] && [ "$ROLE" != "None" ]; then
  echo "runtime exec role (explicit): $ROLE"
  aws iam put-role-policy --role-name "$ROLE" --policy-name agent-runtime-ssm --policy-document file://ssm-pol.json --region "$REGION" && echo "  attached ssm:GetParameter to $ROLE"
else
  echo "  RUNTIME_EXEC_ROLE not set — skipping SSM grant (P0-7: exact role required, no name-prefix discovery)."
fi
aws xray update-trace-segment-destination --destination CloudWatchLogs --region "$REGION" >/dev/null 2>&1 && echo "  enabled Transaction Search" || echo "  (Transaction Search skipped)"
echo "OBS_SETUP_DONE"
