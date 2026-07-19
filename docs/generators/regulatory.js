const G = require("./guides.js");
const { H1, H2, H3, P, bold, code, bullet, num, codeBlock, callout, table, spacer, coverAndToc, makeDoc, Packer } = G;

const cover = coverAndToc(
  ["Regulatory-Adherence Guide"],
  "Pharmacovigilance ICSR Intake Agent on Amazon Bedrock AgentCore",
  "How the governed pharmacovigilance (drug-safety) accelerator maps to ICH E2B(R3), EU GVP, FDA 21 CFR 314.80 / 312.32, FDA 21 CFR Part 11, HIPAA, and GxP / computer-system validation (CSV/CSA) — the controls it provides, the evidence it produces, and the validation that remains the sponsor's responsibility. Accelerator reference; not a compliance certification, a validated system, or regulatory advice. Version 1.0 · 2026.",
  ["1. Purpose & scope", "2. The regulated workflow", "3. Frameworks in scope", "4. Reporting-obligation mapping (ICH E2B(R3) / GVP / FDA)", "5. HIPAA (PHI safeguarding) mapping", "6. 21 CFR Part 11 & GxP / CSV mapping", "7. Separation of duties & the human gate", "8. Shared responsibility", "9. Disclaimer"]
);

const body = [
  H1("1. Purpose & scope"),
  P("This guide maps the controls implemented in the pharmacovigilance ICSR-intake accelerator to the requirements a drug-safety organization must satisfy. It is written for the pharmacovigilance, quality, privacy, security, and regulatory-affairs stakeholders who decide whether an AI-assisted case-intake workflow can be adopted."),
  P([bold("What this guide is: "), "a control-to-requirement mapping showing how the accelerator supports adherence, what evidence it produces, and where the sponsor's own validation is required."]),
  P([bold("What this guide is not: "), "a certification, an attestation, a validated (CSV/CSA) system, or legal/regulatory advice. Adopting this accelerator does not by itself make a system compliant or a reportability determination correct. Authorization to operate, computer-system validation, and the correctness of market-specific reporting rules and narratives remain the sponsor's responsibility (§8)."]),
  callout("Design principle", [["Every control below follows one rule from the regulated workflow: a qualified safety reviewer (qualified person) makes and commits the causality/reportability determination and the ICSR submission — the agent intakes, pulls FAERS background, de-identifies PHI, assesses seriousness, and drafts, but never self-submits. The security design exists to enforce that rule and to produce the tamper-evident audit trail."]], G.colors.TEAL),

  H1("2. The regulated workflow"),
  P("Pharmacovigilance intake decides whether an adverse-event report is a serious ICSR, how fast it must reach the regulator, and how it is coded and narrated. When an adverse-event source arrives, a regulated workflow runs: intake the non-PHI decision fields, pull authoritative FAERS background, de-identify PHI, assess ICH E2B(R3) seriousness and the reporting clock (expedited 15-day vs. periodic vs. routine), draft a CIOMS/ICSR narrative, obtain a qualified person's review and sign-off, and commit the submission to the safety system of record."),
  P("The accelerator automates the intake, background-lookup, de-identification, assessment, and drafting steps under governance, and pauses at a human sign-off gate before any submission is committed. Three regulatory areas bear on this workflow, mapped in §§4–6."),

  H1("3. Frameworks in scope"),
  table(["Framework", "Relevance to the workflow"], [
    [[bold("ICH E2B(R3) / GVP")], "ICSR data elements and the seriousness definition; EU Good Pharmacovigilance Practices for case management and expedited/periodic reporting; the qualified-person determination."],
    [[bold("FDA 21 CFR 314.80 / 312.32")], "Postmarket 15-day expedited reporting of serious, unexpected adverse experiences (314.80) and IND safety reporting (312.32, 7-day / 15-day) — the reporting-clock obligations."],
    [[bold("FDA 21 CFR Part 11 / GxP")], "Electronic records and signatures: attributable, tamper-evident audit trails, access controls, and computer-system validation (CSV/CSA) for GxP systems."],
    [[bold("HIPAA")], "Safeguarding protected health information (PHI) in the adverse-event case — de-identification, access, and audit controls."],
  ], [2900, 7540]),

  H1("4. Reporting-obligation mapping (ICH E2B(R3) / GVP / FDA)"),
  P("The core safety obligation is to identify a serious ICSR, apply the correct reporting clock, code and narrate it, and submit it within the deadline — with a defensible, documented basis. The accelerator produces the seriousness/clock determination, the FAERS background, and the narrative, and the tamper-evident record a submission depends on; the correctness of market-specific reporting rules and the narrative remain the sponsor's responsibility."),
  table(["Reporting requirement", "How the accelerator addresses it", "Evidence / sponsor responsibility"], [
    ["A defensible seriousness determination (ICH E2B(R3))", "assess_seriousness is a deterministic rules engine (no model, no licensed data) over the six ICH E2B(R3) / 21 CFR 314.80 seriousness criteria (death, life-threatening, hospitalization, disability, congenital anomaly, other medically important), returning SERIOUS / non-serious with the criteria met.", "The auditable determination basis; sponsor owns the authoritative, market-specific criteria configuration."],
    ["The correct reporting clock (21 CFR 314.80 / 312.32)", "The same engine returns the reporting category and clock — EXPEDITED (15-day) for serious + unexpected, PERIODIC for serious + listed, ROUTINE for non-serious — deterministically, and flags when expectedness was unknown and treated conservatively as unlisted.", "The reporting_category + clock_days fields; sponsor configures the authoritative market clocks (e.g. IND 7-day)."],
    ["Authoritative safety background", "openfda_lookup fetches aggregate, non-PHI FAERS background (report count + top MedDRA reaction terms) for the suspect product from the live openFDA drug-event API; it runs before masking (the drug name is non-PHI) and fails soft to a deterministic aggregate.", "The FAERS background attached to the case; sponsor owns signal interpretation and the licensed MedDRA/WHODrug coding."],
    ["A coded, narrated ICSR (CIOMS)", "draft_narrative produces a de-identified CIOMS/ICSR narrative through a fail-closed output guardrail, on de-identified data only.", [{ text: "Sponsor: ", bold: true }, "medical review of the narrative, MedDRA/WHODrug coding, and E2B(R3) formatting."]],
    ["No double-reporting of a case", "detect_duplicate deterministically compares a de-identified case key (product | event | onset | reporter) against already-received keys and HOLDs a suspected duplicate so the same case isn't reported twice.", "The duplicate HOLD; a safety reviewer confirms merge/close."],
    ["The submission is made by a qualified person", "The submit is performed by a qualified safety reviewer at the human sign-off gate; the agent cannot finalize (Cedar no_self_submit).", "Enforced by the Step Functions gate + the forbid (see §7)."],
    ["Causality / reportability stays with a senior physician", "record_causality PREPARES a documented causality/reportability determination and requires a different senior safety physician to approve; commit_causality is forbidden to the agent (no_self_causality_commit).", "Cedar forbid + the human gate; sponsor owns the clinical causality methodology."],
  ], [2650, 4090, 3700]),

  H1("5. HIPAA (PHI safeguarding) mapping"),
  P("The adverse-event case contains protected health information — patient name, date of birth, address, identifiers, and contact details. HIPAA requires strict safeguarding: minimum-necessary use, restricted access, audit of access, and protection against unauthorized disclosure. The accelerator de-identifies PHI before the model or the audit sees it, and constrains access by least privilege. A signed BAA and the sponsor's HIPAA program remain prerequisites for handling real PHI."),
  table(["HIPAA safeguard", "How the accelerator addresses it", "Evidence / sponsor responsibility"], [
    ["De-identify / minimum necessary", "The mask_pii tool runs Amazon Comprehend DetectPiiEntities to remove PHI (name, DOB, address, identifiers, contact info) before drafting and before the audit — fail-closed: if masking cannot run, no assessment or draft is produced.", "Comprehend detection; demo proves PHI redaction and the fail-closed path (deidentified=false is refused)."],
    ["Restrict access to authorized users", "Amazon Cognito authentication with AgentCore Policy (Cedar) deny-by-default; every tool call is authorized against the safety reviewer's identity and pv_reviewer group.", "Cognito pool + Cedar policies; deny strings name the firing policy."],
    ["Audit every access and disclosure", "Every governed action writes a tamper-evident record capturing INTENT → COMMITTED with a content hash and timestamp; duplicates are rejected.", "The pv-audit ledger + WORM bucket; demo proves write-once + duplicate rejection."],
    ["Minimum necessary / least privilege", "The agent acts only within the intersection of its own and the reviewer's permissions; the finalize and commit actions are forbidden to the agent entirely.", "Cedar least-privilege permit/forbid policies."],
    ["Protect PHI in transit and at rest", "Runs inside the sponsor's AWS account; PHI is masked before any model call; records are Object-Lock protected.", [{ text: "Sponsor: ", bold: true }, "Business Associate Agreement, KMS/encryption, network controls, HIPAA program & risk analysis."]],
  ], [2500, 4240, 3700]),

  H1("6. 21 CFR Part 11 & GxP / CSV mapping"),
  P("A pharmacovigilance system is a GxP computer system: it must produce attributable, tamper-evident electronic records, control who can act, and be validated for its intended use. The accelerator implements the technical record, access, and integrity controls; the validation package (CSV/CSA) and authorization to operate are the sponsor's."),
  table(["Part 11 / GxP area", "How the accelerator addresses it", "Status / sponsor responsibility"], [
    ["Attributable, tamper-evident audit trail (Part 11)", "Immutable WORM audit of every decision and state change (append-only DynamoDB + S3 Object Lock), identity-tagged and OTel-correlated; the writing principal is denied delete, update, and retention bypass.", "Live in ENFORCE; sponsor sets retention and log aggregation to its records schedule."],
    ["Access & authority checks (Part 11)", "Deny-by-default Cedar authorization at the Gateway; authenticated identity via Cognito/IdP; least-privilege permits scoped to the pv_reviewer group.", "Live; sponsor federates its IdP and maps the qualified-person role."],
    ["Signature / separation of duties (Part 11)", "The submission is committed only by a different qualified person through a Step Functions gate, with a bound, single-use token — an attributable, second-person authorization.", "Live; sponsor maps the qualified-person signature authority."],
    ["System & information integrity (GxP)", "Fail-closed PHI masking and a fail-closed Bedrock output guardrail (PHI anonymize + prompt-attack HIGH) on every drafted narrative.", "Live; sponsor tunes guardrail policy."],
    ["Computer-system validation (CSV/CSA)", "Reproducible, manifest-driven infrastructure-as-code and a 32-check governance test harness that runs in ENFORCE mode, plus a reusable red-team harness (7/7 — governance holds under attack).", [{ text: "Sponsor: ", bold: true }, "the CSV/CSA validation package, IQ/OQ/PQ, and authorization to operate."]],
  ], [2500, 4240, 3700]),

  H1("7. Separation of duties & the human sign-off gate"),
  P("The single most important control for pharmacovigilance integrity is that a qualified person — not the agent — makes and commits the causality/reportability determination and the ICSR submission. The accelerator enforces this structurally:"),
  bullet([bold("The agent cannot submit or commit. "), "finalize_submission and commit_causality are forbidden by Cedar policies (no_self_submit, no_self_causality_commit) and hidden from the agent entirely; they are not reachable as tools."]),
  bullet([bold("Submission runs only through the gate. "), "The sanctioned path is a request for sign-off that starts a Step Functions workflow, which pauses until a qualified person approves."]),
  bullet([bold("The approver must differ from the requester. "), "A separation-of-duties check rejects self-approval — and for causality, a different senior safety physician must approve."]),
  bullet([bold("Approvals are single-use. "), "The approval token is consumed against a durable ledger; it cannot be replayed."]),
  bullet([bold("Both ends are audited. "), "An INTENT record is written when sign-off is requested and a COMMITTED record when the submission is finalized."]),
  callout("Proven live", [["In ENFORCE mode: a reviewer's request to self-approve is blocked as a separation-of-duties violation; a different qualified person's approval succeeds; the submission finalizes only after approval; and re-using the token is rejected. Proven across 32/32 governance checks and a 7/7 red-team harness."]], G.colors.MINT, "E9F5EF"),

  H1("8. Shared responsibility"),
  P("The accelerator provides the pattern, the controls, and the evidence. Authorization, validation, and the connection to the sponsor's real systems and rules remain the sponsor's."),
  table(["The accelerator provides", "The sponsor is responsible for"], [
    ["The governed agent, Cedar policies, and tools", "Authorization to operate and computer-system validation (CSV/CSA)"],
    ["Fail-closed PHI de-identification", "IdP federation and qualified-person role mapping to the workforce"],
    ["The human sign-off workflow (separation of duties)", "Validated connectors to the safety system of record (Argus / ArisG / E2B gateway)"],
    ["The deterministic seriousness / reporting-clock engine (illustrative defaults)", "The authoritative market-specific reporting rules and their regulatory review"],
    ["The immutable WORM audit design", "Data-retention policy and records management"],
    ["The live openFDA FAERS integration (non-PHI background)", "Licensed MedDRA / WHODrug coding dictionaries and signal interpretation"],
    ["Reproducible IaC + the 32-check governance harness (7/7 red-team)", "Medical review of narratives, causality methodology, and the CSV validation package"],
    ["Documentation (this guide, the runbook, maintenance)", "Business Associate Agreement and the HIPAA safeguarding program, where PHI is used"],
  ], [5220, 5220]),

  H1("9. Disclaimer"),
  P([{ text: "This document describes how an accelerator's technical controls map to selected regulatory requirements. It is provided for evaluation and architecture purposes only. It is not legal, regulatory, or compliance advice, and it is not a certification, an attestation, or a validated (CSV/CSA) system for compliance with ICH E2B(R3), EU GVP, FDA 21 CFR 314.80 / 312.32, FDA 21 CFR Part 11, HIPAA, GxP, or any other authority. Reportability determinations and ICSR submissions have direct patient-safety and regulatory consequences; the correctness of seriousness criteria, reporting clocks, coding, and narratives, and the lawfulness of the process, depend on the sponsor's validated implementation, policies, medical and regulatory review, and use. The seriousness thresholds and reporting clocks shipped with the accelerator are illustrative regulatory defaults, not authoritative market rules, and MedDRA/WHODrug coding and safety-system connectors ship as labeled stubs. Consult your pharmacovigilance, quality, regulatory, and privacy functions before processing real patient or adverse-event data.", italics: true, color: G.colors.MUTED, size: 19 }]),
];

const doc = makeDoc(cover, body, "Pharmacovigilance AgentCore · Regulatory-Adherence Guide");
Packer.toBuffer(doc).then((b) => { require("fs").writeFileSync("PV-AgentCore-Regulatory-Adherence.docx", b); console.log("wrote regulatory"); });
