// consent_purpose_before_assess_seriousness  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS  ·  scope: perimeter)
// CONSENT + PURPOSE (#161) on the governed decision. ICSR handling is regulated processing of identifiable patient data: the reporter's / patient's recorded consent (or the lawful basis captured at intake) and an authorized purpose (pharmacovigilance or signal_detection) must both be on record before the seriousness assessment that drives expedited reporting.
// consent/purpose are OPTIONAL context.input fields, so each access is presence-guarded
// (`context.input has ...`) - the GA engine rejects an unguarded optional-attribute access and a missing
// field fails the guard, so the forbid fires (fail-closed). Both values are AUTHORITATIVE: the gateway
// interceptor strips any caller-supplied copy and the authoritative_context resolver injects them from
// the server-side authz store (the record ingest wrote from the verified operator's attestation).
// These are COARSE gateway gates layered on the server-side controls; forbid wins over pv_reviewer_permit.
forbid(
  principal,
  action == AgentCore::Action::"assess-seriousness___assess_seriousness",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/pv-safety-gw"
)
unless {
  context.input has consent && context.input.consent == true
  && context.input has purpose
  && (context.input.purpose == "pharmacovigilance" || context.input.purpose == "signal_detection")
};
