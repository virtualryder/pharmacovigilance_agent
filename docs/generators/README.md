# Doc generators — Pharmacovigilance AgentCore

The Word guides and PowerPoint decks in `../` (the `docs/` folder) are **generated** from these scripts,
so they can be regenerated after a change instead of hand-edited. `guides.js` is the shared helper
library (headings, tables, callouts, cover/TOC); the rest are runnable generators.

| Script | Output (written into `docs/`) |
|---|---|
| `runbook.js` | `PV-AgentCore-SA-Runbook.docx` |
| `maintenance.js` | `PV-AgentCore-Maintenance.docx` |
| `regulatory.js` | `PV-AgentCore-Regulatory-Adherence.docx` |
| `leadership_deck.js` | `PV-AgentCore-Leadership.pptx` |
| `customer_deck.js` | `PV-AgentCore-Customer.pptx` |

## Regenerate

Requires Node 18+ (developed on Node 22). Run from the `docs/` folder so outputs land there:

```bash
cd docs
npm --prefix generators install        # one-time: installs docx + pptxgenjs into generators/node_modules
node generators/runbook.js             # -> docs/PV-AgentCore-SA-Runbook.docx
node generators/maintenance.js
node generators/regulatory.js
node generators/leadership_deck.js     # -> docs/PV-AgentCore-Leadership.pptx
node generators/customer_deck.js
```

`node_modules/` is git-ignored. The generators have no external asset dependencies.
