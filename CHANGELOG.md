# Changelog

## Unreleased

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
