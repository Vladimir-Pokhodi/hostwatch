# hostwatch

[Русская версия](README.ru.md)

A Telegram bot on your own VPS that reads servers, balances and load from your hosting
accounts and tells you before the money runs out or a server stops. It can also create
top-up invoices (SBP or crypto), switch servers on and off, renew them from the hoster
balance and order new ones. It never moves money from your cards or wallets: a top-up is
always a link or a QR code that you pay yourself.

One installation serves one person. The bot answers only the Telegram account set in
`OWNER_ID` and ignores everyone else.

## Hosters

| | Aeza | Timeweb Cloud | Regxa | AdminVPS |
|---|---|---|---|---|
| How the bot connects | API key | API token; cabinet login for top-up invoices | cabinet login | cabinet login |
| Servers, balance, «money lasts until» | yes | yes | yes | yes |
| Load | CPU, RAM, network | CPU, disk | CPU, RAM, disk, traffic | traffic |
| Top-up invoice | SBP, crypto | SBP | crypto (Cryptomus) | SBP |
| Power: start, stop, reboot | yes | yes, cloud servers | yes | yes |
| Hoster autorenewal on/off | yes | not needed, hourly billing | no such feature | yes |
| Renew now from the balance | yes | not needed | no | yes |
| Pay the hoster's renewal invoice from the balance (autopay rule) | no | no | yes | no |
| Order a server | yes | yes | yes, not tested live | planned for v2 |
| Show the root password | yes | yes | yes | no |

«Cabinet login» means you log in on the hoster's site through a one-time link the bot
sends. The browser runs on your server, so the session belongs to the server's IP.

## Chat and Mini App

**The chat** is for quick actions. It keeps one live menu: every button edits that message,
and «☰ Menu» under the input field (or `/start`) brings a fresh one to the bottom and
removes the old one. Each screen starts with one line of state («🟢 All good · 4 hosters ·
13 servers»), then a few short lines; details are behind buttons. Alerts still come as
separate messages, and their buttons open the menu on the right screen. Everything the bot
can do, it does from the chat, with or without the Mini App.

**The Mini App** («📊 Analytics» left of the input field, «📊 Charts» on a server card) is
for looking, not acting:

- Overview: balances, the month's spending against last month, problems, each hoster's
  forecast.
- Servers: all servers with filters and sorting; a server's page has CPU, RAM, disk and
  network charts for 24 hours, 7 and 30 days and a year, its plan and price history and
  what it has cost.
- Money: spending by month split by hoster and server, every top-up and charge.
- Journal: every action of yours, of the autopay rule and of the bot, with filters.
- Tips: rules over the load, prices and the hosters' order catalogs. A server whose 30-day
  peaks stay under 10% CPU and 30% RAM may do with a smaller plan; one above 85% CPU or 90%
  RAM or disk for a week is cramped; a similar plan at another connected hoster may be 15%
  cheaper or more; autorenewal is off and the term ends within 14 days; the balance will
  not cover a coming charge; a server has been stopped for a week but is still billed;
  this month's spending runs 25% above last month. Each card says why, with the numbers,
  and how much it saves or costs a month when the catalog allows counting it. The load
  tips wait for 7 days of data.

