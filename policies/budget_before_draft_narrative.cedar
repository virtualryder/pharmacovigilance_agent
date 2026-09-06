// budget_before_draft_narrative  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS  ·  scope: perimeter)
// BUDGET (#161) on the model-spending drafter: the tool call is refused unless the live per-tenant
// spend meter is under cap. budget_ok is interceptor-injected from the REAL meter (the caller cannot
// assert it); optional, so it is presence-guarded and a missing value fails closed. This is the coarse
// GATEWAY gate - the authoritative cap is the reserve-before/commit-after meter inside the drafter and
// the Runtime, which refuses mid-session when the cap is reached.
forbid(
  principal,
  action == AgentCore::Action::"pv-core___draft_narrative",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/pv-safety-gw"
)
unless {
  context.input has budget_ok && context.input.budget_ok == true
};
