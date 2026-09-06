// require_service_window  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS  ·  scope: perimeter)
// TEMPORAL (#161): no governed DECISION action outside the deployment's service window. SCOPED to the
// declared decision actions - an UNSCOPED context.input access is invalid on the GA AgentCore Policy
// engine (the built-in actions InvokeAgent/InvokeLLM/Mcp/... carry no `input`). within_service_window is
// set by the gateway interceptor from the real request time (authoritative, never the caller); it is an
// OPTIONAL field, so the access is presence-guarded and a missing value fails closed.
forbid(
  principal,
  action in [AgentCore::Action::"assess-seriousness___assess_seriousness", AgentCore::Action::"detect-duplicate___detect_duplicate", AgentCore::Action::"record-causality___record_causality", AgentCore::Action::"pv-core___draft_narrative"],
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/pv-safety-gw"
)
unless {
  context.input has within_service_window
  && context.input.within_service_window == true
};
