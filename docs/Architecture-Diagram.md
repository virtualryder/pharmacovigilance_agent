# Governed agent — production architecture (P0-6)

The diagram below is the target production architecture: corporate identity → deny-by-default
authorization → private-networked compute → customer-managed-encrypted, append-only evidence, with the
data plane isolated in a separate governance/audit account. It renders on GitHub (Mermaid) and is
reproduced as a standalone HTML in the delivery.

```mermaid
flowchart TB
  subgraph IDP["Identity plane (shared identity account)"]
    OKTA["Corporate IdP<br/>(Okta / Entra / Ping)"]
    COG["Cognito user pool<br/>federation + groups"]
    OKTA -->|OIDC / SAML| COG
  end

  USER["Human reviewer<br/>(MFA, step-up for final commit)"]
  USER -->|authenticate| COG
  COG -->|access token (RS256 JWT)| USER

  subgraph WL["Workload account — VPC (private subnets, no IGW route)"]
    direction TB
    GW["AgentCore Gateway (MCP)<br/>CUSTOM_JWT authorizer<br/><b>Cedar deny-by-default</b>"]
    RT["AgentCore Runtime<br/>(Strands agent)"]
    subgraph LAM["Tool + control Lambdas (private subnets)"]
      direction LR
      T1["intake"]
      T2["lookup_*<br/>(authoritative source)"]
      T3["mask_pii<br/>(Comprehend)"]
      T4["assess_*<br/>(provenance verify)"]
      T5["draft_*<br/>(Bedrock + Guardrail)"]
      T6["write_audit<br/>(evidence service)"]
      T7["request/approve<br/>sign-off (JWT id)"]
    end
    subgraph VPE["VPC endpoints (PrivateLink + gateway)"]
      direction LR
      EP1["bedrock-runtime<br/>comprehend(-medical)"]
      EP2["states · logs · sts<br/>ssm · secretsmanager · kms"]
      EP3["dynamodb · s3<br/>(gateway endpoints)"]
    end
    NAT["NAT (single egress subnet,<br/>allow-listed)"]
  end

  USER -->|"tool call, bearer = user JWT"| GW
  GW -->|"authorized (Cedar) invoke"| RT
  RT --> LAM
  LAM --> VPE
  T2 -.->|"HUD / Scorecard / openFDA"| NAT
  NAT -.-> EXT["External authoritative sources<br/>(public internet, controlled path)"]

  subgraph GOV["Governance / audit account (separation of duties)"]
    direction TB
    KMS["Customer-managed KMS key (CMK)<br/>rotate · audit · revoke"]
    DDB["DynamoDB audit ledger<br/>append-only hash chain · CMK SSE"]
    WORM["S3 WORM bucket<br/>Object Lock (COMPLIANCE) · CMK"]
    SFN["Step Functions<br/>sign-off (separation of duties)"]
    SCP["Org SCPs: deny delete/alter<br/>of evidence + key deletion"]
    KMS --- DDB
    KMS --- WORM
  end

  EP3 -->|"append-only (cross-account role)"| DDB
  EP3 -->|"put object (cross-account role)"| WORM
  T6 --> DDB
  T6 --> WORM
  T7 --> SFN
  SFN --> T6

  classDef gov fill:#e8f0ff,stroke:#3b6;
  classDef sec fill:#fff3e8,stroke:#e83;
  class GOV,DDB,WORM,KMS,SFN,SCP gov;
  class GW,SCP sec;
```

## Reading the diagram

- **Authorization before compute.** Every tool call carries the human's access token; the Gateway's Cedar
  policy engine evaluates the *real principal* deny-by-default before any Lambda runs (P0-4/P0-5).
- **Private substrate.** Compute sits in private subnets; AWS-service traffic uses VPC endpoints; only the
  authoritative-source lookups leave via a single controlled NAT path (P0-6 §1).
- **Evidence is isolated and encrypted.** The append-only hash-chained ledger and the WORM bucket live in a
  separate governance account, encrypted with a customer-managed key, with Org SCPs denying deletion/alter —
  so operators of the agent cannot rewrite history (P0-1, P0-6 §2/§3).
- **A human commits.** The consequential action only finalizes through the Step Functions sign-off gate
  after a *different* verified identity approves (P0-5); the agent never self-commits.
