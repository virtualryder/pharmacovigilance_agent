/* Customer deck — Governed AI for Pharmacovigilance (accelerator on AWS AgentCore) */
const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.layout = "LAYOUT_WIDE";
p.author = "Pharmacovigilance AgentCore Accelerator";
p.title = "Governed AI for Pharmacovigilance";

const NAVY = "0E2A47", NAVY2 = "143A5E", TEAL = "1C7293", MINT = "02C39A", AMBER = "E8A13A";
const INK = "1A2733", CLOUD = "F4F7FA", CARD = "FFFFFF", MUTED = "6B7A8A", LINE = "D9E1E8", WHITE = "FFFFFF", ICE = "CADCFC";
const TF = "Cambria", BF = "Calibri";
const sh = (o = {}) => Object.assign({ type: "outer", color: "0A1F33", blur: 9, offset: 3, angle: 90, opacity: 0.22 }, o);
function bg(s, c) { s.background = { color: c }; }
function footer(s, n) {
  s.addText("Governed AI for Pharmacovigilance · on Amazon Bedrock AgentCore", { x: 0.6, y: 7.06, w: 9, h: 0.3, color: MUTED, fontSize: 8, fontFace: BF, margin: 0 });
  s.addText(String(n).padStart(2, "0"), { x: 12.4, y: 7.02, w: 0.4, h: 0.3, align: "right", color: MUTED, fontSize: 9, fontFace: BF, margin: 0 });
}
function title(s, t, color = INK) { s.addText(t, { x: 0.6, y: 0.45, w: 12.1, h: 0.9, fontFace: TF, fontSize: 27, bold: true, color, valign: "top", margin: 0 }); }
function eyebrow(s, t, color = TEAL) { s.addText(t.toUpperCase(), { x: 0.62, y: 0.28, w: 12, h: 0.3, fontFace: BF, fontSize: 11, bold: true, color, charSpacing: 2, margin: 0 }); }
function card(s, x, y, w, h, fill = CARD, radius = 0.09) { s.addShape(p.ShapeType.roundRect, { x, y, w, h, fill: { color: fill }, line: { color: LINE, width: 1 }, rectRadius: radius, shadow: sh() }); }
function circle(s, x, y, d, fill, txt, txtColor = WHITE, fs = 16) {
  s.addShape(p.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: fill }, line: { type: "none" }, shadow: sh({ blur: 6, offset: 2, opacity: 0.18 }) });
  if (txt !== undefined) s.addText(txt, { x, y, w: d, h: d, align: "center", valign: "middle", color: txtColor, fontFace: BF, fontSize: fs, bold: true, margin: 0 });
}

/* 1. TITLE */
(() => {
  const s = p.addSlide(); bg(s, NAVY);
  s.addShape(p.ShapeType.ellipse, { x: 9.4, y: -1.9, w: 6.2, h: 6.2, fill: { color: NAVY2 }, line: { type: "none" } });
  s.addShape(p.ShapeType.ellipse, { x: 10.4, y: -0.9, w: 4.2, h: 4.2, fill: { color: "18466F" }, line: { type: "none" } });
  s.addShape(p.ShapeType.ellipse, { x: 11.25, y: -0.05, w: 2.5, h: 2.5, fill: { color: TEAL }, line: { type: "none" } });
  s.addText("PHARMACOVIGILANCE  ·  GOVERNED AI ON AWS", { x: 0.7, y: 1.4, w: 11, h: 0.4, fontFace: BF, fontSize: 13, bold: true, color: MINT, charSpacing: 2, margin: 0 });
  s.addText("Governed AI for\nPharmacovigilance Intake", { x: 0.66, y: 2.0, w: 11.2, h: 2.0, fontFace: TF, fontSize: 42, bold: true, color: WHITE, lineSpacingMultiple: 1.02, margin: 0 });
  s.addText("An accelerator that intakes, de-identifies, assesses, and drafts adverse-event ICSRs under strict governance — fail-closed on PHI, tamper-proof by design, and never able to submit on its own. Built on Amazon Bedrock AgentCore, running in your AWS account.",
    { x: 0.7, y: 4.15, w: 10.2, h: 1.3, fontFace: BF, fontSize: 15.5, color: ICE, lineSpacingMultiple: 1.18, margin: 0 });
  const chips = ["Compliant by construction", "Runs in your account", "Qualified-person-in-command"];
  let cx = 0.7;
  chips.forEach((c) => {
    const w = 0.42 + c.length * 0.1;
    s.addShape(p.ShapeType.roundRect, { x: cx, y: 5.8, w, h: 0.5, fill: { color: NAVY2 }, line: { color: TEAL, width: 1 }, rectRadius: 0.25 });
    s.addText(c, { x: cx, y: 5.8, w, h: 0.5, align: "center", valign: "middle", color: WHITE, fontFace: BF, fontSize: 12, bold: true, margin: 0 });
    cx += w + 0.25;
  });
  s.addNotes("Frame for a pharmacovigilance, quality, privacy, and compliance audience. The promise is governance first: this agent helps safety reviewers without ever stepping outside the controls GxP and 21 CFR Part 11 expect.");
})();

