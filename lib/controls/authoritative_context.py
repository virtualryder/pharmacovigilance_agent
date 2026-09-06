"""authoritative_context — pharmacovigilance pack resolver for the Cedar authorization-context fields the CALLER
MUST NOT control (deep-dive #3, end-to-end).

The governed-core gateway interceptor (tenant_interceptor) STRIPS any caller-supplied consent / purpose /
budget_ok / within_service_window and then calls `resolve(args, tenant)` to inject only what an
AUTHORITATIVE source vouches for. This module supplies the two the interceptor cannot compute from the
clock or the meter:

  * consent  — TRUE only if an authoritative, unexpired consent record exists for the case in the authz
               store (written by an authorized workflow / caseworker action). A caller-asserted
               `consent: true` is discarded by the interceptor; only a real record produces consent=true.
  * purpose  — the case's AUTHORIZED purpose, bound to the case when it was authorized — NOT the caller's
               free-text request field.

FAIL-CLOSED: no case_id, no record, an unreadable table, or a malformed record -> the field is simply not
returned, so Cedar sees it UNSET and DENIES (the perimeter policies are presence-guarded). This module
never raises into the request path and never grants on error.

Store: DynamoDB, partition key `case_id`, item shape
    {case_id, consent (bool), authorized_purpose (str), expires_at (epoch, optional TTL)}.
Silo: env AUTHZ_TABLE. Multi-tenant: AUTHZ_TABLE_TEMPLATE.format(tenant=<tenant>) routes to the acting
tenant's own authz table (the interceptor passes the verified tenant).
"""
import os
import time


def _table_name(tenant):
    tmpl = os.environ.get("AUTHZ_TABLE_TEMPLATE", "")
    if tenant and tmpl and "{tenant}" in tmpl:
        return tmpl.format(tenant=tenant)
    return os.environ.get("AUTHZ_TABLE", "")


def _get_record(table, case_id):
    import boto3
    ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return ddb.Table(table).get_item(Key={"case_id": case_id}).get("Item") or {}


def resolve(args, tenant):
    """Return the server-authoritative Cedar context fields for this call, or {} to leave them UNSET
    (fail-closed). Called by the interceptor AFTER caller-supplied copies are stripped."""
    out = {}
    case_id = args.get("case_id") if isinstance(args, dict) else None
    if not isinstance(case_id, str) or not case_id:
        return out                                  # no case -> nothing authoritative -> Cedar denies
    table = _table_name(tenant)
    if not table:
        return out                                  # no authz store configured -> fail-closed
    try:
        rec = _get_record(table, case_id)
    except Exception:
        return out                                  # unreadable -> never grant on error (fail-closed)
    if not rec:
        return out
    # CONSENT: an explicit True, and not past its expiry (if the record carries one).
    if rec.get("consent") is True:
        exp = rec.get("expires_at")
        try:
            fresh = (exp is None) or (int(exp) > int(time.time()))
        except (TypeError, ValueError):
            fresh = False                           # malformed expiry -> treat as not consented
        if fresh:
            out["consent"] = True
    # PURPOSE: the case's AUTHORIZED purpose, bound at authorization time (a non-empty string).
    p = rec.get("authorized_purpose")
    if isinstance(p, str) and p:
        out["purpose"] = p
    return out
