#!/usr/bin/env python3
"""Invoke the AgentCore Runtime as a Cognito user with an EXPLICIT runtime session id (phase 110).

The session id header is what AgentCore turns into the mandatory `session.id` span attribute; the
agent binds it (plus the derived tenant + case_id) onto every span, every Converse requestMetadata and
- via OTEL baggage -> the gateway interceptor -> __aegis_trace - every tool call and WORM record.
Direct HTTPS data-plane call (the runtime authorizes the human's JWT, not SigV4).

Usage: python scripts/rt_invoke.py --runtime-arn <arn> --token <access token> --case-id C-1 [--session-id ...]"""
import argparse
import json
import sys
import urllib.parse
import uuid

import requests


def invoke(runtime_arn, token, case_id, requester, session_id=None, region="us-east-1", prompt=None, timeout=600):
    session_id = session_id or ("aegis-" + uuid.uuid4().hex)          # AgentCore requires >= 33 chars
    url = "https://bedrock-agentcore.%s.amazonaws.com/runtimes/%s/invocations?qualifier=DEFAULT" % (
        region, urllib.parse.quote(runtime_arn, safe=""))
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
               "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id}
    body = {"access_token": token, "case_id": case_id, "requester": requester}
    if prompt:
        body["prompt"] = prompt
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = r.text[:2000]
    return {"session_id": session_id, "status": r.status_code, "response": payload,
            "trace_id_header": r.headers.get("X-Amzn-Trace-Id"), "request_id": r.headers.get("x-amzn-RequestId")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-arn", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--requester", default="caseworker")
    ap.add_argument("--session-id", default="")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--prompt", default="")
    a = ap.parse_args()
    out = invoke(a.runtime_arn, a.token, a.case_id, a.requester, a.session_id or None, a.region, a.prompt or None)
    print(json.dumps(out, indent=1, default=str))
    sys.exit(0 if out["status"] == 200 else 1)


if __name__ == "__main__":
    main()
