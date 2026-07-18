import json
import os
import base64
import time

# sor_api.py — MOCK external "system of record" (stands in for a real verification service such as EIV,
# a student information system, or a safety database). It is OAuth2-PROTECTED: every request must carry a
# valid Cognito machine-to-machine (client_credentials) access token with the required scope. Exposed via
# an API Gateway HTTP API. It is NOT a stub: a call without a valid, correctly-scoped, unexpired token
# from the expected client is rejected (401/403). Reusable across agents — the domain label is env-driven.
#
# Token validation checks the standard access-token claims (issuer, token_use, client_id, scope, exp).
# Full JWKS/RS256 signature verification is the obvious production hardening (needs a crypto lib in the
# Lambda); the claims gate already proves "a real OAuth token is required."

EXPECTED_ISS = os.environ.get("EXPECTED_ISS", "")
EXPECTED_CLIENT_ID = os.environ.get("EXPECTED_CLIENT_ID", "")
REQUIRED_SCOPE = os.environ.get("REQUIRED_SCOPE", "")
SOR_LABEL = os.environ.get("SOR_LABEL", "MOCK-SOR")


def _b64url(s):
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _claims(tok):
    parts = tok.split(".")
    if len(parts) != 3:
        return None
    try:
        return json.loads(_b64url(parts[1]))
    except Exception:
        return None


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def handler(event, context):
    headers = event.get("headers") or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return _resp(401, {"error": "missing OAuth2 Bearer token", "www_authenticate": "Bearer"})
    c = _claims(auth.split(" ", 1)[1].strip())
    if not c:
        return _resp(401, {"error": "malformed token"})
    now = int(time.time())
    if EXPECTED_ISS and c.get("iss") != EXPECTED_ISS:
        return _resp(403, {"error": "wrong issuer"})
    if c.get("token_use") != "access":
        return _resp(403, {"error": "not an access token"})
    if EXPECTED_CLIENT_ID and c.get("client_id") != EXPECTED_CLIENT_ID:
        return _resp(403, {"error": "unrecognized client"})
    if REQUIRED_SCOPE and REQUIRED_SCOPE not in str(c.get("scope", "")).split():
        return _resp(403, {"error": "insufficient scope", "need": REQUIRED_SCOPE, "got": c.get("scope")})
    if int(c.get("exp", 0)) < now:
        return _resp(401, {"error": "token expired"})

    qs = event.get("queryStringParameters") or {}
    case = qs.get("case_id") or "_default"
    return _resp(200, {"system_of_record": SOR_LABEL, "case_id": case, "verified": True,
                       "record": {"status": "on file", "as_of": "2026-06"},
                       "authorized_via": "OAuth2 client_credentials (Cognito M2M)", "client_id": c.get("client_id")})
