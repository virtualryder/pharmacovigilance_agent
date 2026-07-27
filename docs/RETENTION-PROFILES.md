# Retention Profiles — Pharmacovigilance ICSR Assistant

*WORM (S3 Object Lock) retention for the evidence vault, selected by CDK switch. One page.*

---

## Profiles (`-c retention_profile=...`)

| Profile | Object Lock mode | Retention | Use |
|---|---|---|---|
| `sandbox-demo` | GOVERNANCE | 1 day | **Sandbox only** — disposable validation; a privileged role can bypass |
| `pilot` | GOVERNANCE | 90 days | Pilot working-evidence window |
| `production-reference` | **COMPLIANCE** | 2555 days (7-yr reference) | Production — **no one, including root, can shorten or delete** before expiry |

GOVERNANCE mode allows a specifically-privileged role to override retention (fine for sandbox/pilot);
COMPLIANCE mode is immutable even to the account root — required where the evidence must be provably
un-editable for its full schedule.

## What each store retains

- **WORM evidence vault (S3 Object Lock)** — the tamper-evident copy of every governed decision; mode +
  days per the profile above; `RETAIN` on stack delete.
- **Audit ledger (DynamoDB)** — append-only, hash-chained, PITR on, `RETAIN` on stack delete.
- **Sanitized-artifacts + case store (DynamoDB)** — transient working data, **TTL-expired** (sanitized
  ~1 day, case store 7 days); these hold content and are deliberately short-lived.

## Choosing the number

Pharmacovigilance record retention is set by the **sponsor's regulatory obligations** (e.g. ICH/GVP
record-keeping, product-lifetime + a defined period; regional rules vary). The `production-reference`
7-year figure is a **reference default** — configure the exact schedule to the sponsor's SOP and the
applicable regulation before production, and record the approval in the change log.
