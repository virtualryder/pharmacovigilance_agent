/* Generates the three PV-AgentCore Word guides: SA runbook, regulatory-adherence, maintenance. */
const docx = require("docx");
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, TableOfContents,
  Table, TableRow, TableCell, WidthType, BorderStyle, ShadingType, PageBreak, PageNumber, Header, Footer,
} = docx;

const NAVY = "0E2A47", TEAL = "1C7293", MINT = "0B8F72", AMBER = "B9791F", INK = "1A2733", MUTED = "6B7A8A", LIGHT = "EEF3F7", LINE = "C9D6DF", WHITE = "FFFFFF";
const BODY = "Calibri", HEAD = "Cambria", MONO = "Consolas";

// ---- styles ----
const styles = {
  default: { document: { run: { font: BODY, size: 21, color: INK } } },
  paragraphStyles: [
    { id: "Title", name: "Title", basedOn: "Normal", next: "Normal", run: { font: HEAD, size: 52, bold: true, color: NAVY }, paragraph: { spacing: { after: 120 } } },
    { id: "Subtitle", name: "Subtitle", basedOn: "Normal", run: { font: BODY, size: 24, color: TEAL }, paragraph: { spacing: { after: 80 } } },
    { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: HEAD, size: 30, bold: true, color: NAVY }, paragraph: { spacing: { before: 320, after: 140 }, keepNext: true, outlineLevel: 0 } },
    { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: HEAD, size: 24, bold: true, color: TEAL }, paragraph: { spacing: { before: 240, after: 90 }, keepNext: true, outlineLevel: 1 } },
    { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: BODY, size: 22, bold: true, color: NAVY }, paragraph: { spacing: { before: 160, after: 60 }, keepNext: true, outlineLevel: 2 } },
    { id: "Normal", name: "Normal", run: { font: BODY, size: 21, color: INK }, paragraph: { spacing: { after: 130, line: 264 } } },
    { id: "Code", name: "Code", basedOn: "Normal", run: { font: MONO, size: 17, color: "1B3A2B" }, paragraph: { spacing: { after: 20, line: 240 }, shading: { type: ShadingType.CLEAR, fill: "F0F4F1" } } },
    { id: "Caption", name: "Caption", basedOn: "Normal", run: { font: BODY, size: 17, italics: true, color: MUTED } },
  ],
};

// ---- helpers ----
const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const H3 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(t)] });
function P(runs, opts = {}) {
  const children = (Array.isArray(runs) ? runs : [runs]).map((r) => typeof r === "string" ? new TextRun(r) : new TextRun(r));
  return new Paragraph(Object.assign({ children }, opts));
}
const bold = (t) => ({ text: t, bold: true });
const code = (t) => ({ text: t, font: MONO, size: 18, color: "1B3A2B" });
function bullet(runs, level = 0) {
  const children = (Array.isArray(runs) ? runs : [runs]).map((r) => typeof r === "string" ? new TextRun(r) : new TextRun(r));
  return new Paragraph({ children, bullet: { level }, spacing: { after: 70, line: 260 } });
}
function num(runs, ref, level = 0) {
  const children = (Array.isArray(runs) ? runs : [runs]).map((r) => typeof r === "string" ? new TextRun(r) : new TextRun(r));
  return new Paragraph({ children, numbering: { reference: ref, level }, spacing: { after: 70, line: 260 } });
}
function codeBlock(lines) {
  return lines.map((l, i) => new Paragraph({
    style: "Code",
    children: [new TextRun({ text: l || " ", font: MONO, size: 17 })],
    shading: { type: ShadingType.CLEAR, fill: "F0F4F1" },
    border: {
      left: { style: BorderStyle.SINGLE, size: 18, color: MINT, space: 6 },
      top: i === 0 ? { style: BorderStyle.SINGLE, size: 4, color: "DDE6DE", space: 3 } : undefined,
      bottom: i === lines.length - 1 ? { style: BorderStyle.SINGLE, size: 4, color: "DDE6DE", space: 3 } : undefined,
    },
    spacing: { after: i === lines.length - 1 ? 140 : 0, before: i === 0 ? 40 : 0, line: 240 },
  }));
}
function callout(titleText, bodyRuns, color = TEAL, fill = LIGHT) {
  const kids = [new Paragraph({ children: [new TextRun({ text: titleText, bold: true, color, size: 20 })], spacing: { after: 40 } })];
  (Array.isArray(bodyRuns[0]) ? bodyRuns : [bodyRuns]).forEach((line) => {
    kids.push(new Paragraph({ children: line.map((r) => new TextRun(typeof r === "string" ? r : r)), spacing: { after: 20, line: 258 } }));
  });
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE }, columnWidths: [10440],
    borders: { top: { style: BorderStyle.SINGLE, size: 2, color }, bottom: { style: BorderStyle.SINGLE, size: 2, color }, left: { style: BorderStyle.SINGLE, size: 18, color }, right: { style: BorderStyle.SINGLE, size: 2, color }, insideHorizontal: { style: BorderStyle.NONE }, insideVertical: { style: BorderStyle.NONE } },
    rows: [new TableRow({ children: [new TableCell({ width: { size: 10440, type: WidthType.DXA }, shading: { type: ShadingType.CLEAR, fill }, margins: { top: 120, bottom: 120, left: 180, right: 180 }, children: kids })] })],
  });
}
function table(headers, rows, widths) {
  const total = widths.reduce((a, b) => a + b, 0);
  const hdr = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA }, shading: { type: ShadingType.CLEAR, fill: NAVY }, margins: { top: 70, bottom: 70, left: 110, right: 110 },
      children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, color: WHITE, size: 19 })], spacing: { after: 0 } })],
    })),
  });
  const body = rows.map((r, ri) => new TableRow({
    children: r.map((c, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA }, shading: { type: ShadingType.CLEAR, fill: ri % 2 ? "F4F8FA" : WHITE }, margins: { top: 60, bottom: 60, left: 110, right: 110 },
      children: (Array.isArray(c) ? c : [c]).map((line) => new Paragraph({ children: [typeof line === "string" ? new TextRun({ text: line, size: 19 }) : new TextRun(Object.assign({ size: 19 }, line))], spacing: { after: 0, line: 250 } })),
    })),
  }));
  return new Table({ width: { size: total, type: WidthType.DXA }, columnWidths: widths, rows: [hdr, ...body],
    borders: { top: { style: BorderStyle.SINGLE, size: 2, color: LINE }, bottom: { style: BorderStyle.SINGLE, size: 2, color: LINE }, left: { style: BorderStyle.NONE }, right: { style: BorderStyle.NONE }, insideHorizontal: { style: BorderStyle.SINGLE, size: 2, color: LINE }, insideVertical: { style: BorderStyle.SINGLE, size: 2, color: "E6EDF1" } } });
}
const spacer = (h = 80) => new Paragraph({ text: "", spacing: { after: h } });

