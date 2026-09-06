// require_entitlement  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS  ·  scope: perimeter)
// ENTITLEMENT / zero-default tools (#160): a principal with NO explicit tool grant may call NOTHING -
// being in the pv_reviewer role group is not enough. The grant is EITHER a non-empty per-user
// custom:tools claim (the authoritative per-user entitlement, injected into the access token by the
// pool's pre-token-generation trigger) OR membership in the tools_granted group (the group form, which
// rides in cognito:groups - always present in a Cognito access token). Either satisfies the unless;
// with neither, tools are zero. Forbid wins over pv_reviewer_permit, so tools are opt-in per identity,
// never default-to-all. Defense in depth for the gateway REQUEST interceptor, which already drops the
// advertised tool list when the entitlement is missing. Attaches only with -c perimeter=1.
forbid(
  principal,
  action,
  resource is AgentCore::Gateway
)
unless {
  (principal.hasTag("custom:tools") && principal.getTag("custom:tools") != "")
  || (principal.hasTag("cognito:groups") && principal.getTag("cognito:groups") like "*tools_granted*")
};
