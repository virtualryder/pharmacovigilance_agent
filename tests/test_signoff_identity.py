"""P0-5 — approver/requester identity is derived from a cryptographically-validated Cognito access token,
never from an event-body string. Covers: the claims gate (issuer/token_use/client/exp/group), the pure
RS256/JWKS signature primitive (reusing the connector fixture), and the sign-off handlers rejecting a
spoofed approver + enforcing separation of duties on the verified username."""
import base64
import importlib.util
import json
import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTROLS = ROOT / "lib" / "controls"
if str(CONTROLS) not in sys.path:
    sys.path.insert(0, str(CONTROLS))

ISS = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST"
os.environ.update({"VERIFY_SIGNATURE": "false", "CLIENT_ID": "test-client",
                   "REVIEWER_GROUP": "pv_reviewer", "COGNITO_ISS": ISS})

import identity  # noqa: E402


@pytest.fixture(autouse=True)
def _claims_only_signature():
    # identity.verify_access_token reads VERIFY_SIGNATURE at call time; these fixtures use
    # unsigned tokens, so force claims-only per test regardless of env leaked by other modules.
    os.environ["VERIFY_SIGNATURE"] = "false"
    yield


def _b64url(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _tok(claims):
    return _b64url({"alg": "RS256", "kid": "k"}) + "." + _b64url(claims) + ".sig"


def _claims(**over):
    c = {"iss": ISS, "token_use": "access", "client_id": "test-client",
         "exp": int(time.time()) + 3600, "username": "pv_reviewer", "cognito:groups": ["pv_reviewer"]}
    c.update(over)
    return c


# ---- claims gate ----

def test_valid_token_verifies():
    claims, err = identity.verify_access_token(_tok(_claims()))
    assert err is None and identity.identity_of(claims) == "pv_reviewer"


def test_no_token_rejected():
    assert identity.verify_access_token(None)[1].startswith("no access token")


def test_wrong_issuer_rejected():
    assert "issuer" in identity.verify_access_token(_tok(_claims(iss="https://evil.example")))[1]


def test_not_access_token_rejected():
    assert "access token" in identity.verify_access_token(_tok(_claims(token_use="id")))[1]


def test_wrong_client_rejected():
    assert "client" in identity.verify_access_token(_tok(_claims(client_id="someone-else")))[1]


def test_expired_rejected():
    assert "expired" in identity.verify_access_token(_tok(_claims(exp=int(time.time()) - 10)))[1]


def test_not_in_group_rejected():
    assert "group" in identity.verify_access_token(_tok(_claims(**{"cognito:groups": ["interns"]})))[1]


def test_group_check_optional():
    claims, err = identity.verify_access_token(_tok(_claims(**{"cognito:groups": ["interns"]})), require_group=False)
    assert err is None and identity.identity_of(claims) == "pv_reviewer"


# ---- RS256/JWKS signature primitive (reuse the connector's pre-signed fixture) ----
JWK = {"kty": "RSA", "kid": "sor-test-kid", "use": "sig", "alg": "RS256",
       "n": "zw5gFqbITr-rk12mA3SXpquyHqD1HBEgsV0QEvboJwuW9wXgHeORpAP8ZilQKkoHM5ImXdEEzEtS3IAX_56NC_4U08Kuz0LpPATAwVxAemuDqxrMpAM7q6Guyp7KgBpKuAMsYDnerdkBfWY7GrMZm4rzv0NaNMd4fhdIVMFDTE42YQC7Dvq3gKGK8rWQAFAFhBzzIKDM6A81XoYNvvI1uPHouozdYuIt-cRSbpMqIRFPzyqlnNJI8VuqCl1ckdCSXOtrsBdfGnifQuY6oO91DOcFY5QtB3rk4r0X788MMLFPtRp4EFTjFzHw5IsY3BQynC_4sMVoaoucwoe342nNaw",
       "e": "AQAB"}
VALID = "eyJhbGciOiAiUlMyNTYiLCAia2lkIjogInNvci10ZXN0LWtpZCIsICJ0eXAiOiAiSldUIn0.eyJpc3MiOiAiaHR0cHM6Ly9jb2duaXRvLWlkcC51cy1lYXN0LTEuYW1hem9uYXdzLmNvbS91cy1lYXN0LTFfRklYVFVSRSIsICJ0b2tlbl91c2UiOiAiYWNjZXNzIiwgImNsaWVudF9pZCI6ICJmaXh0dXJlLWNsaWVudCIsICJzY29wZSI6ICJweC1zb3IvcmVhZCIsICJleHAiOiA5OTk5OTk5OTk5fQ.CkchbPXU_Be4adiFwlUHL7lorVyyepKO25oO2EJNRKDhA4W-yPSk0uToI7W2FytYLPsuv0bnHLxa_CFfLt46NT_iyWjU1NQXBV1o4bmJ6ufydiNu_D9JaLvHFTGVkDBZ3MqMkiChvrhgGA2P108yNFlCGferd_MCucPsrbEmOkZtS4m5-CgLXM5kuZfgAmX35jBE4K9Qj-KGOye5nYKRQyiJfBg5JfjIkXtPpqCbz5T8ymwMXuh6ooXYFbPBtjlI-8pwkCSRcEl0RH39m-tOTMH5mFXP-3bOb43pokzrhPUvEBD-JFEzgy3DU2MPRoTOZ4wPSp8lJR6F7YY-7dUSbQ"
TAMPERED = "eyJhbGciOiAiUlMyNTYiLCAia2lkIjogInNvci10ZXN0LWtpZCIsICJ0eXAiOiAiSldUIn0.eyJpc3MiOiAiaHR0cHM6Ly9jb2duaXRvLWlkcC51cy1lYXN0LTEuYW1hem9uYXdzLmNvbS91cy1lYXN0LTFfRklYVFVSRSIsICJ0b2tlbl91c2UiOiAiYWNjZXNzIiwgImNsaWVudF9pZCI6ICJmaXh0dXJlLWNsaWVudCIsICJzY29wZSI6ICJweC1zb3IvYWRtaW4iLCAiZXhwIjogOTk5OTk5OTk5OX0.CkchbPXU_Be4adiFwlUHL7lorVyyepKO25oO2EJNRKDhA4W-yPSk0uToI7W2FytYLPsuv0bnHLxa_CFfLt46NT_iyWjU1NQXBV1o4bmJ6ufydiNu_D9JaLvHFTGVkDBZ3MqMkiChvrhgGA2P108yNFlCGferd_MCucPsrbEmOkZtS4m5-CgLXM5kuZfgAmX35jBE4K9Qj-KGOye5nYKRQyiJfBg5JfjIkXtPpqCbz5T8ymwMXuh6ooXYFbPBtjlI-8pwkCSRcEl0RH39m-tOTMH5mFXP-3bOb43pokzrhPUvEBD-JFEzgy3DU2MPRoTOZ4wPSp8lJR6F7YY-7dUSbQ"


def test_rs256_signature_primitive(monkeypatch):
    monkeypatch.setattr(identity, "_jwk_for", lambda kid, force=False: JWK)
    ok, reason = identity._verify_signature(VALID)
    assert ok is True and reason == "ok"
    ok2, _ = identity._verify_signature(TAMPERED)
    assert ok2 is False


# ---- sign-off handlers ----
def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_ut", CONTROLS / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Ctx:
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:x-approve"


class _FakeTable:
    def __init__(self, item):
        self._item = item
        self.consumed = False

    def get_item(self, Key):
        return {"Item": self._item} if self._item else {}

    def update_item(self, **kw):
        self.consumed = True
        return {}


class _FakeSFN:
    def __init__(self):
        self.released = False

    def send_task_success(self, taskToken, output):
        self.released = True


def _fake_boto3(table_item):
    tbl = _FakeTable(table_item)
    sfn = _FakeSFN()

    class _B:
        @staticmethod
        def resource(*a, **k):
            return type("R", (), {"Table": staticmethod(lambda n: tbl)})()

        @staticmethod
        def client(*a, **k):
            return sfn
    return _B, tbl, sfn


def test_approve_rejects_spoofed_string(monkeypatch):
    A = _load("approve_signoff")
    rec = {}
    monkeypatch.setattr(A.evidence, "record_event", lambda ev, ctx, source=None: rec.update(ev) or {"stored": True})
    # a caller-supplied approver string with NO token must be rejected (and recorded DENIED)
    r = A.handler({"case_id": "C1", "approver": "dr_spoof"}, _Ctx())
    assert r["approved"] is False
    assert "not verified" in r["reason"]
    assert rec.get("phase") == "DENIED"


def test_approve_separation_of_duties(monkeypatch):
    A = _load("approve_signoff")
    monkeypatch.setattr(A.evidence, "record_event", lambda ev, ctx, source=None: {"stored": True})
    # verified approver == requester -> SoD denial
    monkeypatch.setattr(A.identity, "verify_access_token",
                        lambda t, require_group=True: ({"username": "alice", "cognito:groups": ["pv_reviewer"]}, None))
    B, tbl, sfn = _fake_boto3({"case_id": "C1", "requester": "alice", "task_token": "tok", "status": "PENDING"})
    monkeypatch.setattr(A, "boto3", B)
    r = A.handler({"case_id": "C1", "access_token": "x"}, _Ctx())
    assert r["approved"] is False and "separation-of-duties" in r["reason"]
    assert sfn.released is False


def test_approve_success_with_verified_token(monkeypatch):
    A = _load("approve_signoff")
    monkeypatch.setattr(A.evidence, "record_event", lambda ev, ctx, source=None: {"stored": True})
    monkeypatch.setattr(A.identity, "verify_access_token",
                        lambda t, require_group=True: ({"username": "bob", "cognito:groups": ["pv_reviewer"]}, None))
    B, tbl, sfn = _fake_boto3({"case_id": "C1", "requester": "alice", "task_token": "tok", "status": "PENDING"})
    monkeypatch.setattr(A, "boto3", B)
    r = A.handler({"case_id": "C1", "access_token": "bob-token"}, _Ctx())
    assert r["approved"] is True and r["approver"] == "bob"
    assert tbl.consumed is True and sfn.released is True


def test_request_rejects_unverified(monkeypatch):
    R = _load("request_signoff")
    monkeypatch.setattr(R.evidence, "record_event", lambda ev, ctx, source=None: {"stored": True})
    r = R.handler({"case_id": "C1", "requester": "spoof"}, _Ctx())
    assert r["requested"] is False and "not verified" in r["error"]


def test_request_uses_verified_identity(monkeypatch):
    R = _load("request_signoff")
    monkeypatch.setattr(R.evidence, "record_event", lambda ev, ctx, source=None: {"stored": True})
    monkeypatch.setattr(R.identity, "verify_access_token",
                        lambda t, require_group=True: ({"username": "alice", "cognito:groups": ["pv_reviewer"]}, None))
    started = {}

    class _SFN:
        def start_execution(self, stateMachineArn, input):
            started["input"] = json.loads(input)
            return {"executionArn": "arn:aws:states:us-east-1:123:execution:x:1"}

    monkeypatch.setattr(R, "boto3", type("B", (), {"client": staticmethod(lambda *a, **k: _SFN())}))
    r = R.handler({"case_id": "C1", "access_token": "alice-token", "requester": "ignored-spoof"}, _Ctx())
    assert r["requested"] is True and r["requester"] == "alice"
    assert started["input"]["requester"] == "alice"   # verified identity, not the body 'requester'
