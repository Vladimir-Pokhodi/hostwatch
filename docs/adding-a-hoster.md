# Adding a hoster

A hoster is a plugin: a folder `hostwatch/plugins/<id>/` with one subclass of `Plugin`. The
core finds it by itself, so adding a hoster needs no changes outside that folder, its test
and its fixtures.

The plugins for Aeza (`aeza/`, API key), Timeweb (`timeweb/`, official API plus a cabinet
session), Regxa (`regxa/`, internal JSON of the cabinet) and AdminVPS (`adminvps/`, HTML of
WHMCS) are working examples of each kind.

## 1. Before you start

- Read the hoster's terms. If they forbid automated access to the cabinet, stop: such a
  plugin will not be accepted.
- Find out how the bot will log in: an API key the owner creates, or a cabinet session that
  the owner gets through the login page (`BrowserAuth`).
- Check that the hoster has a prepaid balance you can read. hostwatch is built around
  «money on the balance lasts until»: `balance()` is required and the contract test calls
  it. A hoster that bills a card after the fact (Hetzner Cloud, for example) needs a change
  in the core first; open an issue before writing such a plugin.
- You need your own account at the hoster with at least one server. Reading is enough to
  start; nothing in this guide spends money. Without an account you can write a draft on
  hand-made fixtures (step 6), but a pull request needs fixtures recorded from a real
  account and the output of `hostwatch check`.

## 2. Explore the cabinet

Look for data in this order and stop at the first one that has it:

1. **Official API.** Documentation, API keys in the account settings, whether a key can be
   bound to an IP. An API is the most stable choice.
2. **Requests of the cabinet itself (XHR).** Log in, open the browser developer tools,
   Network tab, filter Fetch/XHR, and walk through the pages with servers, balance, invoices
   and top-up. Most modern cabinets load JSON from their own backend. Note which cookies or
   headers the requests need and whether there is a CSRF token.
3. **HTML pages.** The last resort, for cabinets that render everything on the server (WHMCS
   is a common case). Pick selectors that do not depend on the design: ids, form fields,
   table cells with data attributes.

Do not send anything that changes state while exploring: no top-ups paid, no power buttons,
no orders. Creating an unpaid top-up invoice is fine if you need to see its answer.

### What to write down for every request

Keep these notes; they go into the pull request description.

- Method and URL, with an example of the parameters.
- Authentication: header name or cookie names, and how long the session lives if you can
  tell.
- The fields the plugin uses: name, type, units (cents or whole units, which currency,
  MB or GB, UTC or local time).
- Every status value you have seen and what it means, including the short ones while a
  server starts, stops or is reinstalled.
- Paging: how many items one answer holds and how to get the next page.
- Errors: what comes back without a session, with a wrong key, when rate limited, and what
  the anti-bot page looks like (status code, body).
- Rate limits and pauses the hoster asks for.
- Whether the request changes anything at the hoster.
- Where it comes from: the documentation, the cabinet's script, or a live request, and the
  date.

`docs/research.md` (in Russian) has these notes for the four built-in hosters.

## 3. Create the plugin from the template

In a checkout of the repository:

```sh
uv sync
uv run hostwatch new-plugin firstbyte --name "FirstByte"
uv run ruff format hostwatch/plugins/firstbyte tests/test_firstbyte.py
uv run pytest tests/test_firstbyte.py
```

The id is lowercase letters, digits and `_`. `ruff format` is there because the new names
change line lengths. The command creates:

- `hostwatch/plugins/firstbyte/__init__.py` — the plugin;
- `tests/test_firstbyte.py` — its tests;
- `tests/fixtures/firstbyte/` — example answers the tests replay.

The copy passes its tests at once. Its requests and answers are made up: replace the
docstring, `site`, `BASE_URL`, the models and the fixtures step by step and keep the tests
green.

## 4. Write the plugin

**Meta.** `PluginMeta(id, name, site, auth, currency)`. `id` must match the folder name.
`currency` is the currency of the account balance, three letters.

**Authentication.**

- `ApiKeyAuth(fields=("api_key",))`: the bot asks the owner for each field and passes them
  in `self.ctx.secrets`.
