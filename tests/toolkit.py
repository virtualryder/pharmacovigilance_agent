"""Test helper: load a governed tool's handler by name, from the agent tools or shared controls."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_TOOLS = ROOT / "agents" / "pharmacovigilance" / "tools"
CONTROLS = ROOT / "lib" / "controls"


def load(name):
    for base in (AGENT_TOOLS, CONTROLS):
        p = base / f"{name}.py"
        if p.exists():
            spec = importlib.util.spec_from_file_location(name, p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise FileNotFoundError(name)


def call(name, event):
    return load(name).handler(event, None)
