// mask_before_causality  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS)
// No causality/reportability determination on un-masked PHI. record_causality is forbidden unless deidentified == true (set only by mask_pii).
forbid(
  principal,
  action == AgentCore::Action::"record-causality___record_causality",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/pv-pharmacovigilance-gw"
)
unless { context.input.deidentified == true };
