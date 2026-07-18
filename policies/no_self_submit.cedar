// no_self_submit  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS)
// Submitting an ICSR to the regulator is a HUMAN-ONLY action reachable ONLY through the Step Functions
// sign-off gate. The agent can never call finalize_submission — forbidden outright and hidden from the
// agent's tool list. forbid wins over any permit.
forbid(
  principal,
  action == AgentCore::Action::"pv-core___finalize_submission",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/pv-pharmacovigilance-gw"
);