- `BrowserAuth(start_url, allowed_domains, cookies, extra_cookies)`: the owner logs in
  through the login page. `allowed_domains` are the only sites the browser may open (add
  the captcha's domain if there is one). `cookies` are the names the login must produce;
  the bot stores them as secrets. Read them with `cookie_header(self.ctx.secrets, auth)`.
  A plugin with a session can define `check_login()` to confirm the session works.
- When the hoster gives you a new token or cookie, call `await self.ctx.rotate_secrets(...)`
  before using it.

**Required methods.** `servers()` returns `list[Server]`, `balance()` returns `Balance`.

- Parse answers with strict pydantic models that declare only the fields you use. If an
  answer no longer fits, raise `LayoutChanged`; never show a guessed value.
- Money is `Money(amount=Decimal, currency=...)`. Never pass a float.
- A status you do not know becomes `ServerStatus.UNKNOWN`, with the hoster's word in
  `raw_status`. Short transient states (starting, stopping, reinstalling) also stay
  `UNKNOWN` unless the hoster's docs say clearly what they mean.
- If the hoster pages its lists, read all pages or raise `HosterError` when there are more
  than one; never show half a list as the whole.
- `next_charge_at` is the date of the next charge or the end of the paid period, with a time
  zone.
- `billing` is `HOURLY` when the hoster charges by the hour: then `price` is the price of
  one hour and `period_months` is `None`. Otherwise `PERIODIC`, with `price` per
  `period_months`.
- `autorenew` is `ON` or `OFF` when the hoster has autorenewal, `NOT_AVAILABLE` when it has
  none (hourly billing, for example).

**Errors.** Use the HTTP client in `self.ctx.http`; it already has timeouts and a browser
User-Agent. Map failures to the core's errors, so one broken hoster does not affect the
others:

| Situation | Raise |
|---|---|
| The key or session is dead (often 401) | `AuthExpired` |
| Anti-bot page or 403 | `Blocked` |
| 429, «too many requests» | `RateLimited` |
| The answer has an unexpected shape | `LayoutChanged` |
| The hoster refused with a reason worth showing | `HosterError("reason")` |
| Network timeout | `TimeoutError` |

Never put response bodies, tokens or cookies into error texts or logs.

**Capabilities.** A capability is a set of methods from `hostwatch/core/plugin.py`:
`SupportsUsage`, `SupportsTopup`, `SupportsPayOptions`, `SupportsInvoices`,
`SupportsPayInvoice`, `SupportsAutorenew`, `SupportsRenew`, `SupportsPower`, `SupportsOrder`,
`SupportsRootPassword`. A crypto `TopupMethod` may name its `coin` and `network`: the bot
asks for the coin first. When the payment page lists its coins only once the invoice exists,
return them in `Payment.options` and implement `SupportsPayOptions`. Implement all methods of a protocol or none of them; the bot shows a
button only when the plugin has the capability. `Server.actions` says what is allowed for a
single server (for example, the hoster does not let you stop a dedicated server).

Anything that spends money or changes a server needs extra care: read the price again
before acting, refuse when it differs from what the owner confirmed, and never retry an
order or a payment. In the pull request, say which of these calls you ran on a live account
and which you only wrote from the cabinet's code.

**Fixtures scrubbing.** `hostwatch record` replaces emails, IPs, ids and secrets by itself.
List the JSON paths of names the owner chose in `record_names` (for example
`"servers.*.name"`). For HTML, override `record_text` and use `scrubber.label`,
`scrubber.fake_id` and `scrubber.fake_money`.

## 5. Check it on a live account

With an API key, keep the key in an environment variable, not on the command line:

```sh
export FIRSTBYTE_KEY='...'
uv run hostwatch check firstbyte --key '$FIRSTBYTE_KEY'
```

`check` prints the servers, the balance, the capabilities, the forecast and the time each
call took. It never changes anything. Compare every line with the cabinet.

With a cabinet session, run your branch on a server (`docker-compose.build.yml`, see the
README), connect the hoster in the bot and run
`docker compose exec hostwatch hostwatch check <label>`.

If the hoster binds keys or sessions to an IP, run `check` from the server whose IP is
allowed.

## 6. Record fixtures

```sh
uv run hostwatch record firstbyte --key '$FIRSTBYTE_KEY'
```

It runs every read-only call of the plugin, scrubs the answers and replaces the files in
`tests/fixtures/firstbyte/` (the template examples go away). For an account in the
database: `hostwatch record <label>`.

Each file is one answer: `method`, `url`, `query`, `status` and either `json` or `text`.
The tests replay them by method and URL path; the query is not compared, and several
answers to one path come back in the recorded order. You can write such files by hand for a
draft or for a case your account does not have, but the main set must be recorded.

Open every file and read it. Look for your name, email, phone, IPs, server names, balances,
tokens, cookies and form tokens. Fix the scrubbing in the plugin rather than editing the
files by hand, and record again.

## 7. Tests

The generated test already calls the shared contract test (`tests/contract.py`). It checks
that the meta is valid, money is `Decimal`, capabilities are complete, server ids are unique
and server actions match the capabilities. Adjust the other tests to your real answers. `test_reads_servers` looks at the first
server in the fixtures: keep a running server first, or change the test.

- `test_passes_contract` on the recorded fixtures;
- the HTTP errors map to `AuthExpired`, `Blocked`, `RateLimited`, `HosterError`;
- a changed answer gives `LayoutChanged`;
- a test per capability, on recorded or hand-written answers (`respx`). Calls that change
  anything are tested only this way.

```sh
uv run pytest tests/test_firstbyte.py
```

## 8. A plugin only for your installation

If you do not want to publish the plugin, put its folder next to the bot:

```sh
mkdir -p /opt/hostwatch/plugins
cp -r firstbyte /opt/hostwatch/plugins/
echo 'PLUGINS_DIR=/plugins' >> /opt/hostwatch/.env
cat > /opt/hostwatch/docker-compose.override.yml <<'EOF'
services:
  hostwatch:
    volumes:
      - ./plugins:/plugins:ro
EOF
cd /opt/hostwatch && docker compose up -d
```

`docker compose` reads `docker-compose.override.yml` by itself. A local plugin cannot replace
a built-in one with the same id.

## 9. Checklist before the pull request

- [ ] The hoster's terms allow automated access.
- [ ] `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`,
      `uv run mypy hostwatch` pass.
- [ ] Fixtures are recorded with `hostwatch record`, and you have read every file.
- [ ] `gitleaks detect` finds nothing.
- [ ] No changes outside `hostwatch/plugins/<id>/`, `tests/test_<id>.py` and
      `tests/fixtures/<id>/`. If the core really needs a change, it goes into a separate pull
      request with the reason.
- [ ] The description has your notes from step 2 and the output of `hostwatch check` with
      names, IPs and sums replaced.
- [ ] The description says which calls were run on a live account and which were not.
