# Contributing

Bug reports, fixes and new hosters are welcome.

## Before you open an issue

Never attach the database (`hostwatch.sqlite3` or a backup), `.env`, cookies, API keys,
login links or full logs. The database holds your hoster sessions, encrypted, and `.env`
holds the key to them. Replace names, IPs, emails and sums in anything you paste.

If you think you found a security problem, do not open a public issue: use GitHub's
private vulnerability report on the Security tab.

## Pull requests

1. Fork the repository and create a branch.
2. Set up: `uv sync`, then `uv run playwright install chromium` for the login test.
3. Make the change. Keep it focused: one fix or one hoster per pull request.
4. Run the same checks as CI:

   ```sh
   uv run pytest
   uv run ruff check
   uv run ruff format --check
   uv run mypy --strict hostwatch/core
   uv run mypy hostwatch
   ```

5. Install the hooks once with `pre-commit install`: they run ruff and gitleaks before each
   commit.

A new hoster follows [docs/adding-a-hoster.md](docs/adding-a-hoster.md), including its
checklist.

## Code and texts

- Types everywhere; `mypy --strict` on `hostwatch/core`.
- Money is `Decimal` inside `Money`, never float.
- Answers of hosters are parsed with strict models. An unknown shape is an error, not a
  guess.
- Secrets, tokens, cookies and passwords never go to logs, the journal, error texts or
  fixtures.
- Every text the bot shows lives in `hostwatch/locales/en.py` and `ru.py`, with the same
  keys in both.
- Comments explain why, not what.
- Docs and bot texts are plain and concrete: short sentences, commands that can be copied.
