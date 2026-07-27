"""WorkflowStack (PV) — the DETERMINISTIC workflow controller (P0-2) + the human sign-off gate.

The regulated ICSR pipeline is a Step Functions STANDARD state machine — the model no longer decides the
compliance sequence. Every transition is gated on machine-verifiable evidence via the workflow_guards
Lambda; a failed guard routes to ManualReview (NEEDS_REVIEW), never onward:

  RECEIVED → Extract → [extracted?] → LookupBackground → [background?] → Mask → [deidentified?]
    → AssessSeriousness → [rules_executed?] → DetectDuplicate → [duplicate?] → (dup → DuplicateHold)
    → DraftNarrative → AuditIntent → HumanSignoff (waitForTaskToken, SoD) → COMMITTED
  DuplicateHold is a TERMINAL WORK-QUEUE state (not an error): a case detected as a duplicate ICSR is
  held so it is never double-reported to the regulator; the safety team works the hold.

Execution-input contract (documented): {case_id, requester, case_ref, drug, case_key, known_keys}.
`case_ref` is the OPAQUE reference minted by the ingest-case Lambda (called first by the intake
API/operator) over the raw adverse-event text — R3-2 ZERO-PHI: the raw `source` NEVER enters Step
Functions state; intake + mask fetch it server-side by ref, and the masked text is reached only via the
signed `sanitized_ref` (server-side sanitized-artifact store), so no content crosses execution history.
`drug` is the suspect product; `case_key`/`known_keys` feed the duplicate check. The LLM operates INSIDE
bounded steps only (the drafter Lambda invokes Bedrock; extraction/seriousness are deterministic). The
sign-off gate keeps separation-of-duties: signoff_register stores the task token for a DIFFERENT verified
approver; finalize runs only after approval."""
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

        # R3-2: the execution is started with {case_id, requester, case_ref, drug, ...} — the raw source
        # NEVER enters Step Functions state (it lives in the encrypted case store; the intake API/operator
        # calls the ingest-case Lambda FIRST). intake + mask fetch the raw text server-side by ref.
        extract = invoke("Extract", compute.intake, {"case_ref.$": "$.case_ref"}, "$.extract")
        g_extracted = guard("GuardExtracted", "extracted", {"fields.$": "$.extract.out.fields"})

        lookup = invoke("LookupBackground", compute.lookup, {"drug.$": "$.drug"}, "$.lookup")
        # Pass the WHOLE lookup output: openFDA returns found:false on a source failure (no terms),
        # and judging that is the guard's job — not a brittle JSONPath here.
        g_bg = guard("GuardBackground", "background", {"lookup.$": "$.lookup.out"})

        mask = invoke("MaskPii", compute.mask, {"case_ref.$": "$.case_ref"}, "$.mask")
        g_deid = guard("GuardDeidentified", "deidentified",
                       {"sanitized_ref.$": "$.mask.out.sanitized_ref"})

        # R3-2: assess receives ONLY the signed sanitized_ref (no masked_case) — it loads the masked text
        # SERVER-SIDE from the sanitized-artifact store, so masked content never crosses state either.
        assess = invoke("AssessSeriousness", compute.assess,
                        {"sanitized_ref.$": "$.mask.out.sanitized_ref"}, "$.assessment")
        g_rules = guard("GuardRulesExecuted", "rules_executed", {"assessment.$": "$.assessment.out"})

        dup = invoke("DetectDuplicate", compute.duplicate,
                     {"case_key.$": "$.case_key", "known_keys.$": "$.known_keys"}, "$.dup")
        g_dup = guard("GuardDuplicate", "duplicate", {"duplicate.$": "$.dup.out"})
        duplicate_hold = sfn.Succeed(
            self, "DuplicateHold",
            comment="TERMINAL WORK QUEUE (not an error): case detected as a duplicate ICSR — held so it "
                    "is not double-reported to the regulator; the safety team works the hold.")

        # R3-2 (both directions): the drafter loads the masked text SERVER-SIDE via the signed ref (no
        # content in the input) AND returns only an opaque narrative_ref (the CIOMS text is stored
        # server-side, never in $.draft). So neither the case nor the drafted narrative enters state.
        draft = invoke("DraftNarrative", compute.core,
                       {"deidentified": True, "sanitized_ref.$": "$.mask.out.sanitized_ref"}, "$.draft")
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
                 # opaque ref only (artifact_id + digest + signature) — the reviewer fetches the narrative
                 # server-side; the narrative TEXT never enters the pending record or execution state.
                 "narrative_ref.$": "$.draft.out.narrative_ref",
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
