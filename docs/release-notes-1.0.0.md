The first release of hostwatch: a Telegram bot on your own VPS that watches servers and
balances at Aeza, Timeweb Cloud, Regxa and AdminVPS, warns before the money runs out or a
server stops, and creates top-up invoices you pay yourself by SBP or crypto.

## Install

On a separate VPS with free ports 80 and 443, as root:

```sh
curl -fsSL https://raw.githubusercontent.com/Vladimir-Pokhodi/hostwatch/v1.0.0/install.sh -o install.sh && bash install.sh
```

The installer asks for the bot token and your Telegram ID, installs Docker if needed and
starts the bot. Details are in the [README](https://github.com/Vladimir-Pokhodi/hostwatch#readme).

Image: `ghcr.io/vladimir-pokhodi/hostwatch:1.0.0` (also `1.0`, `1`, `latest`), for
linux/amd64 and linux/arm64.

## What it does

| | Aeza | Timeweb Cloud | Regxa | AdminVPS |
|---|---|---|---|---|
| Connects by | API key | API token + cabinet login | cabinet login | cabinet login |
| Servers, balance, forecast, load | yes | yes | yes | yes (traffic only) |
| Top-up invoice | SBP, crypto | SBP | crypto | SBP |
| Power | yes | yes | yes | yes |
| Autorenewal, renewal | yes | not needed | pays renewal invoices | yes |
| Ordering | yes | yes | yes, not tested live | v2 |

## Please read before you trust it with money

This release was tested on one person's accounts with a zero budget.

- Tested live: reading servers, balances and load at all four hosters; creating unpaid
  top-up invoices at all four; cabinet login through the link at Timeweb, Regxa and
  AdminVPS; reading order catalogs and prices.
- Not tested live: power, autorenewal, renewal, paying invoices, the autopay rule, ordering.
  Their requests come from the cabinets' own scripts and the hosters' documentation and are
  covered by tests on recorded answers. No top-up has been paid, so the «Paid» check has not
  seen real money arrive.
- Autopay and ordering are limited to 0 per month by default: the bot pays nothing by
  itself and asks twice before every order until you set a limit.

If you try one of the untested actions, the expected result is in
[docs/manual-checks.md](https://github.com/Vladimir-Pokhodi/hostwatch/blob/v1.0.0/docs/manual-checks.md).
Please report what happened in an issue, without cookies, keys or the database.

Full list of changes: [CHANGELOG.md](https://github.com/Vladimir-Pokhodi/hostwatch/blob/v1.0.0/CHANGELOG.md).
