// require_tenant  (kind: forbid  ·  validation: IGNORE_ALL_FINDINGS  ·  scope: multitenant)
// Hybrid multi-tenant (phase 108): a principal whose identity carries NO tenant may not call ANY
// governed tool. Forbid wins, so no permit can override it - an un-tenanted identity is refused at the
// gateway before the interceptor or any target runs. Cross-tenant access is impossible by construction:
// the gateway REQUEST interceptor derives the tenant from the caller's OWN validated identity (never
// from the request) and the target routes to THAT tenant's store, so a caller can only ever reach its
// own tenant. ATTACHED ONLY IN MULTI-TENANT DEPLOYMENTS (silo principals carry no tenant tag).
forbid(
  principal,
  action,
  resource is AgentCore::Gateway
)
unless {
  principal.hasTag("custom:tenant")
  || (principal.hasTag("cognito:groups") && principal.getTag("cognito:groups") like "*tenant_*")
};