/* 2. YOUR CHALLENGE */
(() => {
  const s = p.addSlide(); bg(s, CLOUD);
  eyebrow(s, "Your challenge");
  title(s, "Case volume rises. Every intake step must stay compliant.");
  s.addText("Adverse-event reports keep coming, intake is largely manual, and the reporting clocks don't wait — a serious, unexpected case is a 15-day expedited report. AI could take the assembly load off safety reviewers — but only if it operates entirely inside your compliance envelope.",
    { x: 0.62, y: 1.5, w: 12.0, h: 0.85, fontFace: BF, fontSize: 15, color: INK, lineSpacingMultiple: 1.15, margin: 0 });
  const items = [
    ["Volume & clocks", "Rising case counts against fixed expedited (15-day) and periodic reporting deadlines.", TEAL],
    ["Manual assembly", "Skilled safety reviewers spend time on extraction, coding, and narrative drafting — not judgment.", AMBER],
    ["Zero compliance slack", "PHI handling, GxP audit trails, and the qualified-person determination leave no room for an ungoverned tool.", MINT],
  ];
  const w = 3.86, gap = 0.24, x0 = 0.62, y = 2.8, h = 3.4;
  items.forEach((it, i) => {
    const x = x0 + i * (w + gap);
    card(s, x, y, w, h);
    circle(s, x + 0.32, y + 0.34, 0.62, it[2], String(i + 1));
    s.addText(it[0], { x: x + 1.12, y: y + 0.36, w: w - 1.3, h: 0.6, fontFace: TF, fontSize: 17, bold: true, color: INK, valign: "middle", margin: 0 });
    s.addText(it[1], { x: x + 0.34, y: y + 1.3, w: w - 0.66, h: 1.9, fontFace: BF, fontSize: 13.5, color: "34434F", valign: "top", lineSpacingMultiple: 1.14, margin: 0 });
  });
  footer(s, 2);
  s.addNotes("Meet the safety org where they are: case-volume pressure, manual toil, and a compliance bar that rules out ungoverned AI. The rest of the deck resolves that tension.");
})();

