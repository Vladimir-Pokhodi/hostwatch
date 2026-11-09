# Changelog

## 1.1.0 — unreleased

The chat becomes one live menu with short screens; a Telegram Mini App shows history,
charts, money, the journal and tips. Nothing changed in what the bot does at the hosters.

### Top-up

- Crypto top-ups offer every coin and network the hoster lists, coin first, then the
  network when the coin has several. Aeza: all crypto methods of `/billing/payment-methods`
  (13 on 09.11). Regxa: the coins and networks of the Cryptomus invoice page (97 pairs on
  09.11), read over plain HTTP, shown a page of coins at a time; if the list cannot be read,
  the owner picks on the payment page.

### Chat

- One live menu: every button edits the same message. «☰ Menu» under the input field and
  `/start` send a fresh menu to the bottom and delete the old one; a menu older than 48
  hours loses its buttons instead. Pressing an old menu says «This menu is closed».
- Typed answers (a sum, a name) are deleted after reading, and the menu shows the result.
  Payment and login links are buttons inside the menu; the screen changes by itself when
  the link expires or the payment arrives.
- Alert buttons («Top up», «Open server») close the old menu and open a new one on that
  screen.
- Every screen redone: one line of state first, then short lines; a «⚠️ Problems» screen;
  servers by hoster, 8 to a page; a server card with IP and plan, load, price and next
  charge; «💰 Money» with a line per hoster and top-up in 3 visible steps; «🔌 Hosters»
  with connected and available ones apart.

### History

- The database keeps history forever: load samples on every poll (rolled into hourly
  average and peak after 30 days, at the nightly backup), server changes (appeared,
  removed, plan, price, period, IP, status, autorenewal), money in and out (balance
  changes and Aeza's per-server charges), and a balance row only when the balance changes.
- Past load is read once per server where the hoster keeps it: Aeza (30 days, hourly) and
  Timeweb (CPU, up to 30 days, hourly).
- The journal also records connecting and disconnecting hosters, account status, settings,
  «Paid» and its result, autopay marks, holds and answers; the bot's own actions show as
  «bot».

### Mini App

- `HOSTWATCH_DOMAIN` turns it on: Caddy gets a usual certificate for the domain, login links
  move to it, and «📊 Analytics» appears left of the input field. Outside only `/login/*`,
  `/app/*` and `/api/*` answer. Without a domain everything works in the chat as before.
- Every API request carries Telegram `initData`, checked against the bot token and
  `OWNER_ID`, at most 24 hours old; anything else gets 401, and repeated failures 429.
- No build step and no npm: plain JS modules and uPlot in the repository, colours from the
  Telegram theme, light and dark, Russian and English.
- Tabs: Overview (balances, month spending against last month, problems, forecasts);
  Servers (table with filters and sorting; a server page with CPU, RAM, disk and network
  for 24 h, 7 d, 30 d and a year, plan and price history, cost by month); Money (by month,
  split by hoster and server, every top-up and charge); Journal (filters by who, what,
  hoster and dates). «📊 Charts» on a chat server card opens the server's page.
- Tips tab: rules in `hostwatch/core/tips.py`, sorted by money. Oversized (30-day hourly
  peaks p95 under 10% CPU and 30% RAM: a smaller plan of the same location that holds the
  peaks with headroom, and the saving), cramped (7-day hourly averages p95 over 85% CPU or
  90% RAM or disk: a bigger plan and the extra cost), cheaper at another connected hoster
  (same or more CPU, RAM and disk, at least 15% cheaper after conversion; by the catalog,
  regardless of country and bandwidth), not renewing within 14 days, money short for a
  coming charge, stopped for 7 days but billed, month spending pace 25% above last month.
  Load rules need 7 days with 70% of the hours covered; until then one «little data» card.
  Specs come from the hosters' order catalogs (Aeza, Timeweb, Regxa), never from plan
  names; AdminVPS has no catalog, so its servers get no plan suggestions.

## 1.0.0 — 2026-11-09

The first release.

### Hosters

- Aeza by API key: servers, balance, load, charges, SBP and crypto top-up invoices, power,
  autorenewal, renewal, ordering, root password.
- Timeweb Cloud by API token: cloud and dedicated servers, balance with Timeweb's own
  «hours left», load, power, ordering, root password. SBP top-up invoices through a cabinet
  session.
- Regxa by cabinet session: services, VPS, balance, invoices, load, power, paying renewal
  invoices from the balance, crypto top-up through Cryptomus, ordering (not tested live),
  root password.
- AdminVPS by cabinet session (WHMCS pages): services, balance, status, traffic, power,
  autorenewal, renewal, SBP top-up through PayMaster.

### Bot

- Summary, servers by hoster, server cards, balances with «money lasts until» and how it
  was counted, integrations, journal, settings. English and Russian.
- Alerts: balance running out, server status, end of a paid period, load, login needed,
  broken plugin. Repeats only when things get worse, «resolved» messages, quiet hours, a
  daily summary.
- Login on the hoster's site through a one-time link: Chromium runs in its own container,
  only the hoster's domains open, the owner is told who opened the link. `/cookie` as a
  fallback.
- Top-up invoices with a QR code and a «Paid» button that rereads the balance.
- Server control with a confirmation for every action, a journal and a message for every
  action with money.
- Autopay rule for renewal invoices: off by default, monthly limit, one payment per invoice,
  a check that the sum has not grown.
- Ordering servers with a second confirmation above the monthly limit, «Order the same»,
  SSH key, root password that the bot deletes from the chat after 5 minutes.
- Nightly database backups, «Send backup», a heartbeat URL, a daily check for new releases,
  extra warning for the bot's own server.

### Install and development

- `install.sh`, an image on GHCR for linux/amd64 and linux/arm64, Docker Compose with Caddy
  and a Let's Encrypt certificate for the server IP.
- Commands `check`, `record`, `login`, `autopay` (dry run), `catalog`, `new-plugin`.
- `docs/adding-a-hoster.md`, a plugin template and a shared contract test.

### Not tested on live accounts

Power, autorenewal, renewal, paying invoices, autopay and ordering were not run against a
real hoster; no top-up was paid. See «What has been tested on live accounts» in the README.
