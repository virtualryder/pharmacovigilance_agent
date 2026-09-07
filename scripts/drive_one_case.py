#!/usr/bin/env python3
"""Drive ONE isolated governed pharmacovigilance case (for the #168 lineage coverage proof).

The PV counterpart of the benefits script of the same name, and the last thing REL-5 was missing:
without it the portfolio gate's LIN_case_driven and LIN_zero_orphans checks have nothing to measure
on this pack.

Reuses mt_two_tenant_proof's helpers to: create a reviewer in the target tenant, token-verify an
ingest (which mints the signed tenant pair), start ONE ICSR-assessment execution, and poll to the
sign-off pause. Runs in a quiet window so the account capture in [start_ms, end_ms] contains only
this execution's governed activity - clean invoke/audit parity for the lineage proof.

Prints a JSON result (case_id, execution_arn, window) to stdout and to .build/lineage-case.json,
which is where full_portfolio_gate.py reads it from.

Pack facts come from pack.json (PAR-4), not from constants here: the sign-off state the execution
must reach is declared once, in the descriptor, rather than being spelled the same way in four files.
"""
import io, json, time, uuid, secrets, sys, os, pathlib
import boto3

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import mt_two_tenant_proof as mt

with io.open(REPO / "pack.json", encoding="utf-8") as fh:
    PACK = json.load(fh)
SIGNOFF_STATE = PACK["workflow"]["signoff_state"]

REGION = os.environ.get("AWS_REGION", "us-east-1")
PREFIX = os.environ.get("LINEAGE_PREFIX", "pv-gate")     # deployment prefix, e.g. pv-fp
TENANT = os.environ.get("LINEAGE_TENANT", PACK["tenants"][0])

# A realistic ICSR: named patient, address, phone, DOB, plus the suspect product and the event.
# The PII is the point - mask_pii has to have something to find for the lineage chain to be real.
CASE = ("Patient Jane Q. Sample, DOB 1990-01-01, 12 Elm St Springfield, phone 555-0100, "
        "email jane.sample@example.com. Suspect product atorvastatin 40 mg daily; adverse event: "
        "rhabdomyolysis, hospitalized 2026-08-01; outcome recovering; reporter: physician.")

cf = boto3.client("cloudformation", region_name=REGION)
idp = boto3.client("cognito-idp", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
sfn = boto3.client("stepfunctions", region_name=REGION)

ident = mt.outputs(cf, f"{PREFIX}-identity")
pool, client = ident["UserPoolId"], ident["ClientId"]
ctrl = mt.outputs(cf, f"{PREFIX}-workflow")["ControllerArn"]

pw = "Ln-" + secrets.token_urlsafe(12) + "aA1!"
# PV's reviewer role carries no separate tools_granted group - that is a benefits-side distinction.
mt.make_user(idp, pool, "lineage-cw", ["pv_reviewer", "tenant_" + TENANT], pw)
time.sleep(3)
tok = mt.access_token(pool, client, REGION, "lineage-cw", pw)

case_id = "LIN-" + uuid.uuid4().hex[:6].upper()
start_ms = int(time.time() * 1000)
ing = json.loads(lam.invoke(FunctionName=f"{PREFIX}-ingest-case",
                            Payload=json.dumps({"source": CASE, "case_id": case_id,
                                                "consent_attested": True, "purpose": "pharmacovigilance",
                                                "access_token": tok}).encode())["Payload"].read())
if not ing.get("case_ref"):
    # Fail loudly. A driven case with no case_ref produces an empty lineage window, and an empty
    # window reads as "zero orphans" - the L25/L29/L31/L33 failure mode, where absence of evidence
    # is reported as evidence of absence.
    print(json.dumps({"error": "ingest did not return a case_ref", "ingest": ing}, indent=2))
    sys.exit(1)

ex = sfn.start_execution(stateMachineArn=ctrl, name="lineage-" + case_id.lower(),
                         input=json.dumps({"case_id": case_id, "requester": "lineage-cw",
                                           "case_ref": ing["case_ref"],
                                           "drug": "atorvastatin",
                                           "case_key": "atorvastatin|rhabdomyolysis|2026|hcp",
                                           "known_keys": "",
                                           **ing.get("tenant_binding", {})}))["executionArn"]
status, states = "RUNNING", []
for _ in range(60):
    time.sleep(5)
    d = sfn.describe_execution(executionArn=ex)
    status = d["status"]
    states = [e.get("stateEnteredEventDetails", {}).get("name") for e in
              sfn.get_execution_history(executionArn=ex, maxResults=200)["events"]
              if e["type"] == "TaskStateEntered"]
    states = [s for s in states if s]
    if status != "RUNNING" or SIGNOFF_STATE in states:
        break
if status == "RUNNING":
    time.sleep(6)
    sfn.stop_execution(executionArn=ex, cause="lineage isolated case: signoff pause reached")
end_ms = int(time.time() * 1000)

result = {"case_id": case_id, "execution_arn": ex, "status": status, "states": states,
          "start_ms": start_ms, "end_ms": end_ms, "tenant": TENANT,
          "case_ref": ing.get("case_ref"), "minted_binding": bool(ing.get("tenant_binding")),
          "signoff_state": SIGNOFF_STATE, "reached_signoff": SIGNOFF_STATE in states}
REPO.joinpath(".build").mkdir(exist_ok=True)
REPO.joinpath(".build", "lineage-case.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
