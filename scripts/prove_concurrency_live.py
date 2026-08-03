#!/usr/bin/env python3
"""LIVE proof of the two concurrency controls, against a deployed environment.

WHY THIS EXISTS
---------------
Both controls were shipped and covered by offline tests that were verified to fail when the control
is disabled. Neither had ever been exercised against deployed AWS resources, and the offline tests
use a fake DynamoDB. A conditional put is exactly the kind of control where "passes against a fake"
and "holds against the real service" can differ — the condition expression, the attribute names, and
the IAM policy all have to be right at once.

  1. EXACTLY-ONCE FINALIZATION (FINAL# conditional put).
     A retried Lambda, a replayed Step Functions execution, or a second approval path must NOT write
     a second COMMITTED record. For an ICSR workflow a second COMMITTED record is a second submission
     of the same case to a regulator.

  2. DUPLICATE-SUBMISSION PROTECTION (pending-approval conditional put).
     A second concurrent execution for the same case must not overwrite the first PENDING approval —
     that would strand the first execution's task token. It must fail closed.

Both are proved here by BEHAVIOUR against live resources, then corroborated by reading the
underlying tables. Usage:

    python scripts/prove_concurrency_live.py --env val3 --region us-east-1
"""
import argparse
import concurrent.futures
import json
import sys
import time
import uuid

import boto3


def invoke(lam, fn, payload):
    r = lam.invoke(FunctionName=fn, InvocationType="RequestResponse",
                   Payload=json.dumps(payload).encode())
    body = r["Payload"].read().decode("utf-8", "ignore")
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = {"_raw": body}
    return r.get("FunctionError"), parsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()

    pfx = "pv-%s" % a.env
    lam = boto3.client("lambda", region_name=a.region)
    ddb = boto3.resource("dynamodb", region_name=a.region)
    audit = ddb.Table("%s-audit-ledger" % pfx)
    pending = ddb.Table("%s-pending-approvals" % pfx)

    out = {"env": a.env, "region": a.region, "checks": {}}
    marker = uuid.uuid4().hex[:10].upper()

    # ---------------------------------------------------------------- exactly-once
    case = "PVLIVE-EO-%s" % marker
    first_err, first = invoke(lam, "%s-finalize" % pfx,
                              {"case_id": case, "requester": "pv.reporter",
                               "approver": "pv.qualified.person"})
    time.sleep(1)

    # Replay the SAME finalize (retried Lambda / replayed execution).
    replay_err, replay = invoke(lam, "%s-finalize" % pfx,
                                {"case_id": case, "requester": "pv.reporter",
                                 "approver": "pv.qualified.person"})
    # And a DIFFERENT approver — a genuinely different approval path must also be refused a second
    # COMMITTED record, and must receive the ORIGINAL submission id.
    other_err, other = invoke(lam, "%s-finalize" % pfx,
                              {"case_id": case, "requester": "pv.reporter",
                               "approver": "pv.second.approver"})

    committed = [i for i in audit.scan(
        ProjectionExpression="audit_id, case_id, phase").get("Items", [])
        if i.get("case_id") == case and i.get("phase") == "COMMITTED"]
    final_marker = audit.get_item(Key={"audit_id": "FINAL#%s" % case}).get("Item")

    out["checks"]["exactly_once"] = {
        "case_id": case,
        "first_call": {"error": first_err, "committed": first.get("committed"),
                       "idempotent": first.get("idempotent"),
                       "submission_id": first.get("submission_id")},
        "replay_same_approver": {"error": replay_err, "committed": replay.get("committed"),
                                 "idempotent": replay.get("idempotent"),
                                 "submission_id": replay.get("submission_id")},
        "replay_other_approver": {"error": other_err, "committed": other.get("committed"),
                                  "idempotent": other.get("idempotent"),
                                  "submission_id": other.get("submission_id")},
        "COMMITTED_records_for_case": len(committed),
        "FINAL_marker_present": bool(final_marker),
        "submission_ids_all_equal": (first.get("submission_id")
                                     == replay.get("submission_id")
                                     == other.get("submission_id")),
    }
    eo = out["checks"]["exactly_once"]
    eo["verdict"] = "PASS" if (
        first.get("committed") and not first.get("idempotent")
        and replay.get("idempotent") is True and other.get("idempotent") is True
        and eo["submission_ids_all_equal"]
        and eo["COMMITTED_records_for_case"] == 1
        and eo["FINAL_marker_present"]) else "FAIL"

    # ------------------------------------------------- exactly-once under CONCURRENCY
    # The single-threaded replay above proves idempotence. This proves the RACE: N simultaneous
    # finalizes for one case must produce exactly one commit, not N.
    race_case = "PVLIVE-RACE-%s" % marker
    payload = {"case_id": race_case, "requester": "pv.reporter",
               "approver": "pv.qualified.person"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda _: invoke(lam, "%s-finalize" % pfx, payload), range(8)))
    firsts = [r for _, r in results if r.get("committed") and not r.get("idempotent")]
    idems = [r for _, r in results if r.get("idempotent") is True]
    race_committed = [i for i in audit.scan(
        ProjectionExpression="audit_id, case_id, phase").get("Items", [])
        if i.get("case_id") == race_case and i.get("phase") == "COMMITTED"]
    out["checks"]["exactly_once_concurrent"] = {
        "case_id": race_case, "parallel_invocations": 8,
        "non_idempotent_commits": len(firsts), "idempotent_returns": len(idems),
        "COMMITTED_records_for_case": len(race_committed),
        "distinct_submission_ids": len({r.get("submission_id") for _, r in results}),
        "verdict": "PASS" if (len(firsts) == 1 and len(race_committed) == 1
                              and len({r.get("submission_id") for _, r in results}) == 1)
                   else "FAIL",
    }

    # ------------------------------------------------------- duplicate submission
    dup_case = "PVLIVE-DUP-%s" % marker
    reg1_err, reg1 = invoke(lam, "%s-signoff-register" % pfx,
                            {"case_id": dup_case, "requester": "pv.reporter",
                             "taskToken": "tok-first-%s" % marker,
                             "content_hash": "sha256:" + "a" * 64})
    time.sleep(1)
    # Second concurrent execution for the same case, while the first is still PENDING.
    reg2_err, reg2 = invoke(lam, "%s-signoff-register" % pfx,
                            {"case_id": dup_case, "requester": "pv.other.reporter",
                             "taskToken": "tok-second-%s" % marker,
                             "content_hash": "sha256:" + "b" * 64})

    item = pending.get_item(Key={"case_id": dup_case}).get("Item") or {}
    second_refused = bool(reg2_err) or "duplicate" in json.dumps(reg2).lower()

    out["checks"]["duplicate_submission"] = {
        "case_id": dup_case,
        "first_registration": {"error": reg1_err, "registered": reg1.get("registered")},
        "second_registration_error": reg2_err,
        "second_refused": second_refused,
        "stored_task_token_is_the_FIRST": item.get("task_token") == "tok-first-%s" % marker,
        "stored_status": item.get("status"),
        "content_hash_bound": bool(item.get("content_hash")),
        "verdict": "PASS" if (reg1.get("registered") and second_refused
                              and item.get("task_token") == "tok-first-%s" % marker
                              and item.get("status") == "PENDING") else "FAIL",
    }

    verdicts = [v["verdict"] for v in out["checks"].values()]
    out["overall"] = "PASS" if all(v == "PASS" for v in verdicts) else "FAIL"
    print(json.dumps(out, indent=2, default=str))
    return 0 if out["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
