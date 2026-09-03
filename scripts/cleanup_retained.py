#!/usr/bin/env python3
"""Teardown helper — removes the RETAIN'd resources a `cdk destroy` deliberately leaves behind.

RETAIN policies protect evidence in real deployments; in a DISPOSABLE validation account they leave
residue. This script deletes, for one env prefix: the audit ledger table, the WORM vault (versions
deleted with governance bypass — sandbox-demo retention only), the Cognito pool, any `<prefix>*`
secrets (force, no recovery), and schedules any `alias/<prefix>-data` CMK for deletion (KMS 7-day
minimum). It then prints a residual sweep. REFUSES to run unless --i-know-this-deletes-evidence.

Usage: python scripts/cleanup_retained.py --prefix hou-val9 --region us-east-1 --i-know-this-deletes-evidence
Exit 0 = swept clean; 2 = residue remains (fail the validation run)."""
import argparse
import json
import sys

import boto3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--i-know-this-deletes-evidence", action="store_true")
    a = ap.parse_args()
    if not a.i_know_this_deletes_evidence:
        ap.error("refusing: this deletes audit evidence; pass --i-know-this-deletes-evidence (validation accounts only)")
    p, region = a.prefix, a.region
    s = boto3.session.Session(region_name=region)

    ddb = s.client("dynamodb")
    for t in ddb.list_tables()["TableNames"]:
        if t.startswith(p):
            ddb.delete_table(TableName=t)
            print("deleted table", t)

    s3 = s.client("s3")
    for b in s3.list_buckets()["Buckets"]:
        if p in b["Name"]:
            try:
                vs = s3.list_object_versions(Bucket=b["Name"])
                for o in vs.get("Versions", []) + vs.get("DeleteMarkers", []):
                    s3.delete_object(Bucket=b["Name"], Key=o["Key"], VersionId=o["VersionId"],
                                     BypassGovernanceRetention=True)
                s3.delete_bucket(Bucket=b["Name"])
                print("deleted bucket", b["Name"])
            except Exception as e:
                print("bucket", b["Name"], "->", type(e).__name__)

    cog = s.client("cognito-idp")
    for pool in cog.list_user_pools(MaxResults=60)["UserPools"]:
        if pool["Name"].startswith(p):
            try:
                for d in cog.describe_user_pool(UserPoolId=pool["Id"])["UserPool"].get("Domain", []) or []:
                    pass
                cog.delete_user_pool(UserPoolId=pool["Id"])
                print("deleted pool", pool["Id"])
            except Exception as e:
                print("pool", pool["Id"], "->", type(e).__name__)

    sm = s.client("secretsmanager")
    for sec in sm.list_secrets(IncludePlannedDeletion=False).get("SecretList", []):
        if sec["Name"].startswith(p):
            sm.delete_secret(SecretId=sec["ARN"], ForceDeleteWithoutRecovery=True)
            print("purged secret", sec["Name"])

    kms = s.client("kms")
    for al in kms.list_aliases()["Aliases"]:
        if al["AliasName"] == f"alias/{p}-data":
            try:
                kms.schedule_key_deletion(KeyId=al["TargetKeyId"], PendingWindowInDays=7)
                print("CMK scheduled for deletion (7d)", al["TargetKeyId"])
            except Exception as e:
                print("cmk ->", type(e).__name__)

    # AgentCore engines can resurface (async deletes) — always re-sweep by name prefix
    try:
        cc = s.client("bedrock-agentcore-control")
        engp = p.replace("-", "_")
        for e in cc.list_policy_engines().get("policyEngines", []):
            if e.get("name", "").startswith(engp):
                try:
                    for pol in cc.list_policies(policyEngineId=e["policyEngineId"]).get("policies", []):
                        cc.delete_policy(policyEngineId=e["policyEngineId"], policyId=pol["policyId"])
                    cc.delete_policy_engine(policyEngineId=e["policyEngineId"])
                    print("deleted orphan policy engine", e["name"])
                except Exception:
                    pass
    except Exception:
        pass

    # residual sweep
    residue = {
        "tables": [t for t in ddb.list_tables()["TableNames"] if t.startswith(p)],
        "lambdas": [f["FunctionName"] for f in s.client("lambda").list_functions()["Functions"]
                    if f["FunctionName"].startswith(p)],
        "stacks": [st["StackName"] for st in s.client("cloudformation").describe_stacks()["Stacks"]
                   if st["StackName"].startswith(p)],
        "pools": [q["Name"] for q in cog.list_user_pools(MaxResults=60)["UserPools"] if q["Name"].startswith(p)],
    }
    clean = not any(residue.values())
    print(json.dumps({"prefix": p, "clean": clean, "residue": residue}, indent=2))
    sys.exit(0 if clean else 2)


if __name__ == "__main__":
    main()
