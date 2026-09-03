#!/usr/bin/env python3
"""CDK app — the PRIMARY customer deployment path for the Pharmacovigilance ICSR Intake Assistant (P0-5).

Replaces the imperative shell engine for customer deployments with reviewable, parameterized IaC:
explicit IAM, exact-ARN outputs (P0-7), configurable audit retention profiles incl. COMPLIANCE (P0-12),
no built-in users or default passwords (P0-6 — identity is a federation-ready pool only), a
sanitized-artifacts store (P0-1), and the DETERMINISTIC workflow controller state machine (P0-2).

    cdk synth -c env=dev -c retention_profile=sandbox-demo
    cdk deploy --all -c env=prod -c retention_profile=production-reference -c kms=customer-managed

The AgentCore control-plane attachment (gateway targets + Cedar policy load) consumes the CfnOutputs
of these stacks; see cdk/README.md. The legacy shell engine remains an internal reference only.

governed-core 1.9.0 parity (2026-09-03): -c tenants=a,b (hybrid multi-tenant, per-tenant data stacks +
gateway interceptor), -c global_kill_switch, -c budget_usd / budget_behavior, -c model_logging=1,
-c runtime_role — the same context switches as the benefits pack.
"""
import os
import shutil

import aws_cdk as cdk

from pv_stacks.data_stack import DataStack
from pv_stacks.network_stack import NetworkStack
from pv_stacks.compute_stack import ComputeStack
from pv_stacks.workflow_stack import WorkflowStack
from pv_stacks.identity_stack import IdentityStack
from pv_stacks.observability_stack import ObservabilityStack
from pv_stacks.gateway_stack import GatewayStack

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stage_lambda_bundle():
    """Stage tools + controls into one flat Lambda asset dir.

    The governance controls come from the PINNED `governed-core` package, not from a copy in this
    repo. That is the whole point: this repo once carried its own copy of these modules and the copy
    was missing the exactly-once FINAL# finalization control that two sibling agents had, which for
    an ICSR workflow is a regulator double-submission risk. A copy can silently diverge; a
    hash-pinned wheel cannot.

    Layering is deliberate and ordered:
      1. governed_core.controls_dir()  — the shared, versioned control plane
      2. lib/controls                  — this agent's domain-shaped modules (mask_pii, provenance,
                                         workflow_guards, sanitized, case_store)
      3. agents/.../tools              — the tool handlers

    Later layers overwrite earlier ones, so a domain module could in principle shadow a core module.
    It must not, and `tests/test_core_dependency.py` fails the build if one ever does — a silent
    shadow would reintroduce the drift by the back door.
    """
    import governed_core

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".build", "lambda-src")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    for src in (str(governed_core.controls_dir()),
                os.path.join(REPO, "lib", "controls"),
                os.path.join(REPO, "agents", "pharmacovigilance", "tools")):
        for f in os.listdir(src):
            if f.endswith(".py"):
                shutil.copy2(os.path.join(src, f), os.path.join(out, f))
    # Stamp the staged bundle with the core version actually used, so a deployed artifact can be
    # traced back to a released core rather than "whatever was in the tree that day".
    with open(os.path.join(out, "CORE_VERSION"), "w", encoding="utf-8") as fh:
        fh.write(governed_core.__version__ + "\n")
    return out


def budget_from_manifest(app):
    """B5 (task 128): the manifest's budget: block is THE place a customer sets the token cap; the CDK reads
    it here and every governed Lambda + the Runtime enforce it. -c budget_usd=<dollars per month> adds the
    USD cap (0 = tokens only); -c budget_behavior=soft downgrades a deployment to flag-only."""
    import json
    import yaml
    m = yaml.safe_load(open(os.path.join(REPO, "agents", "pharmacovigilance", "manifest.yaml"), encoding="utf-8"))
    b = dict((m or {}).get("budget") or {})
    b["monthly_usd"] = float(app.node.try_get_context("budget_usd") or 0)
    b["cap_behavior"] = app.node.try_get_context("budget_behavior") or b.get("cap_behavior") or "hard"
    with open(os.path.join(REPO, "lib", "model_prices.json"), encoding="utf-8") as fh:
        b["prices_json"] = json.dumps(json.load(fh), separators=(",", ":"))
    return b


app = cdk.App()
env_name = app.node.try_get_context("env") or "dev"
profile = app.node.try_get_context("retention_profile") or "sandbox-demo"
prefix = f"pv-{env_name}"
asset_dir = stage_lambda_bundle()

data = DataStack(app, f"{prefix}-data", prefix=prefix, retention_profile=profile,
                 kms_mode=app.node.try_get_context("kms") or "aws-managed")