/* 3. THE IDEA */
(() => {
  const s = p.addSlide(); bg(s, NAVY);
  eyebrow(s, "The idea", MINT);
  s.addText("An agent that does the assembly — and is structurally incapable of overstepping", { x: 0.6, y: 0.45, w: 12.1, h: 1.3, fontFace: TF, fontSize: 25, bold: true, color: WHITE, lineSpacingMultiple: 1.0, margin: 0 });
  s.addText("The agent intakes the report, pulls FAERS background, de-identifies it, runs a transparent seriousness assessment, drafts the CIOMS narrative, and prepares it for review. Then it stops. A qualified person makes the determination and submits — the agent cannot.",
    { x: 0.62, y: 1.95, w: 11.8, h: 0.9, fontFace: BF, fontSize: 15, color: ICE, lineSpacingMultiple: 1.16, margin: 0 });
  const cols = [
    ["The agent DOES", MINT, ["Intake & extract the decision fields", "Pull aggregate FAERS background (non-PHI)", "De-identify the case (fail-closed)", "Run the seriousness + reporting-clock engine", "Draft the narrative + request sign-off"]],
    ["The agent NEVER", "C0392B", ["See un-masked PHI without controls", "Assess or draft on un-de-identified data", "Approve its own work", "Submit the ICSR to the regulator", "Commit causality or alter the audit trail"]],
  ];
  cols.forEach((c, ci) => {
    const x = 0.62 + ci * 6.16;
    card(s, x, 3.05, 5.95, 3.15, NAVY2, 0.1);
    s.addText(c[0].toUpperCase(), { x: x + 0.35, y: 3.3, w: 5.3, h: 0.4, fontFace: BF, fontSize: 13, bold: true, color: c[1], charSpacing: 1.5, margin: 0 });
    c[2].forEach((t, i) => {
      const y = 3.85 + i * 0.46;
      circle(s, x + 0.35, y, 0.3, c[1], ci === 0 ? "✓" : "✕", ci === 0 ? NAVY : WHITE, 11);
      s.addText(t, { x: x + 0.78, y: y - 0.05, w: 4.95, h: 0.4, valign: "middle", fontFace: BF, fontSize: 12.5, color: WHITE, margin: 0 });
    });
  });
  footer(s, 3);
  s.addNotes("The 'does vs never' framing earns trust with privacy and quality teams. The 'never' column is enforced by policy, not by prompt — that's the difference.");
})();

/* 4. HOW IT FITS */
(() => {
  const s = p.addSlide(); bg(s, CLOUD);
  eyebrow(s, "How it fits your workflow");
  title(s, "Agent-assisted intake, qualified-person-in-command");
  const steps = [["Intake", "the ICSR", TEAL, "AGENT"], ["De-identify", "the PHI (fail-closed)", TEAL, "AGENT"], ["Assess & draft", "seriousness + narrative", TEAL, "AGENT"], ["Review &", "sign off", AMBER, "QUALIFIED PERSON"], ["Submit", "the ICSR", NAVY, "AFTER APPROVAL"]];
  const n = steps.length, w = 2.16, gap = 0.28, x0 = 0.62, y = 2.05, h = 1.9;
  steps.forEach((st, i) => {
    const x = x0 + i * (w + gap);
    s.addShape(p.ShapeType.roundRect, { x, y, w, h, fill: { color: st[2] }, line: { type: "none" }, rectRadius: 0.1, shadow: sh({ blur: 6, offset: 2, opacity: 0.16 }) });
    s.addText(st[3], { x: x + 0.1, y: y + 0.22, w: w - 0.2, h: 0.3, align: "center", fontFace: BF, fontSize: 9.5, bold: true, color: i === 3 ? NAVY : ICE, charSpacing: 1, margin: 0 });
    s.addText(st[0], { x: x + 0.1, y: y + 0.62, w: w - 0.2, h: 0.5, align: "center", fontFace: TF, fontSize: 17, bold: true, color: WHITE, margin: 0 });
    s.addText(st[1], { x: x + 0.12, y: y + 1.12, w: w - 0.24, h: 0.6, align: "center", fontFace: BF, fontSize: 11.5, color: WHITE, valign: "top", lineSpacingMultiple: 1.0, margin: 0 });
    if (i < n - 1) s.addText("›", { x: x + w - 0.06, y, w: gap + 0.12, h, align: "center", valign: "middle", color: MUTED, fontSize: 20, bold: true, margin: 0 });
  });
  card(s, 0.62, 4.35, 12.1, 1.85, CARD, 0.1);
  s.addText("Every agent action passes an access-control check before it runs, and the two consequential steps — assessing or drafting on un-masked data, and submitting the ICSR — are impossible for the agent to reach on its own.",
    { x: 0.95, y: 4.62, w: 11.4, h: 0.9, fontFace: BF, fontSize: 14.5, color: INK, valign: "top", lineSpacingMultiple: 1.16, margin: 0 });
  s.addText([
    { text: "Your safety reviewers stay in command — ", options: { bold: true, color: TEAL } },
    { text: "the agent removes the assembly toil, not the judgment.", options: { color: "34434F" } },
  ], { x: 0.95, y: 5.5, w: 11.4, h: 0.5, fontFace: BF, fontSize: 14, italic: true, margin: 0 });
  footer(s, 4);
  s.addNotes("Blue = agent-assisted, amber = the qualified person, navy = the gated submit. Emphasize: judgment stays human; toil goes to the agent.");
})();

