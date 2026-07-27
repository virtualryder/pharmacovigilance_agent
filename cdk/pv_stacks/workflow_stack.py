"""WorkflowStack (PV) — the DETERMINISTIC workflow controller (P0-2) + the human sign-off gate.

The regulated ICSR pipeline is a Step Functions STANDARD state machine — the model no longer decides the
compliance sequence. Every transition is gated on machine-verifiable evidence via the workflow_guards
Lambda; a failed guard routes to ManualReview (NEEDS_REVIEW), never onward:

  RECEIVED → Extract → [extracted?] → LookupBackground → [background?] → Mask → [deidentified?]
    → AssessSeriousness → [rules_executed?] → DetectDuplicate → [duplicate?] → (dup → DuplicateHold)
    → DraftNarrative → AuditIntent → HumanSignoff (waitForTaskToken, SoD) → COMMITTED
  DuplicateHold is a TERMINAL WORK-QUEUE state (not an error): a case detected as a duplicate ICSR is
  held so it is never double-reported to the regulator; the safety team works the hold.

Execution-input contract (documented): {case_id, requester, source, drug, case_key, known_keys}.
`source` is the raw adverse-event text; `drug` the suspect product; `case_key`/`known_keys` feed the
duplicate check. The LLM operates INSIDE bounded steps only (the drafter Lambda invokes Bedrock;
extraction/seriousness are deterministic). The sign-off gate keeps separation-of-duties: signoff_register
stores the task token for a DIFFERENT verified approver; finalize runs only after approval.

NOTE (Gate-B follow-on): PV is not yet pass-by-reference — the raw `source` transits Step Functions
state until masking. To reach the financial-aid agent's zero-PII-telemetry posture (R3-2) before a
real-data pilot, add an ingest/case-store step so only opaque refs travel through execution history; the
strict PII canary will flag pre-mask content until then. Documented in PV-PILOT-READINESS-PLAN."""
import aws_cdk as cdk
from aws_cdk import aws_stepfunctions as sfn, aws_stepfunctions_tasks as tasks
from constructs import Construct


class WorkflowStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, prefix: str, compute, data, **kw):
        super().__init__(scope, cid, **kw)

        def invoke(name, fn, payload, result_path):
            return tasks.LambdaInvoke(self, name, lambda_function=fn,
                                      payload=sfn.TaskInput.from_object(payload),
                                      result_selector={"out.$": "$.Payload"},
                                      result_path=result_path)

        def guard(name, guard_name, payload):
            return tasks.LambdaInvoke(self, name, lambda_function=compute.guards,
                                      payload=sfn.TaskInput.from_object({"guard": guard_name, **payload}),
                                      result_selector={"ok.$": "$.Payload.ok", "reason.$": "$.Payload.reason"},
                                      result_path=f"$.guards.{guard_name}")

        manual_review = sfn.Succeed(self, "ManualReview",
                                    comment="Fail-closed: evidence missing/unverified -> NEEDS_REVIEW "
                                            "for a safety reviewer; no automated outcome.")

        extract = invoke("Extract", compute.intake, {"source.$": "$.source"}, "$.extract")
        g_extracted = guard("GuardExtracted", "extracted", {"fields.$": "$.extract.out.fields"})

        lookup = invoke("LookupBackground", compute.lookup, {"drug.$": "$.drug"}, "$.lookup")
        # Pass the WHOLE lookup output: openFDA returns found:false on a source failure (no coa/terms),
        # and judging that is the guard's job — not a brittle JSONPath here.
        g_bg = guard("GuardBackground", "background", {"lookup.$": "$.lookup.out"})

        mask = invoke("MaskPii", compute.mask, {"case.$": "$.source"}, "$.mask")
        g_deid = guard("GuardDeidentified", "deidentified",
                       {"sanitized_ref.$": "$.mask.out.sanitized_ref"})

        assess = invoke("AssessSeriousness", compute.assess,
                        {"case.$": "$.mask.out.masked_case",
                         "sanitized_ref.$": "$.mask.out.sanitized_ref"}, "$.assessment")
        g_rules = guard("GuardRulesExecuted", "rules_executed", {"assessment.$": "$.assessment.out"})

        dup = invoke("DetectDuplicate", compute.duplicate,
                     {"case_key.$": "$.case_key", "known_keys.$": "$.known_keys"}, "$.dup")
        g_dup = guard("GuardDuplicate", "duplicate", {"duplicate.$": "$.dup.out"})
        duplicate_hold = sfn.Succeed(
            self, "DuplicateHold",
            comment="TERMINAL WORK QUEUE (not an error): case detected as a duplicate ICSR — held so it "
                    "is not double-reported to the regulator; the safety team works the hold.")

        draft = invoke("DraftNarrative", compute.core,
                       {"case.$": "$.mask.out.masked_case",
                        "deidentified": True, "sanitized_ref.$": "$.mask.out.sanitized_ref"}, "$.draft")
        audit_intent = invoke("AuditIntent", compute.write_audit,
                              {"icsr_id.$": "$.case_id", "action": "icsr-determination",
                               "phase": "INTENT", "actor": "workflow-controller",
                               "payload.$": "States.JsonToString($.assessment.out)"}, "$.audit")

        signoff = tasks.LambdaInvoke(
            self, "HumanSignoff", lambda_function=compute.signoff_register,
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload=sfn.TaskInput.from_object(
                {"icsr_id.$": "$.case_id", "requester.$": "$.requester",
                 "content_hash.$": "States.Hash(States.JsonToString($.assessment.out), 'SHA-256')",
                 "taskToken": sfn.JsonPath.task_token}),
            timeout=cdk.Duration.hours(24), result_path="$.approval")
        finalize = invoke("Finalize", compute.finalize,
                          {"icsr_id.$": "$.case_id", "requester.$": "$.requester",
                           "approver.$": "$.approval.approver"}, "$.commit")
        committed = sfn.Succeed(self, "Committed")

        c1 = sfn.Choice(self, "ExtractedOk").when(
            sfn.Condition.boolean_equals("$.guards.extracted.ok", True), lookup).otherwise(manual_review)
        c2 = sfn.Choice(self, "BackgroundOk").when(
            sfn.Condition.boolean_equals("$.guards.background.ok", True), mask).otherwise(manual_review)
        c3 = sfn.Choice(self, "DeidentifiedOk").when(
            sfn.Condition.boolean_equals("$.guards.deidentified.ok", True), assess).otherwise(manual_review)
        c4 = sfn.Choice(self, "RulesOk").when(
            sfn.Condition.boolean_equals("$.guards.rules_executed.ok", True), dup).otherwise(manual_review)
        c5 = sfn.Choice(self, "NotDuplicate").when(
            sfn.Condition.boolean_equals("$.guards.duplicate.ok", True), draft).otherwise(duplicate_hold)

        definition = extract.next(g_extracted).next(c1)
        lookup.next(g_bg).next(c2)
        mask.next(g_deid).next(c3)
        assess.next(g_rules).next(c4)
        dup.next(g_dup).next(c5)
        draft.next(audit_intent).next(signoff).next(finalize).next(committed)

        self.controller = sfn.StateMachine(
            self, "Controller", state_machine_name=f"{prefix}-icsr-workflow",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=cdk.Duration.hours(25),
        )
        cdk.CfnOutput(self, "ControllerArn", value=self.controller.state_machine_arn)