# Hybrid multi-tenant (phase 107/109): -c tenants=a,b provisions a PHYSICALLY SEPARATE data stack per
# tenant (tenant-scoped tables + its own WORM vault). The shared control plane routes to them per
# request (gateway interceptor -> signed tenant -> tenancy.route_store). The base data stack above
# keeps the silo path + env-name shape; tenant stores are the ones tenants actually use.
tenants = [t.strip() for t in str(app.node.try_get_context("tenants") or "").split(",") if t.strip()]
multitenant = bool(tenants) or str(app.node.try_get_context("multitenant") or "").lower() in ("1", "true", "yes")
tenant_data = {t: DataStack(app, f"{prefix}-{t}-data", prefix=prefix, retention_profile=profile,
                            kms_mode=app.node.try_get_context("kms") or "aws-managed", tenant=t)
               for t in tenants}
network = None
if (app.node.try_get_context("network_mode") or "public") == "private":
    network = NetworkStack(app, f"{prefix}-network", prefix=prefix)
identity = IdentityStack(
    app, f"{prefix}-identity", prefix=prefix,
    identity_mode=app.node.try_get_context("identity_mode") or "sandbox",
    tenants=tuple(tenants),   # phase 107/108: one tenant_<id> group per tenant (hybrid multi-tenant)
    federation={
        "issuer_url": app.node.try_get_context("oidc_issuer_url") or "",
        "client_id": app.node.try_get_context("oidc_client_id") or "",
        "client_secret_arn": app.node.try_get_context("oidc_client_secret_arn") or "",
    })
compute = ComputeStack(app, f"{prefix}-compute", prefix=prefix, asset_dir=asset_dir, data=data,
                       provenance_secret=app.node.try_get_context("provenance_secret") or "",
                       network=network,
                       tenant=app.node.try_get_context("tenant") or "",
                       # phase 107 hybrid: -c multitenant=1 -> tenant derived per request (gateway interceptor)
                       multitenant=multitenant,
                       # G1 guardrail-pinned drafting: pass the platform guardrail so DraftNarrative
                       # generations are guardrail-assessed (-c guardrail_id=... -c guardrail_version=1)
                       guardrail_id=app.node.try_get_context("guardrail_id") or "",
                       guardrail_version=str(app.node.try_get_context("guardrail_version") or "1"),
                       # G2 approval-path verification: the identity pool/client feed approve-signoff
                       # (Cognito token verification). approvals_client_id lets a sandbox pass a
                       # CLI-auth demo client without touching the IaC gateway client.
                       identity=identity,
                       approvals_client_id=app.node.try_get_context("approvals_client_id") or "",
                       # task 127: optional platform-wide switch honoured IN ADDITION to the pack's own
                       # (-c global_kill_switch=/aegis/kill-switch, the reference stack's parameter)
                       global_kill_switch=app.node.try_get_context("global_kill_switch") or "",
                       # task 128: caps from the manifest budget: block (+ -c budget_usd / budget_behavior)
                       budget=budget_from_manifest(app))
workflow = WorkflowStack(app, f"{prefix}-workflow", prefix=prefix, compute=compute, data=data,
                         multitenant=multitenant)
gateway = GatewayStack(app, f"{prefix}-gateway", prefix=prefix, compute=compute, identity=identity,
                       multitenant=multitenant)
# Phase 110 (full transparency): -c model_logging=1 turns on Bedrock MODEL INVOCATION LOGGING for the
# account+region (it is an account-level singleton - it replaces any existing configuration, so it is
# opt-in) and delivers the gateway's vended request logs; the runtime's spans/logs are AgentCore-managed.
observability = ObservabilityStack(app, f"{prefix}-observability", prefix=prefix,
                                   compute=compute, workflow=workflow, data=data, gateway=gateway,
                                   model_logging=bool(app.node.try_get_context("model_logging")),
                                   # task 128: per-tenant 60/85/100 % budget alarms + the AWS Budgets USD
                                   # backstop (-c budget_usd) with an IAM deny action + kill-switch engage
                                   tenants=tuple(tenants) or ("default",),
                                   budget_usd=float(app.node.try_get_context("budget_usd") or 0),
                                   runtime_role_name=app.node.try_get_context("runtime_role") or "")

for s in (data, compute, workflow, identity, observability, gateway) + ((network,) if network else ()) \
        + tuple(tenant_data.values()):
    cdk.Tags.of(s).add("app", "pv-icsr-agent")
    cdk.Tags.of(s).add("env", env_name)
    cdk.Tags.of(s).add("cost-center", "governed-agents")

app.synth()