/* 5. CONTROLS THAT LET YOU SAY YES */
(() => {
  const s = p.addSlide(); bg(s, CLOUD);
  eyebrow(s, "For your quality, compliance & privacy teams");
  title(s, "The controls that let you say yes");
  const rows = [
    ["Fail-closed PHI de-identification", "The case is de-identified before the model or the audit sees it. No masking → no assessment, no draft.", "HIPAA"],
    ["Immutable audit trail", "Append-only, write-once evidence of every decision and state change — the writer can't alter it.", "21 CFR Part 11"],
    ["Human sign-off (separation of duties)", "A different qualified person approves with a bound, single-use token before any ICSR is submitted.", "GVP · QPPV"],
    ["Deny-by-default authorization", "Every tool call is checked against policy; nothing runs unless explicitly permitted.", "GxP access"],
    ["Verified identity", "Actions are bound to an authenticated safety reviewer via your identity provider.", "Part 11 · IA"],
  ];
  const x = 0.62, w = 12.1, y0 = 1.55, rh = 0.9;
  rows.forEach((r, i) => {
    const y = y0 + i * rh;
    card(s, x, y, w, rh - 0.14, CARD, 0.08);
    circle(s, x + 0.3, y + (rh - 0.14) / 2 - 0.26, 0.52, i < 3 ? MINT : TEAL, String(i + 1), i < 3 ? NAVY : WHITE, 15);
    s.addText(r[0], { x: x + 1.05, y: y + 0.08, w: 4.7, h: rh - 0.3, valign: "middle", fontFace: BF, fontSize: 14.5, bold: true, color: INK, lineSpacingMultiple: 0.98, margin: 0 });
    s.addText(r[1], { x: x + 5.9, y: y + 0.08, w: 4.5, h: rh - 0.3, valign: "middle", fontFace: BF, fontSize: 12.5, color: "44535F", lineSpacingMultiple: 1.06, margin: 0 });
    s.addShape(p.ShapeType.roundRect, { x: x + 10.55, y: y + (rh - 0.14) / 2 - 0.2, w: 1.4, h: 0.44, fill: { color: "EAF1F4" }, line: { type: "none" }, rectRadius: 0.1 });
    s.addText(r[2], { x: x + 10.55, y: y + (rh - 0.14) / 2 - 0.2, w: 1.4, h: 0.44, align: "center", valign: "middle", fontFace: BF, fontSize: 10.5, bold: true, color: TEAL, margin: 0 });
  });
  footer(s, 5);
  s.addNotes("This is the slide your quality, compliance, and privacy stakeholders came for: each control mapped to the requirement it satisfies. Walk it slowly.");
})();

