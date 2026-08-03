import os
import hashlib
import evidence

# finalize_signoff — the PRIVILEGED commit task, invoked by the sign-off state machine ONLY after a
# valid separation-of-duties approval. The agent can never reach it (Cedar forbids the direct finalize
# tool and this Lambda is not on the Gateway). It records the COMMITTED decision through the CANONICAL
# evidence service (hash-chained + WORM), fail-loud — no raw put_item, no swallowed errors.


def _exactly_once_marker(case_id, submission_id, approver, region):
    """GA-5: EXACTLY-ONCE finalization. A conditional marker item (FINAL#<case>) in the append-only
    audit table is the single commit gate: the first finalize creates it; ANY later finalize — retried
    Lambda, replayed execution, or a different approval path — finds it and returns the ORIGINAL
    submission idempotently, writing no second COMMITTED record. The marker is a new item (append-only
    respected) and the audit role cannot update/delete it.

    Ported from edu_financial_aid_agent 2026-08-03. It was implemented there and in the housing agent
    and never reached this repo, while this repo's IaC comment, deployment guide, evidence file and
    threat model all described it as present. For an ICSR workflow the absence is a double-reporting
    risk: without this gate a retried or replayed finalize writes a second COMMITTED record, i.e. a
    second submission of the same case to a regulator. See docs/MULTI-AGENT-COMPOSITION.md."""
    import boto3
    from botocore.exceptions import ClientError
    table = os.environ.get("AUDIT_TABLE", "governed-audit-ledger")
    cli = boto3.resource("dynamodb", region_name=region).Table(table)
    try:
        cli.put_item(Item={"audit_id": "FINAL#" + str(case_id), "submission_id": submission_id,
                           "approver": approver, "kind": "finalize-marker"},
                     ConditionExpression="attribute_not_exists(audit_id)")
        return True, submission_id
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            prior = cli.get_item(Key={"audit_id": "FINAL#" + str(case_id)}).get("Item") or {}
            return False, prior.get("submission_id", submission_id)
        raise


def handler(event, context):
    case_id = event.get("case_id") or event.get("icsr_id")
    requester = event.get("requester")
    approver = event.get("approver")
    commit_action = event.get("commit_action") or os.environ.get("COMMIT_ACTION", "finalize")
    submission_id = "SUB-" + hashlib.sha256(
        ("%s|%s" % (case_id, approver)).encode("utf-8")).hexdigest()[:12].upper()

    region = os.environ.get("AWS_REGION", "us-east-1")
    first, submission_id = _exactly_once_marker(case_id, submission_id, approver, region)
    if not first:
        return {"committed": True, "idempotent": True, "submission_id": submission_id,
                "case_id": case_id, "requester": requester, "approver": approver,
                "note": "case already finalized; returning the original submission (exactly-once)"}

    res = evidence.record_event({
        "case_id": case_id, "action": commit_action, "phase": "COMMITTED", "actor": approver,
        "deidentified": True,
        "payload": {"requester": requester, "approver": approver, "submission_id": submission_id},
    }, context, source=os.environ.get("SOURCE", "finalize"))

    committed = bool(res.get("stored")) or "already recorded" in (res.get("reason") or "")
    out = {"committed": committed, "submission_id": submission_id, "case_id": case_id,
           "requester": requester, "approver": approver,
           "evidence": {k: res.get(k) for k in ("audit_id", "chain_hash", "seq", "worm", "stored", "reason", "error")}}
    if not committed:
        out["error"] = res.get("error", "the COMMITTED evidence record could not be written")
    return out
