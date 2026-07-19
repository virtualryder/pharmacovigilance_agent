# Production network hardening (P0-6)

The accelerator deploys, by default, with public AWS service endpoints (Lambdas with default egress, AWS-
managed encryption). That is correct for the self-contained demo. This note specifies the **production
network posture** an adopter turns on for a regulated deployment — private networking, customer-managed
encryption, and account isolation — and the `deploy.sh` flags that enable the first two without changing
the governance logic.

## 1. Private networking (VPC mode)

Run every tool/control Lambda and the AgentCore Runtime inside a **VPC, in private subnets with no route
to an Internet Gateway**. All AWS-service traffic leaves through **VPC endpoints**, so nothing transits
the public internet:

- **Gateway endpoints** (free, routed): `com.amazonaws.<region>.dynamodb`, `com.amazonaws.<region>.s3` —
  the audit ledger and the WORM bucket.
- **Interface endpoints** (PrivateLink, one ENI per subnet, SG-guarded): `bedrock-runtime` (draft/guardrail),
  `comprehend` + `comprehendmedical` (PII/PHI masking), `states` (sign-off Step Functions), `ssm`
  (provenance secret / gateway URL), `secretsmanager` (HUD/API tokens), `sts`, `logs` (CloudWatch),
  `kms`, and `bedrock-agentcore` (the gateway/policy-engine control plane).
- **Egress to genuinely external authoritative sources** (HUD USER, College Scorecard, openFDA) is the one
  case that needs the public internet. Route it through a **NAT gateway in a single egress subnet** with an
  allow-list (or a forward proxy), so the blast radius is one controlled path, not per-Lambda egress. The
  connector's outbound OAuth stays minted by AgentCore Identity — no secret on the wire.
- **Security groups**: Lambdas get an SG that egresses only to the endpoint SGs (443) and the NAT path;
  the endpoint SG ingresses 443 only from the Lambda SG. Deny-by-default at the network layer mirrors the
  Cedar deny-by-default at the authorization layer.
- **IAM**: VPC-attached Lambdas need `ec2:CreateNetworkInterface`, `DescribeNetworkInterfaces`,
  `DeleteNetworkInterface` (the managed `AWSLambdaVPCAccessExecutionRole`), added by `deploy.sh` when VPC
  mode is on — still least-privilege (that is the whole managed policy).

Enable it (bring your own VPC):

```bash
VPC_MODE=1 \
  LAMBDA_SUBNET_IDS=subnet-aaa,subnet-bbb \
  LAMBDA_SG_IDS=sg-xxx \
  bash lib/engine/deploy.sh agents/pharmacovigilance
```

`deploy.sh` then attaches every control/tool Lambda to that VPC config and grants the ENI permissions.
Standing up the VPC, subnets, endpoints, NAT, and SGs is adopter IaC (a Terraform/CDK module is the natural
home) — the accelerator consumes the subnet/SG ids you pass, it does not create your network.

## 2. Customer-managed encryption (CMK)

By default DynamoDB and S3 use AWS-owned/managed keys. For a regulated deployment, encrypt every data
store with a **customer-managed KMS key (CMK)** you own, rotate, and can audit/revoke:

- **DynamoDB** (audit ledger + pending-approvals): SSE with the CMK (`--sse-specification
  SSEType=KMS,KMSMasterKeyId=<cmk>`).
- **S3 WORM bucket**: default bucket encryption `aws:kms` with the CMK, `BucketKeyEnabled` on to cut KMS
  cost. Object Lock (WORM) stays as-is; CMK adds confidentiality on top of the integrity/retention control.
- **CloudWatch Logs** (Lambda logs may contain de-identified case text): associate the log groups with the
  CMK.
- **Key policy**: grant the tool/sign-off roles `kms:GenerateDataKey*` + `kms:Decrypt` on the CMK only;
  grant CloudWatch Logs the `kms:Encrypt*/Decrypt*/GenerateDataKey*/Describe*` conditions for the log-group
  ARN. The tamper-Deny already blocks the roles from `s3:BypassGovernanceRetention` etc.; the CMK adds a
  second, revocable control — disabling the key freezes access to the audit data.

Enable it (bring your own key):

```bash
CMK_ARN=arn:aws:kms:us-east-1:<acct>:key/<id> bash lib/engine/deploy.sh agents/pharmacovigilance
```

`deploy.sh` then creates the DynamoDB tables with CMK SSE and sets the WORM bucket's default encryption to
the CMK. (Key creation, rotation, and the key policy are adopter-owned — the accelerator consumes the key
ARN you pass.)

## 3. Multi-account layout (reference)

For separation of duties at the account boundary, split the deployment across accounts under one
Organization, so the people who operate the agent cannot alter or delete the evidence:

- **Governance / audit account** — owns the DynamoDB audit ledger, the S3 WORM bucket (Object Lock in
  COMPLIANCE mode with a longer retention than the demo's 1-day GOVERNANCE default), the CMK, and the
  CloudTrail/Config org trail. Write access is granted cross-account to the workload roles for *append
  only*; no human in the workload account has delete/alter rights (SCPs deny it org-wide).
- **Workload account(s)** — one per environment (dev/stage/prod) and optionally per vertical — run the
  AgentCore Gateway, Runtime, and the tool/control Lambdas. The tool role assumes a **cross-account
  append-only role** in the governance account to write evidence.
- **Identity** — corporate IdP → a central Cognito (or IAM Identity Center) in a shared identity account;
  the sign-off gate verifies the access token exactly as today (P0-5), unchanged by the account split.
- **Guardrails** — Organization SCPs enforce the tamper-Deny at the org level (deny `dynamodb:DeleteItem/
  UpdateItem`, `s3:DeleteObject*`, `s3:BypassGovernanceRetention`, `kms:ScheduleKeyDeletion` on the audit
  resources) so it cannot be undone by a workload-account admin.

This is a **reference topology** — it is an Organizations/Control-Tower decision the adopter makes in their
own AWS org; it cannot be stood up in a single sandbox account. The governance logic (Cedar, evidence
chain, sign-off, provenance, identity) is account-topology-independent and moves unchanged.

## What the accelerator provides vs. what the adopter owns

The accelerator provides: the env-gated VPC attachment and CMK encryption in `deploy.sh`, this design, and
the architecture diagram (`docs/Architecture-Diagram.md`). The adopter owns: the VPC/subnets/endpoints/NAT/
SGs, the KMS key and its policy/rotation, the Organization/accounts/SCPs, and validating the private-network
path in their own account. None of it changes the governance controls — it hardens the substrate they run on.
