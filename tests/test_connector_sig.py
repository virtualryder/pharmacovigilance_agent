"""Unit tests for the connector system-of-record's OAuth2 token gate — RS256/JWKS signature
verification plus the claims checks. Dependency-free: a pre-generated RSA JWK and two pre-signed
tokens (one valid, one whose payload was tampered after signing) are embedded as fixtures, so the
pure-Python verifier in lib/connector/sor_api.py is exercised in CI with only the standard library.
The JWKS fetch is monkeypatched to return the fixture key (no network)."""
import importlib.util
import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOR = ROOT / "lib" / "connector" / "sor_api.py"

# --- fixtures: a 2048-bit RSA public key (JWK) and tokens signed by its private half ---
JWK = {"kty": "RSA", "kid": "sor-test-kid", "use": "sig", "alg": "RS256",
       "n": "zw5gFqbITr-rk12mA3SXpquyHqD1HBEgsV0QEvboJwuW9wXgHeORpAP8ZilQKkoHM5ImXdEEzEtS3IAX_56NC_4U08Kuz0LpPATAwVxAemuDqxrMpAM7q6Guyp7KgBpKuAMsYDnerdkBfWY7GrMZm4rzv0NaNMd4fhdIVMFDTE42YQC7Dvq3gKGK8rWQAFAFhBzzIKDM6A81XoYNvvI1uPHouozdYuIt-cRSbpMqIRFPzyqlnNJI8VuqCl1ckdCSXOtrsBdfGnifQuY6oO91DOcFY5QtB3rk4r0X788MMLFPtRp4EFTjFzHw5IsY3BQynC_4sMVoaoucwoe342nNaw",
       "e": "AQAB"}
KID = "sor-test-kid"
ISS = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_FIXTURE"
CLIENT = "fixture-client"
SCOPE = "px-sor/read"
VALID = "eyJhbGciOiAiUlMyNTYiLCAia2lkIjogInNvci10ZXN0LWtpZCIsICJ0eXAiOiAiSldUIn0.eyJpc3MiOiAiaHR0cHM6Ly9jb2duaXRvLWlkcC51cy1lYXN0LTEuYW1hem9uYXdzLmNvbS91cy1lYXN0LTFfRklYVFVSRSIsICJ0b2tlbl91c2UiOiAiYWNjZXNzIiwgImNsaWVudF9pZCI6ICJmaXh0dXJlLWNsaWVudCIsICJzY29wZSI6ICJweC1zb3IvcmVhZCIsICJleHAiOiA5OTk5OTk5OTk5fQ.CkchbPXU_Be4adiFwlUHL7lorVyyepKO25oO2EJNRKDhA4W-yPSk0uToI7W2FytYLPsuv0bnHLxa_CFfLt46NT_iyWjU1NQXBV1o4bmJ6ufydiNu_D9JaLvHFTGVkDBZ3MqMkiChvrhgGA2P108yNFlCGferd_MCucPsrbEmOkZtS4m5-CgLXM5kuZfgAmX35jBE4K9Qj-KGOye5nYKRQyiJfBg5JfjIkXtPpqCbz5T8ymwMXuh6ooXYFbPBtjlI-8pwkCSRcEl0RH39m-tOTMH5mFXP-3bOb43pokzrhPUvEBD-JFEzgy3DU2MPRoTOZ4wPSp8lJR6F7YY-7dUSbQ"
TAMPERED = "eyJhbGciOiAiUlMyNTYiLCAia2lkIjogInNvci10ZXN0LWtpZCIsICJ0eXAiOiAiSldUIn0.eyJpc3MiOiAiaHR0cHM6Ly9jb2duaXRvLWlkcC51cy1lYXN0LTEuYW1hem9uYXdzLmNvbS91cy1lYXN0LTFfRklYVFVSRSIsICJ0b2tlbl91c2UiOiAiYWNjZXNzIiwgImNsaWVudF9pZCI6ICJmaXh0dXJlLWNsaWVudCIsICJzY29wZSI6ICJweC1zb3IvYWRtaW4iLCAiZXhwIjogOTk5OTk5OTk5OX0.CkchbPXU_Be4adiFwlUHL7lorVyyepKO25oO2EJNRKDhA4W-yPSk0uToI7W2FytYLPsuv0bnHLxa_CFfLt46NT_iyWjU1NQXBV1o4bmJ6ufydiNu_D9JaLvHFTGVkDBZ3MqMkiChvrhgGA2P108yNFlCGferd_MCucPsrbEmOkZtS4m5-CgLXM5kuZfgAmX35jBE4K9Qj-KGOye5nYKRQyiJfBg5JfjIkXtPpqCbz5T8ymwMXuh6ooXYFbPBtjlI-8pwkCSRcEl0RH39m-tOTMH5mFXP-3bOb43pokzrhPUvEBD-JFEzgy3DU2MPRoTOZ4wPSp8lJR6F7YY-7dUSbQ"


@pytest.fixture(autouse=True)
def _verify_signature_on():
    # sor_api reads VERIFY_SIGNATURE at import time; force it ON for this file so the
    # RS256/JWKS path is exercised regardless of env another test module leaked at
    # collection time, and restore the prior value afterwards (order-independent).
    prev = os.environ.get("VERIFY_SIGNATURE")
    os.environ["VERIFY_SIGNATURE"] = "true"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("VERIFY_SIGNATURE", None)
        else:
            os.environ["VERIFY_SIGNATURE"] = prev


def _load():
    os.environ.update({"EXPECTED_ISS": ISS, "EXPECTED_CLIENT_ID": CLIENT,
                       "REQUIRED_SCOPE": SCOPE, "SOR_LABEL": "MOCK-TEST-SOR"})
    spec = importlib.util.spec_from_file_location("sor_api_under_test", SOR)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._fetch_jwks = lambda url: {KID: JWK}          # no network in CI
    m._JWKS_CACHE.update({"url": None, "keys": {}, "exp": 0})
    return m


def _call(m, token=None):
    headers = {"authorization": "Bearer " + token} if token else {}
    r = m.handler({"headers": headers, "queryStringParameters": {"case_id": "C1"}}, None)
    return r["statusCode"], json.loads(r["body"])


def test_valid_token_signature_verifies():
    m = _load()
    code, body = _call(m, VALID)
    assert code == 200
    assert body["verified"] is True
    assert body["token_signature"] == "RS256/JWKS verified"


def test_tampered_payload_rejected():
    # valid RSA signature, but the payload was altered after signing -> signature must not verify
    m = _load()
    code, body = _call(m, TAMPERED)
    assert code == 401
    assert "signature" in json.dumps(body).lower()


def test_missing_token_rejected():
    m = _load()
    code, body = _call(m, None)
    assert code == 401


def test_pure_python_rs256_primitive():
    # the PKCS#1 v1.5 verifier accepts the correct message and rejects a flipped bit
    m = _load()
    ok, reason = m._verify_signature(VALID)
    assert ok is True and reason == "ok"
    bad = TAMPERED
    ok2, _ = m._verify_signature(bad)
    assert ok2 is False
