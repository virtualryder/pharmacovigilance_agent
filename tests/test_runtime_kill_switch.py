"""Task 127 — the RUNTIME analog of governed-core kill_switch (lib/runtime/agent.py carries only itself
in the image, so the containment rules are restated there and proven here against the same cases as
governed-core tests/test_kill_switch.py (a), (d), (e), (f)):

  * not configured => None; disengaged record => None;
  * engaged => the invocation is refused BEFORE the tenant is derived or the gateway is contacted, with
    the structured refusal (guardrail_action KILL_SWITCH) and one aegis.kill_switch log line;
  * the botocore hook refuses the NEXT model call of an in-flight session (KillSwitchEngaged);
  * fail-closed: unreadable / malformed => engaged; many-to-one; TTL cache bounds reads + time-to-effect.

The AgentCore / Strands / MCP SDKs are stubbed at import (no runtime venv needed in CI)."""
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib" / "runtime"))


def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _App:
    def entrypoint(self, fn):
        return fn

    def run(self):
        pass


_stub("bedrock_agentcore"); _stub("bedrock_agentcore.runtime", BedrockAgentCoreApp=_App)
_stub("strands", Agent=object); _stub("strands.models", BedrockModel=object)
_stub("strands.tools"); _stub("strands.tools.mcp", MCPClient=object)
_stub("mcp"); _stub("mcp.client"); _stub("mcp.client.streamable_http", streamablehttp_client=lambda *a, **k: None)

import agent  # noqa: E402

PARAM = "/pv-x-pharmacovigilance/kill-switch"


class _Ssm:
    def __init__(self, values):
        self.values, self.reads, self.fail = values, [], {}

    def get_parameter(self, Name):
        self.reads.append(Name)
        if Name in self.fail:
            raise self.fail[Name]
        if Name not in self.values:
            raise RuntimeError("ParameterNotFound")
        return {"Parameter": {"Value": self.values[Name]}}


@pytest.fixture
def ssm(monkeypatch):
    s = _Ssm({PARAM: json.dumps({"engaged": False, "actor": "", "reason": "", "at": 0})})
    monkeypatch.setattr(agent.boto3, "client", lambda *a, **k: s)
    monkeypatch.setattr(agent, "KILL_SWITCH_PARAMS", [PARAM])
    monkeypatch.setattr(agent, "KILL_SWITCH_TTL", 15)
    agent._ks_cache.clear()
    yield s
    agent._ks_cache.clear()


def test_not_configured_or_disengaged(ssm, monkeypatch):
    monkeypatch.setattr(agent, "KILL_SWITCH_PARAMS", [])
    assert agent._kill_switch() is None and ssm.reads == []
    monkeypatch.setattr(agent, "KILL_SWITCH_PARAMS", [PARAM])
    assert agent._kill_switch() is None


def test_engaged_refuses_the_invocation_before_anything_else(ssm, monkeypatch, caplog):
    ssm.values[PARAM] = json.dumps({"engaged": True, "actor": "arn:aws:iam::123456789012:user/alice",
                                    "reason": "SEV-1", "at": 1})
    touched = []
    monkeypatch.setattr(agent, "_session_tenant", lambda tok: touched.append("tenant"))
    monkeypatch.setattr(agent, "_gateway_url", lambda: touched.append("gateway"))
    out = agent.invoke({"access_token": "t.t.t", "case_id": "C-1", "requester": "r"})
    assert out["refused"] and out["guardrail_action"] == "KILL_SWITCH" and out["engaged_reason"] == "SEV-1"
    assert out["engaged_by"].endswith("user/alice") and out["governed"]
    assert touched == []                                   # nothing downstream was touched
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]
    assert any(l.get("aegis") == "kill_switch" and l.get("outcome") == "denied:kill_switch" for l in lines)


def test_hook_stops_an_in_flight_session_at_the_next_model_call(ssm):
    session = agent._bedrock_session({"tenant": "cw-a", "session.id": "s", "case_id": "C", "requester": "r"})
    params = {}
    session.aegis_inject(params)                           # disengaged: metadata injected, call proceeds
    assert params["requestMetadata"]["tenant"] == "cw-a"
    ssm.values[PARAM] = json.dumps({"engaged": True, "actor": "a", "reason": "stop now", "at": 2})
    agent._ks_cache.clear()
    with pytest.raises(agent.KillSwitchEngaged):
        session.aegis_inject({})


@pytest.mark.parametrize("value", ["", "{bad", '{"engaged": "yes"}', "[]"])
def test_malformed_fails_closed(ssm, value):
    ssm.values[PARAM] = value
    assert agent._kill_switch()["engaged"] is True