/* 6. IN YOUR BOUNDARY */
(() => {
  const s = p.addSlide(); bg(s, NAVY);
  eyebrow(s, "Where it runs", MINT);
  s.addText("Native on Amazon Bedrock AgentCore — inside your AWS boundary", { x: 0.6, y: 0.45, w: 12, h: 0.85, fontFace: TF, fontSize: 25, bold: true, color: WHITE, margin: 0 });
  s.addText("The governance isn't a bolt-on. Identity, deny-by-default authorization (Cedar), governed tools, and the agent runtime are native AWS services — deployed in your account, on your data, under your controls.",
    { x: 0.62, y: 1.45, w: 11.9, h: 0.85, fontFace: BF, fontSize: 14.5, color: ICE, lineSpacingMultiple: 1.16, margin: 0 });
  const items = [
    ["In your account", "Deploys via infrastructure-as-code into your AWS environment — your VPC, your keys, your logs."],
    ["Your data stays yours", "PHI is masked before any model call; nothing leaves your boundary for third-party processing."],
    ["Your identity provider", "Federate your existing IdP; actions are bound to your safety reviewers and your roles."],
    ["Reproducible & reversible", "One-command deploy, one-command teardown with zero residual — easy to evaluate, easy to remove."],
  ];
  const w = 5.95, gap = 0.24, x0 = 0.62, y0 = 2.55, h = 1.75;
  items.forEach((it, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = x0 + col * (w + gap), y = y0 + row * (h + 0.2);
    s.addShape(p.ShapeType.roundRect, { x, y, w, h, fill: { color: NAVY2 }, line: { type: "none" }, rectRadius: 0.1, shadow: sh() });
    circle(s, x + 0.32, y + 0.34, 0.56, TEAL, String(i + 1), WHITE, 15);
    s.addText(it[0], { x: x + 1.05, y: y + 0.3, w: w - 1.2, h: 0.5, fontFace: TF, fontSize: 16, bold: true, color: WHITE, valign: "middle", margin: 0 });
    s.addText(it[1], { x: x + 0.35, y: y + 0.98, w: w - 0.65, h: 0.68, fontFace: BF, fontSize: 12.5, color: ICE, valign: "top", lineSpacingMultiple: 1.1, margin: 0 });
  });
  footer(s, 6);
  s.addNotes("The trust posture in one slide: your account, your data, your IdP, reversible. This addresses the security and GxP review that inevitably follows.");
})();

/* 7. PROVEN TODAY */
(() => {
  const s = p.addSlide(); bg(s, CLOUD);
  eyebrow(s, "Where it stands today");
  title(s, "Proven end-to-end on AWS — not a slideware demo");
  const stats = [["32/32", "governance checks pass, live", MINT], ["15-day", "expedited clock computed deterministically", TEAL], ["100%", "of submissions gated by a qualified person", AMBER]];
  const sw = 3.9, gap = 0.2, x0 = 0.62, y = 1.55;
  stats.forEach((st, i) => {
    const x = x0 + i * (sw + gap);
    card(s, x, y, sw, 1.35);
    s.addText(st[0], { x, y: y + 0.12, w: sw, h: 0.7, align: "center", fontFace: TF, fontSize: 32, bold: true, color: st[2], margin: 0 });
    s.addText(st[1], { x: x + 0.1, y: y + 0.85, w: sw - 0.2, h: 0.42, align: "center", fontFace: BF, fontSize: 11.5, color: "44535F", lineSpacingMultiple: 1.0, margin: 0 });
  });
  card(s, 0.62, 3.2, 12.1, 3.0, CARD, 0.1);
  s.addText("Demonstrated live, in enforcement mode:", { x: 0.95, y: 3.42, w: 11.4, h: 0.4, fontFace: BF, fontSize: 13.5, bold: true, color: NAVY, margin: 0 });
  const proofs = [
    "A real, de-identified CIOMS narrative drafted by the model",
    "PHI (name, DOB, address) removed before the model — fail-closed",
    "Un-masked assessment and drafting refused by policy",
    "The agent blocked from submitting an ICSR — every time",
    "A different qualified person required to approve; approvals single-use",
    "Tamper-proof audit written for each step; duplicates rejected",
  ];
  const cw = 5.5, cx = [0.98, 6.9];
  proofs.forEach((t, i) => {
    const col = i < 3 ? 0 : 1, row = i % 3;
    const x = cx[col], yy = 3.95 + row * 0.62;
    circle(s, x, yy, 0.32, MINT, "✓", NAVY, 12);
    s.addText(t, { x: x + 0.44, y: yy - 0.08, w: cw, h: 0.5, valign: "middle", fontFace: BF, fontSize: 12.5, color: INK, lineSpacingMultiple: 1.02, margin: 0 });
  });
  footer(s, 7);
  s.addNotes("Concrete, honest proof points. 'Not a slideware demo' — it's reproducible and enforced, plus a reusable red-team harness passes 7/7 and the openFDA FAERS background is a live lookup. The seriousness engine is deterministic and transparent, so a qualified person can defend each determination.");
})();

