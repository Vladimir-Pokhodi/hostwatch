# hostwatch

A self-hosted Telegram bot that collects servers, balances and load from all your hosting
accounts, warns you before money runs out or a server goes down, and creates top-up invoices
(SBP or crypto). It never moves money from your cards or wallets.

Work in progress. Nothing to install yet.

## Development

```sh
uv sync
uv run pytest
uv run ruff check
uv run mypy --strict hostwatch/core
```

The design lives in [docs/spec.md](docs/spec.md), the stage plan in [docs/plan.md](docs/plan.md).

## License

MIT
