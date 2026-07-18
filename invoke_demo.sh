#!/usr/bin/env bash
# One-off: invoke the deployed PV Runtime as a safety reviewer WITH real case data. Usage: bash invoke_demo.sh
SELF="$(cd "$(dirname "$0")" && pwd)"
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 PYTHONUTF8=1 AWS_REGION=us-east-1 AWS_PAGER=""
AGENT="$SELF/agents/pharmacovigilance"; RT="$SELF/lib/runtime"
source "$AGENT/spine-state.env"
if [ -f "$RT/.venv/Scripts/agentcore.exe" ]; then AC="$RT/.venv/Scripts/agentcore.exe"; PY="$RT/.venv/Scripts/python.exe"; else AC="$RT/.venv/bin/agentcore"; PY="$RT/.venv/bin/python"; fi
TOK="$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id "$CLIENT_ID" \
  --auth-parameters "USERNAME=pv_reviewer,PASSWORD=${PV_REVIEWER_PW:-ChangeMe-Reviewer1!}" --region us-east-1 \
  --query 'AuthenticationResult.AccessToken' --output text | tr -d '\r')"
SRC="Adverse event: Patient John Doe, DOB 1970-02-15, took atorvastatin and was hospitalized with rhabdomyolysis. Serious, unexpected. Reporter: HCP."
PROMPT="Process this adverse-event report for case ICSR-2026-0700. Raw source: $SRC  Run the full governed workflow end to end (intake_icsr, openfda_lookup, mask_pii, assess_seriousness, draft_narrative, write_audit, request_signoff) and request safety-reviewer sign-off with case id ICSR-2026-0700 and requester pv_reviewer."
PAYLOAD="$("$PY" -c "import json,sys;print(json.dumps({'access_token':sys.argv[1],'case_id':'ICSR-2026-0700','requester':'pv_reviewer','prompt':sys.argv[2]}))" "$TOK" "$PROMPT")"
cd "$RT"
"$AC" invoke --bearer-token "$TOK" "$PAYLOAD" 2>&1
echo "INVOKE_EXIT=${PIPESTATUS[0]}"
