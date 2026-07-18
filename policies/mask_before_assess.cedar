// mask_before_assess  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS)
// No seriousness assessment on un-masked PHI. assess_seriousness is forbidden unless deidentified == true (set only by mask_pii). forbid wins over any permit.
forbid(
  principal,
  action == AgentCore::Action::"assess-seriousness___assess_seriousness",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/pv-pharmacovigilance-gw"
)
unless { context.input.deidentified == true };