def test_unreadable_fails_closed(ssm):
    ssm.fail[PARAM] = RuntimeError("AccessDeniedException")
    s = agent._kill_switch()
    assert s["engaged"] is True and s["reason"].startswith("unreadable")


def test_many_to_one_and_ttl(ssm, monkeypatch):
    monkeypatch.setattr(agent, "KILL_SWITCH_PARAMS", [PARAM, "/aegis/kill-switch"])
    ssm.values["/aegis/kill-switch"] = json.dumps({"engaged": False})
    for _ in range(20):
        assert agent._kill_switch(now=1000.0) is None
    assert ssm.reads.count(PARAM) == 1 and ssm.reads.count("/aegis/kill-switch") == 1
    ssm.values["/aegis/kill-switch"] = json.dumps({"engaged": True, "reason": "platform-wide"})
    assert agent._kill_switch(now=1010.0) is None         # cached
    assert agent._kill_switch(now=1016.0)["source"] == "/aegis/kill-switch"


def test_wrapped_hook_exception_counts_as_contained(ssm):
    """Strands wraps the hook's KillSwitchEngaged in its own EventLoopException (seen live); the cause
    chain, or the switch itself, must still classify the stop as containment."""
    class EventLoopException(Exception):
        pass
    inner = agent.KillSwitchEngaged("stop now")
    wrapped = EventLoopException("stop now")
    wrapped.__cause__ = inner
    assert agent._contained(wrapped)
    assert not agent._contained(RuntimeError("unrelated"))           # switch disengaged, no cause
    ssm.values[PARAM] = json.dumps({"engaged": True, "actor": "a", "reason": "r", "at": 1})
    agent._ks_cache.clear()
    assert agent._contained(RuntimeError("anything, while engaged"))


# ---- task 128: the runtime budget hooks (governed-core budget.py stubbed at the module seam) --------
def test_budget_reserve_before_and_commit_after_each_model_call(monkeypatch):
    calls = []

    class _B:
        class BudgetExceeded(Exception):
            def __init__(self, decision):
                self.decision = decision
                super().__init__(decision["reason"])
        GUARDRAIL_ACTION = "BUDGET"

        @staticmethod
        def reserve(tenant, tokens=None):
            calls.append(("reserve", tenant)); return {"reserved": 4000}

        @staticmethod
        def commit(tenant, usage, model_id="", reserved=0):
            calls.append(("commit", tenant, usage["inputTokens"], usage["outputTokens"], reserved)); return {"metered": True, "tokens": 30}

        @staticmethod
        def check(tenant):
            return None

        @staticmethod
        def log_line(d, component=""):
            pass

    monkeypatch.setattr(agent, "_budget", _B)
    monkeypatch.setattr(agent, "KILL_SWITCH_PARAMS", [])
    session = agent._bedrock_session({"tenant": "cw-a", "session.id": "s", "case_id": "C", "requester": "r"})
    session.aegis_inject({})
    session.aegis_commit({"usage": {"inputTokens": 20, "outputTokens": 10}})
    assert calls == [("reserve", "cw-a"), ("commit", "cw-a", 20, 10, 4000)]


def test_budget_refusal_stops_the_next_model_call_and_is_classified(monkeypatch):
    class _B:
        class BudgetExceeded(Exception):
            def __init__(self, decision):
                self.decision = decision
                super().__init__(decision["reason"])
        GUARDRAIL_ACTION = "BUDGET"

        @staticmethod
        def reserve(tenant, tokens=None):
            raise _B.BudgetExceeded({"tenant": tenant, "reason": "cap reached", "cap_tokens": 1000})

        @staticmethod
        def log_line(d, component=""):
            pass

    monkeypatch.setattr(agent, "_budget", _B)
    monkeypatch.setattr(agent, "KILL_SWITCH_PARAMS", [])
    session = agent._bedrock_session({"tenant": "cw-b", "session.id": "s", "case_id": "C", "requester": "r"})
    with pytest.raises(agent.BudgetExceeded) as ei:
        session.aegis_inject({})
    # Strands wraps it; the runtime finds it in the cause chain and returns the structured refusal
    wrapped = RuntimeError("EventLoopException"); wrapped.__cause__ = ei.value
    b = agent._budget_stopped(wrapped)
    assert b is not None and b.decision["cap_tokens"] == 1000
    out = agent._budget_refusal(b.decision, {"case_id": "C"})
    assert out["refused"] and out["guardrail_action"] == "BUDGET" and out["tenant"] == "cw-b"
