# Ledger Sense

Document intelligence with confidence routing, for construction progress billing.

Ledger Sense extracts, validates and routes construction pay-application data
(quantities, retainage, stored materials, change orders) with a deterministic
pipeline first and a tightly fenced, opt-in model escalation second. Every
extracted field carries a `Claim` with a method and a confidence score, and
nothing routes past review until that confidence is calibrated against ground
truth.

## Quickstart

```bash
cp .env.example .env
docker compose --profile demo up
```

The `demo` profile runs entirely on the synthetic corpus (`SYNTHETIC` data
class) and mounts no client volume — safe to run anywhere. The `client`
profile is for a design partner's real documents (`CLIENT_CONFIDENTIAL`); its
Postgres is always the container in this compose file, never a hosted
service — **your documents never leave your infrastructure**.

## Data handling

- `CLIENT_CONFIDENTIAL` documents never reach a free-tier or third-party
  model. The optional model-escalation lane accepts `SYNTHETIC` or
  `REDACTED` input only.
- Writeback is draft-only. A human approves every bill inside the accounting
  system; Ledger Sense never posts an approved transaction.
- No AIA form artifact — G702/G703 are copyrighted and actively enforced.
  The synthetic corpus and UI use our own layout with equivalent column
  semantics.

## Notice

Not developed, endorsed, approved, sponsored by, or affiliated with the American Institute of Architects. AIA®, G702® and G703® are registered trademarks of the American Institute of Architects. References describe format compatibility only.

## Licence

Apache License 2.0 — see [`LICENSE`](LICENSE).

## Security

See [`SECURITY.md`](SECURITY.md) for how to report a vulnerability.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).
