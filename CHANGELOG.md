# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow
[SemVer](https://semver.org/); before 1.0 a minor bump can break things.

## [Unreleased]

## [0.1.0] - 2026-11-09

First tagged release. It is an engineering prototype running on a synthetic corpus, not a product
ready for client accounting writeback: the [15 October 2026 self-audit](docs/system-audit-2026-10-15.md)
says so, and the open findings are listed under "Known gaps" below and in the README.

### Added

- Deterministic extraction pipeline: classify, layout parse (PDF text layer first), field
  extraction by anchor, geometry, regex and table reconstruction.
- Validation rules, exact with no tolerance: G703 footing to G702 (A1), per-line arithmetic,
  balance to finish and percentage complete (A2-A4b), and the payment chain through retainage to
  current payment due (A5-A8). Integrity checks: duplicates, vendor resolution with human
  confirmation, and a hard stop on bank-detail changes. Money is `Decimal` only, enforced by a
  lint rule and a test. Retainage rate and stepping, stored-materials and change-order rules are
  specified but not built.
- Confidence routing: every field carries a `Claim` with a method and confidence. Until a field
  type has a reliability curve, everything routes to review.
- The `CLIENT_CONFIDENTIAL` gateway: client documents can't reach a free-tier model; the optional
  model lane accepts `SYNTHETIC` or `REDACTED` input only.
- Writeback that never approves or pays: an unpaid QuickBooks Online Bill stamped
  `AWAITING REVIEW`, with idempotency keys and closed-period refusal, plus a CSV/IIF export with
  no ledger effect.
- Next.js frontend: baseline study, inbox, review queue with `J`/`K` keyboard navigation,
  reconciliation, vendor ledger.
- A synthetic pay-application corpus in our own layout (no AIA form artifact) with injected
  error classes. It is the demo.
- CI: ruff, `mypy --strict`, pytest with coverage floors, a no-float check, a licence gate that
  rejects AGPL, gitleaks over full history, `pip-audit`, `npm audit`, a CycloneDX SBOM, frontend
  tests and build, and Playwright end-to-end tests.
- `.github/workflows/release.yml`: a version tag publishes a GitHub Release from this file, only
  if the tagged commit has a green `ci` run.

### Fixed

- CI's `secrets` job was red on `main` after the root commit was re-dated on 4 Nov 2026: the
  three reviewed false positives in `.gitleaksignore` are pinned to commit SHAs, and the rewrite
  changed them. The new SHAs are added; a full-history scan finds nothing else.

### Known gaps

Open self-audit findings as of 4 Nov 2026:

- LS-11: extraction still runs outside the parser's resource boundary.
- LS-12: the parser queue can reject valid text-rich PDFs.
- LS-14: reconciliation reads demo-only state.
- LS-15: baseline studies aren't durable or resumable yet.
- LS-34: QuickBooks reversal has no durable recovery.
- LS-16 to LS-33: all 18 Medium findings.

There are no published results on real documents, and no calibrated confidence curves.

[Unreleased]: https://github.com/Berakhah/ledger-sense/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Berakhah/ledger-sense/releases/tag/v0.1.0