/* 8. DIVISION OF RESPONSIBILITY */
(() => {
  const s = p.addSlide(); bg(s, CLOUD);
  eyebrow(s, "Clear division of responsibility");
  title(s, "What we bring, what stays yours");
  card(s, 0.62, 1.65, 5.95, 4.35, CARD, 0.1);
  s.addText("THE ACCELERATOR PROVIDES", { x: 0.95, y: 1.9, w: 5.3, h: 0.4, fontFace: BF, fontSize: 12.5, bold: true, color: MINT, charSpacing: 1, margin: 0 });
  ["The governed agent + policies", "The intake & assessment tools", "Fail-closed PHI masking", "The deterministic seriousness rules engine", "The human sign-off workflow + audit", "Infrastructure-as-code + documentation"].forEach((t, i) => {
    const y = 2.5 + i * 0.55;
    circle(s, 0.98, y, 0.32, MINT, "✓", NAVY, 12);
    s.addText(t, { x: 1.42, y: y - 0.05, w: 4.95, h: 0.44, valign: "middle", fontFace: BF, fontSize: 13.5, color: INK, margin: 0 });
  });
  card(s, 6.78, 1.65, 5.95, 4.35, CARD, 0.1);
  s.addText("YOU CONTROL", { x: 7.11, y: 1.9, w: 5.4, h: 0.4, fontFace: BF, fontSize: 12.5, bold: true, color: AMBER, charSpacing: 1, margin: 0 });
  ["Your identity provider + reviewer roles", "Connection to your safety system of record", "Licensed MedDRA / WHODrug coding dictionaries", "The authoritative reporting rules (regulatory review)", "Validation (CSV/CSA) + authorization to operate"].forEach((t, i) => {
    const y = 2.5 + i * 0.64;
    circle(s, 7.14, y, 0.32, AMBER, String(i + 1), NAVY, 12);
    s.addText(t, { x: 7.58, y: y - 0.06, w: 5.0, h: 0.58, valign: "middle", fontFace: BF, fontSize: 13.5, color: INK, lineSpacingMultiple: 1.0, margin: 0 });
  });
  s.addShape(p.ShapeType.roundRect, { x: 0.62, y: 6.2, w: 12.1, h: 0.55, fill: { color: NAVY }, line: { type: "none" }, rectRadius: 0.09 });
  s.addText("An accelerator, not a finished product — the reporting rules, validation, and certification for your intended use stay with you.", { x: 0.62, y: 6.2, w: 12.1, h: 0.55, align: "center", valign: "middle", fontFace: BF, fontSize: 13, bold: true, italic: true, color: WHITE, margin: 0 });
  footer(s, 8);
  s.addNotes("Honesty builds trust. Be explicit that the authoritative reporting rules (and their regulatory review), licensed MedDRA/WHODrug coding, connectors to their safety system of record, validation, and CSV/CSA authorization stay with them. The shipped thresholds are illustrative.");
})();

/* 9. WHAT IT CHANGES */
(() => {
  const s = p.addSlide(); bg(s, CLOUD);
  eyebrow(s, "What it changes for your team");
  title(s, "Safety reviewers spend their time on judgment, not assembly");
  const items = [
    ["Faster first drafts", "The agent produces a de-identified case and a drafted CIOMS narrative for review in minutes — reviewers refine instead of starting from a blank page.", TEAL],
    ["More consistent intake", "Every case runs the same governed path — the same masking, the same seriousness rules, the same audit — reducing variability.", MINT],
    ["Audit-ready by construction", "Evidence is captured automatically as the work happens, ready for a GxP audit or a regulatory inspection — not reconstructed later.", AMBER],
    ["Reviewers stay in command", "The determination and the submission remain human — the agent never makes the call.", NAVY],
  ];
  const w = 5.95, gap = 0.24, x0 = 0.62, y0 = 1.65, h = 2.05;
  items.forEach((it, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = x0 + col * (w + gap), y = y0 + row * (h + 0.24);
    card(s, x, y, w, h, CARD, 0.1);
    circle(s, x + 0.34, y + 0.36, 0.6, it[2], String(i + 1));
    s.addText(it[0], { x: x + 1.12, y: y + 0.34, w: w - 1.3, h: 0.6, fontFace: TF, fontSize: 16, bold: true, color: NAVY, valign: "middle", margin: 0 });
    s.addText(it[1], { x: x + 0.34, y: y + 1.12, w: w - 0.66, h: 0.85, fontFace: BF, fontSize: 12.6, color: "34434F", valign: "top", lineSpacingMultiple: 1.12, margin: 0 });
  });
  footer(s, 9);
  s.addNotes("Value framed honestly as assist, not replacement. Emphasize consistency (which reduces variability), audit-readiness for GxP inspections, and keeping safety reviewers in command.");
})();

