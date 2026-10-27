# Contributing

## Setup

```bash
uv sync --all-extras
pre-commit install
cp .env.example .env
```

## Workflow

This project follows strict TDD: write the failing test first, confirm it
fails for the right reason, then write the minimum code to pass. See
`CLAUDE.md` for the full set of hard constraints (no floats in the money
path, no client document to a free-tier model, writeback never approves or pays (unpaid bills only),
etc.) — each one has a test that proves it, and a PR that weakens one of
those tests without also removing the constraint will be rejected.

## Before opening a PR

```bash
uv run ruff check .
uv run mypy --strict .
uv run pytest
pre-commit run --all-files
```

CI additionally runs the full backend suite (`ruff`, `mypy --strict`,
`pytest` — the last against a real Postgres service container, so
`migrations/versions/*.py` is validated against Postgres on every push, not
just SQLite), the frontend suite, a licence audit, `gitleaks`, `pip-audit`,
`npm audit`, and produces an SBOM. All must pass.

## Licensing

Ledger Sense is Apache-2.0. By contributing, you agree your contribution is
licensed under the same terms. Only permissive-licensed dependencies are
accepted — see `scripts/licence_audit.py` for the denied-licence list and
the native-dependency carve-outs (e.g. `ocrmypdf` is denied despite a
permissive Python licence because it shells out to AGPL Ghostscript).

## What we will not merge

- AIA G702/G703 form artifacts or a corpus of them (copyrighted, actively
  enforced) — see `CLAUDE.md` rule 7.
- Anything that lets an approved accounting transaction post automatically,
  or that auto-approves a bank-detail change or auto-merges a vendor.
- Generated legal-document text (e.g. lien waiver language).
