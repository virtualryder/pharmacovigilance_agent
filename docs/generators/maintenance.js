const G = require("./guides.js");
const { H1, H2, H3, P, bold, code, bullet, num, codeBlock, callout, table, spacer, coverAndToc, makeDoc, Packer } = G;

const cover = coverAndToc(
  ["Maintenance & Operations Guide"],
  "Pharmacovigilance ICSR Intake Agent on Amazon Bedrock AgentCore",
  "Day-two operations for the governed pharmacovigilance (drug-safety) accelerator — routine changes, the Runtime lifecycle, monitoring, audit-evidence handling, teardown/rebuild, and the known toolchain gotchas. Accelerator reference. Version 1.0 · 2026.",
  ["1. Operating model", "2. Routine operations", "3. The Runtime agent lifecycle", "4. Monitoring & observability", "5. Audit-evidence management", "6. Teardown & rebuild", "7. Troubleshooting & known gotchas", "8. Cost & housekeeping"]
);

const body = [
  H1("1. Operating model"),
  P("The deployment has three lifecycles, and keeping them straight is the key to safe operations:"),
  table(["Lifecycle", "What it contains", "Cadence"], [
    [[bold("Identity")], "Cognito pool, app client, users/group", "Stable — changed rarely; survives spine redeploys"],
    [[bold("Governance spine")], "Cedar engine, Gateway, tools, Guardrail, WORM audit, human gate", "Reproducible — redeploy freely; zero-residual teardown"],
    [[bold("Runtime agent")], "Generic Strands agent container on AgentCore Runtime", "Decoupled — survives spine redeploys untouched"],
  ], [1900, 5540, 3000]),
  callout("Why this matters", [["Because identity is stable and the Runtime discovers the gateway from SSM, you can rebuild the entire spine as often as you like without ever redeploying the Runtime or invalidating safety-reviewer tokens."]], G.colors.TEAL),

  H1("2. Routine operations"),
  H2("2.1 Refresh the spine"),
  P("The safest way to apply most spine changes is a clean rebuild. Destroy leaves identity intact; deploy reuses it."),
  ...codeBlock(["bash lib/engine/destroy.sh agents/pharmacovigilance", "bash lib/engine/deploy.sh  agents/pharmacovigilance", "bash lib/engine/demo.sh    agents/pharmacovigilance   # smoke test: expect 30/30", "bash lib/engine/redteam.sh agents/pharmacovigilance   # adversarial: expect 7/7"]),
  P([bold("Note: "), "run cycles serialized — never two concurrent spine deploys. Deploy from a path without spaces (§7)."]),

  H2("2.2 Change the seriousness rules or a tool"),
  P(["The seriousness criteria and reporting clock live in ", code("agents/pharmacovigilance/tools/assess_seriousness.py"), " (the six ICH E2B(R3) criteria and the expedited/periodic/routine clock logic). Edit the source, then redeploy the spine, which repackages and updates every tool Lambda. For a fast single-tool iteration you can update just one function's code:"]),
  ...codeBlock(["# fast path — update one Lambda's code in place:", "cd agents/pharmacovigilance/tools", "cp assess_seriousness.py lambda_function.py", "python -c \"import zipfile;z=zipfile.ZipFile('f.zip','w');z.write('lambda_function.py');z.close()\"", "aws lambda update-function-code --function-name pv-assess-seriousness \\", "    --zip-file fileb://f.zip --region us-east-1"]),
  callout("Illustrative defaults are not authoritative rules", [["The shipped seriousness criteria and reporting clocks are illustrative regulatory defaults for demonstration. Replace them with the authoritative, market-specific reporting rules (e.g. IND 7-day, EU GVP timelines), under regulatory review, before any real determination. The rules engine is deliberately deterministic and model-free so a qualified person can defend each determination. MedDRA/WHODrug coding and safety-system connectors ship as labeled stubs — replace them with the licensed dictionaries and validated connectors."]], G.colors.AMBER, "FBF3E7"),

  H2("2.3 Change Cedar policies"),
  P(["Policies are declared in the manifest (", code("policies:"), ") and rendered to Cedar statements by ", code("render.py"), ". To add or change a permit/forbid, edit the manifest and redeploy. Two rules to remember:"]),
  bullet([bold("Policies validate against the tool schemas. "), "A policy that references a tool input must match the Gateway's tool definition — deploy the tools before the policies (the engine already orders this)."]),
  bullet([bold("Use the LOG_ONLY → ENFORCE path. "), "The spine attaches the engine in LOG_ONLY, validates, then flips to ENFORCE. For risky policy changes, test in LOG_ONLY first."]),
  P([bold("Cedar reminders: "), code("cognito:groups"), " is a string tag — match with ", code("like \"*pv_reviewer*\""), "; scope resources to ", code("AgentCore::Gateway"), "; a blanket forbid needs ", code("--validation-mode IGNORE_ALL_FINDINGS"), "; ", code("create-policy"), " is asynchronous — poll ", code("get-policy"), ". Policy names are prefixed per agent (", code("pv_mask_before_assess"), ") so multiple agents coexist in one account/region."]),

  H2("2.4 Adjust the Bedrock Guardrail"),
  P(["The output guardrail ", code("pv-pharmacovigilance-guardrail"), " (PHI anonymize + prompt-attack) is created on first deploy and reused by name thereafter. To change its policy, either update it in place or delete and let the next deploy recreate it:"]),
  ...codeBlock(["# change PHI entities / filters, then:", "aws bedrock update-guardrail --guardrail-identifier <id> ... --region us-east-1", "# or force recreation on next deploy:", "aws bedrock delete-guardrail --guardrail-identifier <id> --region us-east-1"]),

  H2("2.5 Swap the drafting model"),
  P([code("draft_narrative"), " uses the model in ", code("DRAFT_MODEL_ID"), " (from the manifest; default ", code("us.anthropic.claude-sonnet-4-5-20250929-v1:0"), "). Point it at another enabled model or inference profile via the manifest and redeploy, or update the Lambda config directly:"]),
  ...codeBlock(["aws lambda update-function-configuration --function-name pv-core-tools \\", "    --environment 'Variables={GUARDRAIL_ID=<id>,GUARDRAIL_VERSION=DRAFT,\\", "        DRAFT_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0}' --region us-east-1"]),
  P([bold("Gotcha: "), "some newer models reject ", code("temperature"), " and ", code("topP"), " together via Converse — the tool sends temperature only. Keep that in mind if you customize inference parameters. Note that ", code("update-function-code"), " preserves env vars, but a full deploy re-applies them from the manifest via the engine's ", code("wire_env"), " step."]),

  H2("2.6 Manage identity"),
  bullet([bold("Add or reset users: "), "re-run ", code("bash lib/engine/deploy_identity.sh agents/pharmacovigilance"), " (idempotent), or use ", code("aws cognito-idp admin-set-user-password"), " to rotate a password."]),
  bullet([bold("Rotate the default passwords "), "before any shared use of the environment."]),
  bullet([bold("Production: "), "federate the sponsor's real identity provider and map workforce roles (safety reviewer / qualified person / senior safety physician) to the ", code("pv_reviewer"), " group / claim, rather than using the built-in test users."]),

  H1("3. The Runtime agent lifecycle"),
  bullet([bold("After an agent or manifest change: "), "from the project root run ", code("bash lib/runtime/_launch.sh agents/pharmacovigilance"), " — the container rebuilds in CodeBuild and the same Runtime ARN is updated with the re-rendered workflow prompt."]),
  bullet([bold("After a spine redeploy: "), "do nothing. The gateway URL rotates, but the agent reads it from SSM ", code("/pv-pharmacovigilance/gateway-url"), " at invoke time, and identity is stable — the Runtime keeps working."]),
  bullet([bold("Only if identity changes "), "(you rebuild the Cognito pool) re-configure the Runtime with the new authorizer and verify with ", code("bash lib/runtime/_verify_rt.sh agents/pharmacovigilance"), "."]),
  bullet([bold("Verify anytime: "), code("bash lib/runtime/_invoke.sh agents/pharmacovigilance pv_reviewer"), " and the outsider negative case."]),

  H1("4. Monitoring & observability"),
  P("Observability is enabled on the Runtime (OpenTelemetry) and every governed step is logged with the acting identity."),
  bullet([bold("Runtime logs: "), code("aws logs tail /aws/bedrock-agentcore/runtimes/pv_runtime_agent-<id>-DEFAULT --since 1h"), " — per-step, identity-tagged, OTel-correlated (trace/span IDs)."]),
  bullet([bold("GenAI dashboard: "), "the CloudWatch GenAI Observability console surfaces agent/tool spans (requires CloudWatch Transaction Search enabled in the account)."]),
  bullet([bold("Spine smoke test: "), code("bash lib/engine/demo.sh agents/pharmacovigilance"), " is the fastest health check — 30/30 means the whole governed path is intact; ", code("redteam.sh"), " (7/7) confirms it holds under attack."]),
  bullet([bold("Watch for: "), "repeated ", code("ACCESS DENIED"), " (identity/authorization drift), ", code("draft failed"), " (model access or inference-parameter issues), guardrail blocks on the narrative, openFDA egress falling back to the deterministic aggregate (network/rate-limit), and any ", code("assess"), " or ", code("record_causality"), " call arriving with ", code("deidentified=false"), " (a masking-order regression)."]),

  H1("5. Audit-evidence management"),
  P(["The audit lives in two places: the append-only DynamoDB ledger ", code("pv-audit"), " (point-in-time recovery enabled) and the S3 Object Lock bucket ", code("pv-audit-worm-<acct>-<region>"), ". Together they are the 21 CFR Part 11 / GxP evidence trail."]),
  bullet([bold("Retention: "), "the reference bucket uses Object Lock in GOVERNANCE mode with a 1-day default retention for easy evaluation. For production, raise the retention period (and consider COMPLIANCE mode) to match your records-management and regulatory-retention schedule."]),
  bullet([bold("Export before teardown: "), code("destroy.sh"), " deletes the ledger and bucket. Export first — scan the DynamoDB table and sync the bucket to a retained location:"]),
  ...codeBlock(["aws dynamodb scan --table-name pv-audit --region us-east-1 > audit-ledger.json", "aws s3 sync s3://pv-audit-worm-<acct>-us-east-1 ./audit-evidence/"]),
  callout("Tamper-evidence is by construction", [["The tool role can write audit records but is denied delete, update, and Object-Lock bypass. Only an administrator with an explicit governance-bypass can remove locked objects — which is exactly what the teardown script does."]], G.colors.TEAL),

  H1("6. Teardown & rebuild"),
  bullet([bold("Spine only (keep identity + Runtime): "), code("bash lib/engine/destroy.sh agents/pharmacovigilance"), " — zero residual, including the Object-Lock bucket."]),
  bullet([bold("Clean refresh: "), "destroy then deploy. Identity and the Runtime are unaffected."]),
  bullet([bold("Full removal: "), "destroy the spine, delete the Cognito pool, then ", code("agentcore destroy"), " from ", code("lib/runtime/"), " (also prune the ECR repo and CodeBuild project)."]),
  bullet([bold("Stop pending sign-off executions first. "), "If you invoked the agent and left a submission PENDING (never approved), stop that RUNNING Step Functions execution before teardown — otherwise it keeps ", code("pv-signoff"), " stuck DELETING and blocks the next deploy (see §7)."]),

  H1("7. Troubleshooting & known gotchas"),
  table(["Symptom", "Cause & fix"], [
    ["Deploy fails: StateMachineDeleting on pv-signoff", "A prior invoke left a waitForTaskToken sign-off execution RUNNING (a PENDING submission that was never approved). It keeps the pv-signoff state machine stuck DELETING, which blocks re-creating one of the same name. Stop the leftover execution (list-executions --status-filter RUNNING → stop-execution), let deletion finish, then redeploy. Stop pending sign-off executions BEFORE teardown to avoid this."],
    ["ConflictException: Policy with the same name already exists", "A second template agent was deployed without prefixing while another was live (policy names are unique per account/region). The template prefixes policies per agent (pv_mask_before_assess); deploy prefixed, or run one agent at a time (destroy the live agent first)."],
    ["Git-Bash 'cd: …/pharmacovigilance: No such file or directory' (Windows)", "The project path contains a space and PowerShell mangled the quoting. Deploy from a no-space path (e.g. pv_icsr_agent)."],
    ["Detached deploy/launch does nothing / empty log (Windows)", "Start-Process -ArgumentList was passed a multi-token array, which PowerShell joins unquoted and drops the command at the first space. Pass the runner as ONE space-free argument: Start-Process -FilePath '…\\bash.exe' -ArgumentList '/c/…/runner.sh' -WindowStyle Hidden. The runner log is block-buffered — verify progress by polling AWS state, not by tailing the log."],
    ["Control Lambda hits the wrong table/bucket (AccessDenied on audit)", "The control's resource env wasn't wired. The engine's wire_env step sets AUDIT_TABLE / AUDIT_BUCKET / PENDING_TABLE / SM_NAME on every control + sign-off Lambda; a full deploy re-applies it."],
    ["render.py FileNotFoundError on manifest.yaml (Windows)", "A helper changed directory before resolving the relative agent path. The helpers resolve the agent dir to an absolute path before cd; run them from the project root."],
    ["Runtime invoke returns 424 / gateway not found", "SSM parameter missing or stale. Confirm /pv-pharmacovigilance/gateway-url exists and matches the live gateway; the deploy sets MSYS_NO_PATHCONV=1 so the name isn't mangled on Windows."],
    ["openfda_lookup returns 'fallback aggregate'", "openFDA egress failed or was rate-limited (or the drug had no FAERS results). The tool fails soft to a deterministic non-PHI aggregate so the governed workflow still proceeds; confirm outbound HTTPS if live background is required."],
    ["'draft failed: ValidationException ... temperature and top_p'", "The model rejects both parameters together via Converse. Send temperature only."],
    ["agentcore configure/launch crashes with a Unicode or console error", "Windows codepage. Export PYTHONIOENCODING=utf-8, PYTHONUTF8=1, AGENTCORE_SUPPRESS_RECOMMENDATION=1 (set in the helper scripts)."],
    ["'Invalid version id' during bucket teardown", "Git-Bash trailing \\r on the version id. Pipe list output through tr -d '\\r' (handled in destroy.sh)."],
    ["demo shows a tool ALLOW but no result field", "The MCP client truncates long output (~200 chars). Put short proof fields first in the tool response (the tools already do), or grep an early field."],
  ], [3100, 7340]),

  H1("8. Cost & housekeeping"),
  bullet([bold("Idle cost is low: "), "Lambdas, DynamoDB (on-demand), and the Gateway are pay-per-use; the largest steady item is the Runtime container and any provisioned observability. The openFDA lookup is free (public API)."]),
  bullet([bold("Tear down between evaluations "), "to keep costs near zero — ", code("destroy.sh"), " leaves only the stable identity and the (idle) Runtime."]),
  bullet([bold("CodeBuild & ECR: "), "each Runtime deploy pushes an image tag to ECR; prune old tags periodically."]),
  bullet([bold("Region: "), "keep all components in us-east-1 for the reference deployment — Comprehend, the Bedrock models, and AgentCore are co-located there. For a GovCloud target, confirm per-service availability first."]),
  spacer(),
  P([{ text: "End of maintenance guide. See the SA Deployment Runbook for first-time setup and the Regulatory-Adherence Guide for the control mapping.", italics: true, color: G.colors.MUTED }]),
];

const doc = makeDoc(cover, body, "Pharmacovigilance AgentCore · Maintenance & Operations Guide");
Packer.toBuffer(doc).then((b) => { require("fs").writeFileSync("PV-AgentCore-Maintenance.docx", b); console.log("wrote maintenance"); });