/* 10. HOW WE'D START */
(() => {
  const s = p.addSlide(); bg(s, NAVY);
  s.addShape(p.ShapeType.ellipse, { x: 10.2, y: 4.4, w: 5.6, h: 5.6, fill: { color: NAVY2 }, line: { type: "none" } });
  eyebrow(s, "How we'd start", MINT);
  s.addText("A scoped pilot on synthetic data — in your account", { x: 0.6, y: 0.45, w: 12, h: 0.85, fontFace: TF, fontSize: 26, bold: true, color: WHITE, margin: 0 });
  s.addText("Low-risk, boundary-explicit, and reversible. We stand the pattern up on synthetic adverse-event cases in your AWS environment, prove the controls with your quality, compliance, and privacy teams, then scope what a validated (CSV/CSA) deployment would take.",
    { x: 0.62, y: 1.45, w: 11.6, h: 0.9, fontFace: BF, fontSize: 14.5, color: ICE, lineSpacingMultiple: 1.16, margin: 0 });
  const steps = [["1", "Workshop", "Walk your safety, quality, privacy, and security teams through the architecture and the live 32-check proof.", "~1 week"], ["2", "Scoped pilot", "Deploy to your account on synthetic adverse-event cases; run cases end-to-end through the governed workflow.", "4–6 weeks"], ["3", "Validation scope", "Define the authoritative reporting rules, licensed coding, connectors, and CSV/CSA a production deployment for your program would require.", "Joint plan"]];
  const w = 3.86, gap = 0.24, x0 = 0.62, y = 2.65, h = 2.55;
  steps.forEach((st, i) => {
    const x = x0 + i * (w + gap);
    s.addShape(p.ShapeType.roundRect, { x, y, w, h, fill: { color: NAVY2 }, line: { color: TEAL, width: 1 }, rectRadius: 0.1, shadow: sh() });
    circle(s, x + 0.32, y + 0.3, 0.6, MINT, st[0], NAVY, 17);
    s.addText(st[1], { x: x + 1.1, y: y + 0.32, w: w - 1.25, h: 0.55, fontFace: TF, fontSize: 17, bold: true, color: WHITE, valign: "middle", margin: 0 });
    s.addText(st[2], { x: x + 0.35, y: y + 1.05, w: w - 0.7, h: 1.05, fontFace: BF, fontSize: 12.3, color: ICE, valign: "top", lineSpacingMultiple: 1.12, margin: 0 });
    s.addShape(p.ShapeType.roundRect, { x: x + 0.35, y: y + h - 0.6, w: 1.7, h: 0.38, fill: { color: TEAL }, line: { type: "none" }, rectRadius: 0.19 });
    s.addText(st[3], { x: x + 0.35, y: y + h - 0.6, w: 1.7, h: 0.38, align: "center", valign: "middle", color: WHITE, fontFace: BF, fontSize: 11, bold: true, margin: 0 });
  });
  s.addText("Governed AI you can actually put in front of an auditor — and a regulatory inspector. Let's prove it on your data.", { x: 0.62, y: 5.75, w: 12, h: 0.5, fontFace: TF, fontSize: 16, italic: true, bold: true, color: MINT, margin: 0 });
  s.addNotes("Close on a concrete, low-commitment path. The pilot is synthetic-data, in-their-account, reversible — an easy yes for a security-, privacy-, and quality-conscious safety organization.");
})();

p.writeFile({ fileName: "PV-AgentCore-Customer.pptx" }).then((f) => console.log("wrote", f));
