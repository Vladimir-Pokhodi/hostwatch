hostwatch 1.1: the chat keeps one live menu with short screens, and a Telegram Mini App
shows what the bot has seen over time: load charts, money by month, the journal and tips on
tariffs. What the bot does at the hosters has not changed.

## Update

```sh
cd /opt/hostwatch
docker compose pull
docker compose up -d
```

The database migrates by itself on start; the history begins from the update. Aeza and
Timeweb give the past weeks of load once per server, so their charts do not start empty.

`docker-compose.yml` and `Caddyfile` changed in this release (the Mini App paths and the
domain certificate). If you installed 1.0.0, take the new ones before `up -d`:

```sh
cd /opt/hostwatch
curl -fsSLO https://raw.githubusercontent.com/Vladimir-Pokhodi/hostwatch/v1.1.0/docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/Vladimir-Pokhodi/hostwatch/v1.1.0/Caddyfile
```

New install: see the [README](https://github.com/Vladimir-Pokhodi/hostwatch#readme).

Image: `ghcr.io/vladimir-pokhodi/hostwatch:1.1.0` (also `1.1`, `1`, `latest`), for
linux/amd64 and linux/arm64.

## What is new

- **One menu in the chat.** Buttons edit one message; «☰ Menu» under the input field brings
  a fresh menu to the bottom and removes the old one. Your typed sums are deleted after
  reading, payment and login links are buttons in the menu.
- **Screens redone.** One line of state on top, a few short lines below, details behind
  buttons; a «⚠️ Problems» screen.
- **All coins.** Crypto top-ups list every coin and network the hoster offers: 13 crypto
  methods at Aeza, the full Cryptomus list at Regxa.
- **History, kept forever.** Load (every poll for 30 days, then hourly average and peak),
  server changes, balances, top-ups and charges, a wider journal.
- **Mini App** («📊 Analytics»): overview, servers with CPU, RAM, disk and network charts up
  to a year, money by month per hoster and server, the journal with filters, and tips.
- **Tips** by rules: a smaller plan when a server idles for 30 days, a bigger one when it is
  cramped for a week, a similar plan cheaper by 15% or more at another connected hoster,
  autorenewal off near the end of the term, money short for a coming charge, a server
  stopped for a week but billed, spending running 25% above last month. Each says why, with
  the numbers, and how much a month when the hoster's order catalog allows counting it.

The Mini App needs a domain pointing to the server: set `HOSTWATCH_DOMAIN` in `.env`
(see «Domain and Mini App» in the README). Without it everything works in the chat as
before. Only your Telegram account can open it: every request is checked against the bot
token and `OWNER_ID`.

## What was checked and what was not

- Checked live: the coin and network lists, on unpaid invoices at Aeza (13 crypto methods)
  and on the Cryptomus page of a Regxa invoice (97 coin-network pairs), on 09.11. Nothing
  was paid.
- Checked in tests and in a browser at phone size (390×844, light and dark, Russian and
  English): every chat screen, the one-menu rules, the history tables and their nightly
  rollup, the Mini App API and its access check, every tab, and every tip rule with too
  little data too.
- Not confirmed in these notes: how the one menu and the Mini App behave in each Telegram
  app (Android, iOS, Desktop, Web), and the tips on a real account. Tips about load need 7
  days of history, so right after the update you will see «Little load data» instead. The
  steps are in [docs/manual-checks.md](https://github.com/Vladimir-Pokhodi/hostwatch/blob/v1.1.0/docs/manual-checks.md)
  (in Russian).
- Tips compare plans only by the hosters' order catalogs (Aeza, Timeweb, Regxa); AdminVPS
  has none, so its servers get no plan suggestions. A comparison between hosters ignores
  country and bandwidth.
- The 1.0 limits stay: power, renewal, autopay and ordering have still not been run against
  a real hoster. Autopay and ordering stay at 0 per month by default.

Full list of changes: [CHANGELOG.md](https://github.com/Vladimir-Pokhodi/hostwatch/blob/v1.1.0/CHANGELOG.md).
