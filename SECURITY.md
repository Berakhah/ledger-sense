# Security policy

Ledger Sense processes `CLIENT_CONFIDENTIAL` construction financial
documents (invoices, bank details, contract sums). Treat any report touching
document handling, the model-escalation boundary, or writeback as high
priority.

## Reporting a vulnerability

Email the maintainer at 77killuazoldic@gmail.com with a description, steps
to reproduce, and impact. Do not open a public issue for a suspected
vulnerability. We aim to acknowledge within 5 business days.

`docs/security.md` is the longer engineering account behind this policy: the
threat model, what data we hold, how documents are encrypted at rest, and the
retention and deletion procedure.

## Scope highlights

- The `CLIENT_CONFIDENTIAL` → free-tier-model boundary (see `CLAUDE.md`,
  rule 1). A report that this boundary can be bypassed is critical.
- Secret handling: `.env.example` must never carry a real credential, and
  `gitleaks` runs in pre-commit and CI against the working tree and against
  `tests/writeback/cassettes` (VCR does not scrub response bodies).
- Writeback: idempotency, closed-period refusal, and the never-auto-approve
  rules on bank-detail changes and vendor merges.

## Supply chain

CI runs `pip-audit --strict`, `npm audit --audit-level=high`, and publishes
a CycloneDX SBOM on every build (see `.github/workflows/ci.yml`, job
`supply-chain`).
