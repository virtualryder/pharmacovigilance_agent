// mask_before_draft  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS)
// The model only ever sees de-identified text. draft_narrative is forbidden unless deidentified == true (set only by mask_pii).
forbid(
  principal,
  action == AgentCore::Action::"pv-core___draft_narrative",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/pv-pharmacovigilance-gw"
)
unless { context.input.deidentified == true };