function coverAndToc(titleLines, subtitle, meta, tocEntries) {
  const kids = [
    new Paragraph({ text: "", spacing: { before: 1400 } }),
    new Paragraph({ children: [new TextRun({ text: "GOVERNED AGENTIC AI · AMAZON BEDROCK AGENTCORE", color: TEAL, bold: true, size: 19, characterSpacing: 40 })], spacing: { after: 200 } }),
  ];
  titleLines.forEach((t) => kids.push(new Paragraph({ style: "Title", children: [new TextRun(t)] })));
  kids.push(new Paragraph({ style: "Subtitle", children: [new TextRun(subtitle)], spacing: { before: 120, after: 500 } }));
  kids.push(new Paragraph({
    border: { top: { style: BorderStyle.SINGLE, size: 6, color: MINT }, bottom: { style: BorderStyle.SINGLE, size: 6, color: MINT } },
    shading: { type: ShadingType.CLEAR, fill: LIGHT },
    children: [new TextRun({ text: meta, italics: true, color: INK, size: 19 })], spacing: { before: 60, after: 60, line: 260 },
  }));
  kids.push(new Paragraph({ children: [new PageBreak()] }));
  kids.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Contents")] }));
  (tocEntries || []).forEach((t) => kids.push(new Paragraph({
    tabStops: [{ type: "left", position: 520 }],
    spacing: { after: 90, line: 260 },
    children: [new TextRun({ text: t.replace(/^(\d+)\.\s*/, "$1.\t"), size: 21, color: INK, bold: /^\d+\./.test(t) })],
  })));
  kids.push(new Paragraph({ children: [new PageBreak()] }));
  return kids;
}

function makeDoc(coverKids, bodyKids, footerText) {
  return new Document({
    styles,
    features: { updateFields: true },
    numbering: {
      config: [
        { reference: "steps", levels: [{ level: 0, format: "decimal", text: "%1.", alignment: AlignmentType.START, style: { paragraph: { indent: { left: 460, hanging: 300 } } } }] },
        { reference: "steps2", levels: [{ level: 0, format: "decimal", text: "%1.", alignment: AlignmentType.START, style: { paragraph: { indent: { left: 460, hanging: 300 } } } }] },
      ],
    },
    sections: [{
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1080, bottom: 1080, left: 1100, right: 1100 } } },
      footers: { default: new Footer({ children: [new Paragraph({ tabStops: [{ type: "right", position: 10040 }], border: { top: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 6 } }, children: [new TextRun({ text: footerText, size: 15, color: MUTED }), new TextRun({ text: "\t", size: 15 }), new TextRun({ children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES], size: 15, color: MUTED })] })] }) },
      children: [...coverKids, ...bodyKids],
    }],
  });
}

module.exports = { docx, H1, H2, H3, P, bold, code, bullet, num, codeBlock, callout, table, spacer, coverAndToc, makeDoc, Packer, Paragraph, TextRun, PageBreak, colors: { NAVY, TEAL, MINT, AMBER, INK, MUTED } };
