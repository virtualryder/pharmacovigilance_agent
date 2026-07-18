// no_self_causality_commit  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS)
// Committing a causality/reportability determination is a CONSEQUENTIAL, senior-safety-physician-only
// action. The agent can never call commit_causality; forbidden outright — the same deny-by-default
// pattern as no_self_submit. Every new high-risk action gets its own forbid.
forbid(
  principal,
  action == AgentCore::Action::"pv-core___commit_causality",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/pv-pharmacovigilance-gw"
);
