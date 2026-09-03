#!/usr/bin/env bash
# Build + deploy the Runtime for an agent. Usage: _launch.sh <agent_dir>
SELF="$(cd "$(dirname "$0")" && pwd)"; export MSYS_NO_PATHCONV=1   # Git-Bash: keep "/ben-.../gateway-url" a parameter NAME, not a Windows path (found 2026-09-02: GATEWAY_SSM_PARAM became C:/Program Files/Git/...)
AGENT="$(cd "${1:?usage: _launch.sh <agent_dir>}" && pwd)"; cd "$SELF"; source "$SELF/_env.sh"
[ -f "$STATE" ] || { echo "spine-state not found ($STATE)."; exit 1; }
source "$STATE"
MODEL="${RUNTIME_MODEL_ID:-us.anthropic.claude-sonnet-4-5-20250929-v1:0}"
echo "GATEWAY_URL=$GW_URL runtime=$RUNTIME_NAME"
# Hybrid multi-tenant (phase 107): MULTITENANT=1 makes the runtime bind the session to the identity's
# tenant and refuse un-tenanted identities (agent.py). Unset = silo.
MT_ENV=(); [ -n "${MULTITENANT:-}" ] && MT_ENV=(--env MULTITENANT="$MULTITENANT")
# Kill switch (task 127): the pack's parameter lives beside the gateway-url one (same SSM root, same
# runtime read grant); GLOBAL_KILL_SWITCH (optional) adds the platform-wide parameter.
KS="${SSM_PARAM%/*}/kill-switch"; [ -n "${GLOBAL_KILL_SWITCH:-}" ] && KS="$KS,$GLOBAL_KILL_SWITCH"
# Budget meter (task 128): table + deployment from the SSM root; the token cap from the manifest budget:
# block (B5: one place to set the number); the pinned price table from lib/model_prices.json. Paths are
# handed to Windows Python in native form (cygpath) because MSYS_NO_PATHCONV is on for this script.
PREFIX="$(printf '%s' "$SSM_PARAM" | sed 's#^/##; s#-[^-/]*/.*$##')"   # strip the pack suffix (-eligibility / -pharmacovigilance / -aid) + path
MANIFEST="$AGENT/manifest.yaml"; PRICES="$SELF/../model_prices.json"
if command -v cygpath >/dev/null 2>&1; then MANIFEST="$(cygpath -w "$MANIFEST")"; PRICES="$(cygpath -w "$PRICES")"; fi
BUDGET_CAP_TOKENS="${BUDGET_CAP_TOKENS:-$(python -c "import yaml,sys;print(int((yaml.safe_load(open(sys.argv[1])).get('budget') or {}).get('monthly_token_cap') or 0))" "$MANIFEST")}"
BUDGET_PRICES_JSON="$(python -c "import json,sys;print(json.dumps(json.load(open(sys.argv[1])),separators=(',',':')))" "$PRICES")"
echo "budget: table=$PREFIX-budgets cap_tokens=$BUDGET_CAP_TOKENS usd_micro=${BUDGET_CAP_USD_MICRO:-0} behavior=${BUDGET_BEHAVIOR:-hard}"
"$AC" launch \
  --env GATEWAY_URL="$GW_URL" \
  --env GATEWAY_SSM_PARAM="$SSM_PARAM" \
  --env KILL_SWITCH_PARAMS="$KS" \
  --env BUDGET_TABLE="$PREFIX-budgets" \
  --env BUDGET_CAP_TOKENS="$BUDGET_CAP_TOKENS" \
  --env BUDGET_CAP_USD_MICRO="${BUDGET_CAP_USD_MICRO:-0}" \
  --env BUDGET_BEHAVIOR="${BUDGET_BEHAVIOR:-hard}" \
  --env BUDGET_DEPLOYMENT="$PREFIX" \
  --env BUDGET_PRICES_JSON="$BUDGET_PRICES_JSON" \
  --env MODEL_ID="$MODEL" \
  --env SYSTEM_PROMPT="$WORKFLOW_PROMPT" \
  "${MT_ENV[@]}" \
  --auto-update-on-conflict 2>&1
echo "LAUNCH_EXIT=${PIPESTATUS[0]}"