The Mini App needs a domain (see [Domain and Mini App](#domain-and-mini-app)). It shows
only to your Telegram account: every request carries Telegram's signed `initData`, and the
bot checks it against its own token and `OWNER_ID`.

**History.** The database keeps the load (every poll for 30 days, then hourly average and
peak), server changes (appeared, removed, plan, price, IP, status, autorenewal), balances,
top-ups and charges, and the journal, all forever. Aeza and Timeweb give the past weeks of
load once, so the charts do not start empty. For 15 servers that is a few MB a year.

## What has been tested on live accounts

hostwatch 1.0 was built and tested on one person's accounts with a zero budget. Nothing
that spends money or changes a server has been run against a real hoster yet.

| Feature | Live test |
|---|---|
| Reading servers, balance and load | yes, all four hosters, compared with the cabinet API |
| Top-up invoices | invoices with SBP links or crypto addresses created at all four hosters and left unpaid. No top-up was paid, so the «Paid» check has not seen real money arrive |
| Cabinet login through the link | yes for Timeweb, Regxa and AdminVPS. A captcha on a phone has not been tried |
| Power, autorenewal, renewal, paying an invoice | no. Requests are taken from the cabinets' own scripts and the hosters' docs and tested on recorded answers |
| Autopay rule | no payment made; a dry run on Regxa found nothing to pay |
| Ordering a server | catalogs and prices read live at Aeza, Timeweb and Regxa; no order placed. AdminVPS ordering waits for a test order (v2) |
| How long cabinet sessions live | Regxa: the bot extends the session on every poll. AdminVPS: not known yet |
| Coin and network lists (1.1) | read from live unpaid invoices: Aeza 13 crypto methods, Regxa's Cryptomus page 97 coin-network pairs |
| One menu, chat screens, Mini App, history, tips (1.1) | tests and a browser preview at phone size. Checks in the Telegram apps are listed in the manual checks |

The step-by-step checks for the untested parts are in
[docs/manual-checks.md](docs/manual-checks.md) (in Russian). If you run one of them, please
report the result in an issue.

## What you need

- A separate VPS for the bot, not a VPN node (see [below](#why-a-separate-vps)):
  1 CPU, 2 GB RAM, 6 GB of free disk. The browser image alone takes about 3.6 GB.
- Linux on x86_64 or arm64 with a public IPv4 address. The bot runs on Ubuntu 26.04, the
  installer was also tried on Ubuntu 24.04. Other distributions that the official Docker
  script supports should work.
- Root access.
- Ports 80 and 443 free and open from the internet. They serve the login page and get it a
  Let's Encrypt certificate.
- For the Mini App: a domain or subdomain whose A record points to the server. Without one
  the bot works fully in the chat.
- A Telegram bot token and your Telegram user ID.

### Create the bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram and send `/newbot`.
2. Give the bot a name, then a username that ends in `bot`.
3. BotFather answers with a token like `123456789:AAE...`. Keep it for the installer.

Your user ID is a number. The installer can find it: leave the question empty and send any
message to your new bot when it asks. Or get it from [@userinfobot](https://t.me/userinfobot).

## Install

On the server, as root:

```sh
curl -fsSL https://raw.githubusercontent.com/Vladimir-Pokhodi/hostwatch/v1.1.0/install.sh -o install.sh && bash install.sh
```

As a user with sudo, run `sudo bash install.sh` instead of `bash install.sh`.

The installer:

1. asks for the bot token and checks it with Telegram, then for your user ID, an optional
   domain and the server IP (it suggests the one it detects);
2. installs Docker from get.docker.com if Docker is missing;
3. creates `/opt/hostwatch` with `docker-compose.yml`, `Caddyfile` and `.env`;
4. generates `HOSTWATCH_KEY` and shows it once. Save it in your password manager: without
   it a backup cannot be read;
5. starts the containers and waits until the bot connects to Telegram.

It changes nothing else: no SSH settings, firewall rules or users. If your hoster has a
firewall in its panel, allow ports 80 and 443 there.

The first start downloads about 1.5 GB of images. Allow 5 to 10 minutes for the whole
install.

Then open your bot in Telegram and send `/start`. It asks for the language, the time zone
and the currency of totals, then offers to connect the first hoster. «☰ Menu» under the input
field (or `/menu`) brings the main menu from anywhere.

## Connect hosters

In the bot: **🔌 Hosters → ＋ Connect a hoster**, pick the hoster and give the account a label (or
press the button for none). One hoster can have several accounts.

**Aeza.** In [my.aeza.net](https://my.aeza.net) open Settings → API keys and create a key.
Add the server's IP to the key's allowed list, so a stolen key is useless anywhere else.
Send the key to the bot: it deletes your message at once and stores the key encrypted.

**Timeweb Cloud.** In the panel open «API и Терраформ» and create a token. Send it to the
bot. That is enough for servers, balance, load, power and ordering. Top-up invoices need a
cabinet session: on the account card in 🔌 Hosters press **🔑 Log in again** and log in
through the link.

**Regxa, AdminVPS.** The bot sends a one-time link to `https://<server-ip>/login/...`. Open
it on your phone or computer within 10 minutes, log in on the hoster's site as usual
(captcha and 2FA included) and press **Done**. The page shows only the hoster's site; other
domains are blocked. When the link is opened, the bot tells you from which IP and browser,
with a **Not me** button that ends the login.

If the login page does not work for you, `/cookie` takes the cookies of a session pasted by
hand. A session made on another IP may not work on the server.

### Domain and Mini App

Without a domain the login page lives at the server's IP, with a Let's Encrypt certificate
for that IP. Such certificates last 6 days, and Caddy renews them by itself. There is no
Mini App then: Telegram opens Mini Apps only from an https address with a name.

To add a domain, point its A record to the server, set `HOSTWATCH_DOMAIN=` (for example
`HOSTWATCH_DOMAIN=hostwatch.example.com`) in `/opt/hostwatch/.env` and run
`docker compose up -d` in `/opt/hostwatch`. Caddy gets a usual certificate for the domain,
login links move to it, and the bot puts «📊 Analytics» left of the input field in your chat.
The Mini App lives at `https://<domain>/app/`. From the outside only `/login/*`, `/app/*`
and `/api/*` answer; everything else is 404.

### Telegram blocked on the server

Set a proxy in `/opt/hostwatch/.env`, for example `TG_PROXY=socks5://user:pass@host:1080`,
then run `docker compose up -d` in `/opt/hostwatch`. Only the Telegram traffic goes through
it.

## Update

```sh
cd /opt/hostwatch
docker compose pull
docker compose up -d
```

The bot checks GitHub once a day and writes when a new release is out. By default the
image follows the newest 1.x release. To stay on one version, set `HOSTWATCH_VERSION=1.1.0`
in `.env`. If a release changes `docker-compose.yml` or `Caddyfile`, its notes say so.

## Backup and restore

Every night at 03:30 (your time zone) the bot copies its database to
`/opt/hostwatch/data/backups/` and keeps the last 7 copies. **⚙️ Settings → 📦 Backup** sends
a fresh copy to the chat. The history lives in the same database, so a backup keeps it too. Hoster keys and sessions in it are encrypted with
`HOSTWATCH_KEY`; the key itself is not in the backup.

To copy the backups to your computer:

```sh
scp 'root@<server-ip>:/opt/hostwatch/data/backups/*' .
```

To restore on a new server, install hostwatch there first, copy the backup file to it, then:

```sh
cd /opt/hostwatch
docker compose down
cp /root/hostwatch-20261108-033000.sqlite3 data/hostwatch.sqlite3
chown 10001:10001 data/hostwatch.sqlite3
nano .env    # put the saved key into HOSTWATCH_KEY=
docker compose up -d
```

Cabinet sessions are tied to the old server's IP, so Regxa, AdminVPS and the Timeweb
session may ask you to log in again. The Aeza key needs the new IP in its allowed list.

## Is the bot alive?

The summary comes every morning at 09:00. No summary means the bot is down.

For an alert from outside, create a check at [healthchecks.io](https://healthchecks.io)
(or any service that waits for pings) with a 5-minute period, put its ping URL into `.env`
as `HEARTBEAT_URL=` and run `docker compose up -d`. The bot pings the URL every 5 minutes,
and the service writes to you when the pings stop.

The bot also finds its own server among your servers by its IP and warns about that
server's balance 3 days earlier than about the others.

## If the server is hacked

Root on the running server means access to everything the bot holds: the key is in the
process memory. A cabinet session usually lets the holder reset a server's root password,
open its console or spend the balance on an order. Revoke everything, starting with the
hosters where the balance has money:

| What | Where to revoke |
|---|---|
| Telegram bot | @BotFather → `/mybots` → your bot → API Token → Revoke current token |
| Aeza API key | my.aeza.net → Settings → API keys: delete the key |
| Timeweb API token | Timeweb panel → «API и Терраформ»: delete the token |
| Timeweb cabinet session | change the password and end the other sessions in the account settings |
| Regxa session | change the password. If the cabinet cannot end other sessions, ask support: the session cookie works for up to a day after its last use |
| AdminVPS session | change the password in the cabinet |
| Servers | look through the cabinets' history for logins, orders and password resets; if there are any, change the root passwords and SSH keys of those servers |
| `HEARTBEAT_URL` | create a new check if it matters to you |

Then install hostwatch on a clean server with a new key and connect the hosters with new
keys. Old backups hold only revoked secrets after this.

To limit the damage in advance: bind the Aeza key to the server IP, keep the monthly limits
of autopay and ordering low (both are 0 by default), and keep no more money on hoster
balances than a month or two of bills. Every login through the link and every action with
money sends you a message, and this cannot be turned off.

## Why a separate VPS

- The login page needs ports 80 and 443. On a VPN node they are usually taken by Xray or a
  web server.
- The watcher should not go down together with what it watches. If the VPN node is
  blocked, overloaded or unpaid, the bot still has to tell you.
- A VPN node is a visible target, and the bot holds keys to all your hosters. Keep it on a
  server that runs nothing else.

The cheapest VPS of any hoster will do. Better if its balance is not the one the bot
watches most.

## Commands on the server

Run them in `/opt/hostwatch`. None of them changes anything at the hoster.

```sh
docker compose exec hostwatch hostwatch check <account>    # servers, balance, forecast, timings
docker compose exec hostwatch hostwatch login <account>    # a new login link, also sent to the chat
docker compose exec hostwatch hostwatch catalog <account>  # what the hoster sells, cheapest price
docker compose exec hostwatch hostwatch autopay <account>  # what the autopay rule would pay now
docker compose logs -f hostwatch                           # the bot's log
```

`<account>` is the label or number of an account from 🔌 Hosters.

## Development

```sh
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy --strict hostwatch/core
uv run playwright install chromium   # once, for the end-to-end login test
```

To run the bot on your computer, copy `.env.example` to `.env`, fill in `BOT_TOKEN`,
`OWNER_ID`, `HOSTWATCH_KEY` and `DATA_DIR` (for example `DATA_DIR=./data`), then:

```sh
uv run python -m hostwatch
```

To run a build of your checkout on a server instead of the published image:

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Adding a hoster: [docs/adding-a-hoster.md](docs/adding-a-hoster.md). Pull requests:
[CONTRIBUTING.md](CONTRIBUTING.md). The design is in [docs/spec.md](docs/spec.md), in
Russian.

## License

MIT
