MESSAGES: dict[str, str] = {
    "greeting": "Hi, {name}! I keep an eye on your servers.",
    "balance.low": "{hoster} balance is down to {amount}. It lasts until {date}.",
    "forecast.method.hoster_hours": "Method: the hoster's own estimate.",
    "forecast.method.hourly_rate": "Method: balance divided by the sum of hourly rates.",
    "forecast.method.charges": "Method: upcoming charges of servers that renew ({count}).",
    "forecast.method.mixed": (
        "Method: hourly rates plus upcoming charges of servers that renew ({count})."
    ),
    "forecast.method.no_spending": "Method: no charges expected.",
    "forecast.no_spending": "Nothing is spending the balance.",
    "forecast.beyond_horizon": "The money lasts more than a year.",
    "forecast.until": "Lasts until {date}.",
    "forecast.charge": "{date}: {amount} will be charged ({server}).",
    "forecast.more_charges": "{count} more charges.",
    "forecast.shortfall": "{date}: {amount} short for {server}.",
    "forecast.not_renewing": "{server} does not renew.",
    "forecast.not_renewing_until": "{server} does not renew and ends on {date}.",
    "forecast.mismatch": (
        "Real spending over 14 days is {actual}, the forecast says {predicted}."
        " The forecast may be off."
    ),
    # Bot
    "button.cancel": "Cancel",
    "button.refresh": "Refresh",
    "button.yes_delete": "Yes, delete",
    "bot.button.back": "⬅️ Back",
    "bot.button.home": "🏠 Menu",
    "bot.servers.hoster": "<b>{title}</b>: {count} servers",
    "bot.servers.problems": "needs attention",
    "bot.servers.page": "Page {page} of {pages}",
    "bot.servers.line": "{name} — {status}, {ip}",
    "bot.integrations.empty": "Nothing is connected yet. Press «+ Connect».",
    "bot.integrations.connect": "+ Connect",
    "bot.connect.title": "<b>Connect a hoster</b>",
    "bot.connect.choose": "Pick one to connect:",
    "bot.connect.line": "• {hoster} — {site}",
    "bot.connect.all_connected": "Every hoster is connected already.",
    "bot.connect.more": "You can add another account of a hoster that is already connected.",
    "bot.connect.another": "+ another {hoster} account",
    "bot.login.link_gone": "This link no longer works.",
    "stub": "Coming at stage {stage}.",
    "menu.title": "<b>hostwatch</b>\nPick a section.",
    "menu.summary": "Summary",
    "menu.servers": "Servers",
    "menu.balance": "Balance",
    "menu.integrations": "Integrations",
    "menu.order": "Order",
    "menu.journal": "Journal",
    "menu.settings": "Settings",
    "onboarding.language": "Choose a language.",
    "onboarding.timezone": (
        "Time zone for dates and quiet hours. I suggest {suggested}; you can pick another."
    ),
    "onboarding.currency": "Which currency should totals across hosters use?",
    "onboarding.done": "Done. Connect your first hoster.",
    "error.auth_expired": "the key or login no longer works",
    "error.rate_limited": "the hoster asks to slow down",
    "error.blocked": "the hoster blocked the request",
    "error.layout_changed": "the hoster changed its answers; the plugin is outdated",
    "error.browser_busy": "the browser is busy with a login; try again in a few minutes",
    "error.hoster_error": "the hoster refused",
    "error.plugin_error": "plugin error",
    "error.timeout": "the hoster did not answer in time",
    "error.unexpected": "unexpected error",
    "account.failed": "<b>{title}</b>: could not get data ({reason}), data from {date}",
    "account.failed_never": "<b>{title}</b>: could not get data ({reason})",
    "account.no_data": "<b>{title}</b>: no data yet",
    "account.disabled_line": "<b>{title}</b>: disabled",
    "account.status.ok": "connected",
    "account.status.relogin": "log in again",
    "account.status.error": "error",
    "account.status.disabled": "disabled",
    "account.card.status": "Status: {status}",
    "account.card.last_ok": "Last successful poll: {date}",
    "account.card.error": "Error: {reason}",
    "account.card.servers": "Servers: {count}",
    "account.never": "not yet",
    "account.relogin": "Log in again",
    "account.change_key": "Change key",
    "account.disable": "Disable",
    "account.enable": "Enable",
    "account.delete": "Delete",
    "account.delete_confirm": (
        "Remove the account <b>{title}</b> from the bot? Its saved servers and balance history"
        " go too. Nothing changes at the hoster."
    ),
    "account.gone": "This account no longer exists.",
    "status.running": "running",
    "status.stopped": "stopped",
    "status.suspended": "suspended",
    "status.unpaid": "unpaid",
    "status.unknown": "unknown",
    "autorenew.on": "on",
    "autorenew.off": "off",
    "autorenew.na": "the hoster has none",
    "summary.title": "<b>Summary</b> as of {date}",
    "summary.empty": "No hosters connected. Open «Integrations».",
    "summary.lasts_until": "lasts until {date}",
    "summary.no_spending": "not spending",
    "summary.servers": "Servers: {count} ({statuses})",
    "summary.problem_status": "{server}: {status}",
    "summary.problem_not_renewing": "{server} does not renew",
    "summary.problem_ends": "{server} does not renew and ends on {date}",
    "summary.problem_relogin": "log in again",
    "summary.total_balance": "Total on balances ≈ {amount}",
    "summary.total_month": "Total per month ≈ {amount}",
    "summary.rate_note": "≈ at the Bank of Russia rate of {date}",
    "summary.no_rates": "No totals in {currency}: no Bank of Russia rate.",
    "servers.title": "<b>Servers</b>",
    "servers.empty": "No servers yet.",
    "servers.gone": "The hoster no longer lists this server.",
    "servers.kind.vps": "VPS",
    "servers.kind.dedicated": "Dedicated servers",
    "price.hourly": "{amount} per hour",
    "price.month": "{amount} per month",
    "price.months": "{amount} per {months} months",
    "card.kind": "Type: {kind}",
    "card.ip": "IP: <code>{ip}</code>",
    "card.plan": "Plan: {plan}",
    "card.status": "Status: {status} ({raw})",
    "card.price": "Price: {price}",
    "card.next_charge": "Next charge: {date}",
    "card.ends": "Paid until: {date}",
    "card.autorenew": "Autorenewal: {value}",
    "card.usage": "Load: {value}",
    "card.usage_unsupported": "the hoster does not report load",
    "card.usage_none": "no data",
    "card.disk": "disk",
    "card.traffic": "traffic {gb} GB",
    "card.data_at": "Data from {date}",
    "action.start": "Start",
    "action.stop": "Stop",
    "action.restart": "Restart",
    "action.autorenew_on": "Turn autorenewal on",
    "action.autorenew_off": "Turn autorenewal off",
    "action.renew": "Renew",
    "action.autopay": "Autopay",
    "action.order_same": "Order the same",
    "refresh.started": "Refreshing…",
    "refresh.too_often": "Refresh works once a minute per account. Try again a bit later.",
    "balance.title": "<b>Balance</b>",
    "balance.line": "<b>{title}</b>: {amount} (as of {date})",
    "balance.hours_left": "The hoster says it lasts {hours} h.",
    "balance.topup": "Top up {title}",
    "topup.title": "<b>Top up: {title}</b>",
    "topup.balance": "Balance: {amount}",
    "topup.choose_method": "How do you pay?",
    "topup.choose_coin": "Which coin?",
    "topup.choose_coin_network": "Which network for {coin}?",
    "topup.invoice_amount": "Amount: {amount}",
    "topup.choose_after": (
        "Choose the coin and the network: I will get the address from the payment page."
        " You can also choose on the page itself."
    ),
    "topup.getting_address": "Getting the address from the payment page, it takes up to a minute…",
    "topup.address_failed": (
        "No address: {reason}. Choose another coin or pay on the payment page."
    ),
    "topup.options_failed": (
        "Could not read the coins of the payment page: {reason}. Choose on the page."
    ),
    "topup.sbp": "SBP ({name})",
    "topup.crypto": "Crypto",
    "topup.crypto_one": "Crypto ({name})",
    "topup.method": "Method: {name}",
    "topup.minimum": "Minimum: {amount}",
    "topup.choose_amount": "How much?",
    "topup.amount_minimum": "Minimum: {amount}",
    "topup.amount_until": "To the end of next month: {amount}",
    "topup.amount_custom": "Other amount",
    "topup.ask_amount": "Type the amount in {currency}, e.g. 500 or 12.50.",
    "topup.below_minimum": "That is below the minimum ({amount}). Type a larger amount.",
    "topup.creating": "Creating the invoice at the hoster…",
    "topup.invoice_title": "<b>Top-up invoice: {title}</b>",
    "topup.invoice_line": "Amount: {amount}, method: {method}",
    "topup.invoice_id": "Invoice number at the hoster: {id}",
    "topup.pay_sbp": (
        '<a href="{url}">Pay by SBP</a>: on a phone it opens the choice of bank.'
        " From another device, scan the QR. The link lives about 10 minutes."
    ),
    "topup.pay_page": '<a href="{url}">Payment page</a>. You can choose the method there.',
    "topup.send_exactly": "Send exactly {amount} to this address:",
    "topup.expires": "The address is valid until {date}.",
    "topup.press_paid": "When you have paid, press «Paid»: I will check the balance.",
    "topup.open": "Open the payment",
    "topup.paid": "✅ Paid",
    "topup.page": "Hoster's top-up page",
    "topup.need_login": (
        "To make an invoice at {title} the bot needs a cabinet login. Log in again"
        " or top up on the hoster's page."
    ),
    "topup.failed": "The invoice was not created: {reason}. You can top up on the hoster's page.",
    "topup.will_check": "I will check the balance in 2, 5 and 10 minutes.",
    "topup.already_checking": "Already checking the balance for this invoice.",
    "topup.arrived": "<b>The top-up arrived: {title}</b>\nBalance: {amount}",
    "topup.not_seen": (
        "{title}: no top-up seen in {minutes} minutes. If the money left your account,"
        " it will arrive later; the balance is still read on schedule."
    ),
    "integrations.title": "<b>Integrations</b>",
    "integrations.account": "{title}: {status}",
    "integrations.ask_label": (
        "A label for the {hoster} account, e.g. «personal». It tells accounts apart"
        " when a hoster has several. Type it or press the button."
    ),
    "integrations.label_default": "No label ({name})",
    "integrations.label_taken": "The label «{label}» is taken. Type another one.",
    "integrations.ask_key": (
        "Send the {hoster} {field} in one message. I delete it from the chat right away"
        " and keep it encrypted in the database."
    ),
    "integrations.checking": "Key saved, checking access…",
    "journal.title": "<b>Journal</b>",
    "journal.empty": "Nothing yet. Actions with servers and money will show up here.",
    "journal.line": "{date} · {actor} · {hoster}: {action} — {result}",
    "journal.actor.owner": "you",
    "journal.actor.autopay": "autopay",
    "journal.actor.system": "bot",
    "journal.ok": "done",
    "journal.failed": "failed",
    "settings.title": "<b>Settings</b>",
    "settings.language": "Language: {value}",
    "settings.timezone": "Time zone: {value}",
    "settings.currency": "Currency of totals: {value}",
    "settings.thresholds": "<b>Alert thresholds</b>",
    "settings.thresholds_note": (
        "During quiet hours ordinary alerts wait for the end of the quiet hours;"
        " critical ones come at once."
    ),
    "settings.button.language": "Language",
    "settings.button.timezone": "Time zone",
    "settings.button.currency": "Currency",
    "settings.timezone_other": "Other…",
    "settings.timezone_ask": "Type the time zone name, e.g. Asia/Omsk.",
    "settings.timezone_bad": "I do not know this time zone. Example: Europe/Moscow.",
    "threshold.balance_days": "Balance runs out in less than {value} days",
    "threshold.load_percent": "CPU or RAM above {value}%",
    "threshold.disk_percent": "Disk above {value}%",
    "threshold.quiet_hours": "Quiet hours: {value}",
    "threshold.summary_time": "Summary at {value}",
    "threshold.button.balance_days": "Balance threshold",
    "threshold.button.load_percent": "CPU and RAM threshold",
    "threshold.button.disk_percent": "Disk threshold",
    "threshold.button.quiet_hours": "Quiet hours",
    "threshold.button.summary_time": "Summary time",
    "threshold.ask.balance_days": "Now {value}. How many days ahead to warn? A number, 1 to 60.",
    "threshold.ask.load_percent": "Now {value}%. CPU and RAM threshold in percent, 1 to 100.",
    "threshold.ask.disk_percent": "Now {value}%. Disk threshold in percent, 1 to 100.",
    "threshold.ask.quiet_hours": "Now {value}. Type quiet hours like this: 23:00-08:00.",
    "threshold.ask.summary_time": "Now {value}. When to send the summary? E.g. 09:00.",
    "threshold.bad": "I did not understand the value. Try again.",
    # Alerts, backups, releases
    "alert.balance_low": "⚠ <b>{title}</b>: the balance is {amount}, the money lasts until {date}.",
    "alert.balance_low_own": "hostwatch itself runs here, so I warn {days} days earlier.",
    "alert.server_status": "‼ <b>{title}</b>: server {server} is {status}.",
    "alert.ends_soon": "⚠ <b>{title}</b>: {server} does not renew and ends on {date}.",
    "alert.ends_soon_short": (
        "⚠ <b>{title}</b>: {server} ends on {date}, and there is not enough money to renew it."
    ),
    "alert.load": (
        "⚠ <b>{title}</b>: {metric} on {server} is above {percent}% for {polls} polls in a row."
    ),
    "alert.disk": "⚠ <b>{title}</b>: the disk on {server} is {percent}% full.",
    "alert.relogin": "⚠ <b>{title}</b>: you need to log in again. Open «Integrations».",
    "alert.plugin_broken": "⚠ <b>{title}</b>: cannot get data ({reason}).",
    "alert.reminder": "Reminder. {text}",
    "alert.resolved": "✅ Resolved: {subject}.",
    "alert.subject.balance_low": "{title} balance",
    "alert.subject.server": "{title}, server {server}",
    "alert.subject.ends_soon": "{title}, {server} ending",
    "alert.subject.load": "{title}, {metric} on {server}",
    "alert.subject.disk": "{title}, disk on {server}",
    "alert.subject.relogin": "{title}, login",
    "alert.subject.plugin_broken": "{title}, getting data",
    "release.new": "hostwatch {version} is out. What is new: {url}",
    "settings.button.backup": "Send backup",
    "backup.caption": (
        "Database backup of {date}. Secrets in it are encrypted; HOSTWATCH_KEY is not in it."
    ),
    "backup.failed": "Could not make a backup. See the logs.",
    "card.own": (
        "This is the hostwatch server: I warn about this account's balance {days} days earlier."
    ),
    "servers.own": "hostwatch",
    # Login page and browser login
    "web.title": "Log in: {hoster}",
    "web.gone": (
        "This link no longer works: it was opened already, expired or the login ended. Ask the "
        "bot for a new one."
    ),
    "web.placeholder": "Text for the field on the site",
    "web.send": "Type",
    "web.back": "Back",
    "web.cancel": "Cancel",
    "web.done": "Done",
    "web.loading": "Opening the site…",
    "web.checking": "Checking the login…",
    "web.reconnecting": "Connection lost, reconnecting…",
    "web.blocked": "Going to {host} is blocked",
    "web.load_failed": "The site did not open. Press «Back» or «Cancel».",
    "web.notdone_missing": "Not logged in yet: log in on the site, then press «Done».",
    "web.notdone_check": "The site did not confirm the login. Log in and press «Done» again.",
    "web.saved": "{hoster} is connected. You can close this page.",
    "web.fail_browser": "The browser did not start. Try later; the bot's log has details.",
    "web.end_cancelled": "Login cancelled. You can close this page.",
    "web.end_idle": "Login closed after 15 minutes without input. Ask the bot for a new link.",
    "web.end_rejected": "Login closed from the bot with «Not me».",
    "web.end_replaced": "This login is closed: a new link was requested in the bot.",
    "web.end_failed": "The login stopped on an error. Ask the bot for a new link.",
    "login.link": (
        "Log in to <b>{hoster}</b>: open the link on your phone or computer.\n{url}\n\nThe "
        "link works once, for 10 minutes. Log in on the site as usual, then press «Done»."
    ),
    "login.open": "Open login",
    "login.no_address": (
        "I do not know this server's address. Set HOSTWATCH_IP or HOSTWATCH_DOMAIN in .env and "
        "restart the bot."
    ),
    "login.opened": "🔑 The {hoster} login link was opened: {ip}, {browser}.",
    "login.not_me": "Not me",
    "login.killed": "Login closed.",
    "login.connected": "✅ {hoster} is connected.",
    "login.connected_unchecked": (
        "✅ {hoster}: the session is saved, but it could not be checked without a browser "
        "({reason})."
    ),
    "login.ended.cancelled": "{hoster} login cancelled.",
    "login.ended.idle": "{hoster} login closed after 15 minutes without input.",
    "login.ended.rejected": "{hoster} login closed with «Not me».",
    "login.ended.replaced": "The previous {hoster} login is closed: a new link was issued.",
    "login.ended.failed": "{hoster} login stopped: the browser did not start. See the logs.",
    "cookie.usage": (
        "Fallback login: paste cookies by hand.\n<code>/cookie label</code> or <code>/cookie "
        "id</code> for a connected account, <code>/cookie {plugins}</code> for a new one.\n"
        "Accounts with browser login: {accounts}"
    ),
    "cookie.unknown": "No account or hoster «{target}». List: /cookie",
    "cookie.ask": (
        "Send the <b>{hoster}</b> cookies in one message: a header line <code>name=value; "
        "name2=value2</code> or JSON from an extension. Needed: {names}.\nI delete the message "
        "at once. Note: a session made from another IP may not work here."
    ),
    "cookie.missing": "Cookies missing: {names}. Send them again, or /cookie to cancel.",
    "cookie.bad": "Could not read the cookies. Send <code>name=value; …</code> or JSON.",
    "control.confirm_title": "<b>Confirm the action</b>",
    "control.what": "What: {what}",
    "control.where": "Where: {where}",
    "control.how_much": "How much: {amount}",
    "control.yes": "✅ Yes",
    "control.charge": "{amount} from the balance at the hoster",
    "control.charge_with_balance": "{amount} from the balance at the hoster (now {balance})",
    "control.later_charges": "nothing now, then {price} at every renewal",
    "control.no_charge": "no charge",
    "control.what.start": "start the server",
    "control.what.stop": "stop the server",
    "control.what.restart": "restart the server",
    "control.what.autorenew_on": "turn autorenewal on",
    "control.what.autorenew_off": "turn autorenewal off",
    "control.what.renew": "renew for one period",
    "control.what.pay_invoice": "pay invoice #{id} from the balance",
    "control.need_login": "The {title} session has ended. Log in again and repeat.",
    "control.cannot": "It did not work: {reason}.",
    "control.unavailable": "This action is not available now. Here is a fresh card.",
    "control.asking_price": "Asking the hoster for the price…",
    "control.expired": "This button is out of date or was pressed already. Nothing done.",
    "control.cancelled": "Cancelled. Nothing done.",
    "control.running": "Working…",
    "control.running_what": "Working: {what}…",
    "control.timeout": (
        "The hoster did not answer in time: {what}, {where}. The result is unknown;"
        " check the cabinet and press «Refresh» on the card."
    ),
    "control.failed": "It did not work: {what}, {where}. Reason: {reason}.",
    "control.done": "Done: {what}, {where}.\nThe hoster said: {detail}",
    "control.unclear": (
        "The hoster took the request but did not confirm the result: {what}, {where}.\n"
        "Answer: {detail}\nCheck the cabinet."
    ),
    "control.money_notice": "💸 <b>Money action</b>\n{text}",
    "autopay.button.on": "Turn autopay on",
    "autopay.button.off": "Turn autopay off",
    "autopay.button.pay": "✅ Pay",
    "autopay.button.skip": "Don't pay",
    "autopay.button.topup": "Top up",
    "autopay.button.invoices": "Hoster invoices",
    "autopay.card.on": "Bot autopay: on",
    "autopay.card.off": "Bot autopay: off",
    "autopay.card.native_hint": (
        "The hoster has its own autorenewal: turn it on with the button below, and the bot"
        " will watch that the balance is enough."
    ),
    "autopay.settings.button": "💳 Autopay",
    "autopay.settings.title": "<b>Autopay</b>",
    "autopay.settings.about": (
        "The bot pays a renewal invoice the hoster has issued, from the balance, when the"
        " due date is this many days away or closer. Only for servers marked «autopay»"
        " (a button on the card) and only at hosters without their own autorenewal. If the"
        " sum grew by more than 10 %, the bot asks."
    ),
    "autopay.settings.off": "Autopay is not available in this run.",
    "autopay.settings.days": "When: {days} days before the due date",
    "autopay.settings.no_accounts": "No connected hoster whose invoices the bot can pay.",
    "autopay.settings.account": (
        "{title}: limit {limit} a month, spent {spent}, servers with autopay: {count}"
    ),
    "autopay.settings.zero_limit": "  Limit 0: the bot pays nothing until you set it.",
    "autopay.settings.button.days": "When: {days} days",
    "autopay.settings.button.limit": "Limit: {title}",
    "autopay.ask_days": "How many days before the due date to pay? Now {days}. 1 to {max}.",
    "autopay.ask_limit": (
        "Monthly autopay limit for {title}. Now {limit}. Type a sum in the hoster's currency,"
        " e.g. 15 or 15.50. 0 turns paying off."
    ),
    "autopay.bad_days": "A whole number from 1 to {max}, please.",
    "autopay.bad_limit": "A sum from 0 to 1 000 000 in whole cents, e.g. 15.50.",
    "autopay.marked_off": "Autopay for {server} is off.",
    "autopay.marked_on": (
        "Autopay for {server} is on: I pay the renewal invoice {days} days before the due"
        " date, at most {limit} a month."
    ),
    "autopay.marked_on_no_limit": (
        "Autopay for {server} is on, but the monthly limit is 0: I pay nothing until you set"
        " it in «Settings → Autopay»."
    ),
    "autopay.report.paid": (
        "Autopay: paid invoice #{id} for {server} · {title}: {amount}.\nHoster's answer: {detail}"
    ),
    "autopay.report.limit": "Month limit: spent {spent} of {limit}.",
    "autopay.report.unclear": (
        "Autopay: the hoster took the payment of invoice #{id} for {server} · {title}"
        " ({amount}) but did not confirm it.\nAnswer: {detail}\nCheck the cabinet. The bot"
        " will not touch this invoice again by itself."
    ),
    "autopay.report.failed": (
        "Autopay did not pay invoice #{id} for {server} · {title} ({amount}). Reason: {reason}."
        "\nThe bot will not touch this invoice again by itself: check the cabinet and pay"
        " by hand."
    ),
    "autopay.report.grown": (
        "⚠️ Invoice #{id} for {server} · {title} grew: {amount} instead of {reference}."
        " Autopay did not pay it. Pay?"
    ),
    "autopay.report.new": (
        "⚠️ Invoice #{id} for {server} · {title} is {amount}: nothing to compare the sum with."
        " Autopay did not pay it. Pay?"
    ),
    "autopay.report.over_limit": (
        "Invoice #{id} for {server} · {title} ({amount}) was not paid by autopay: the monthly"
        " limit is not enough (spent {spent} of {limit})."
    ),
    "autopay.report.low_balance": (
        "Invoice #{id} for {server} · {title} ({amount}) was not paid by autopay: the balance"
        " is {balance}, {missing} short."
    ),
    "autopay.declined": "OK, the bot will not pay invoice #{id}.",
    "autopay.changed": "The invoice sum changed again, now {amount}. Nothing was paid.",
    "invoices.title": "<b>Invoices to pay: {title}</b>",
    "invoices.none": "No unpaid invoices.",
    "invoices.line": "Invoice #{id}: {amount}, due {due}",
    "invoices.for_server": "For server {server}",
    "invoices.pay": "Pay #{id} · {amount}",
    "invoices.not_service": "Not a server renewal: pay it in the cabinet if needed.",
    "invoices.loading": "Reading invoices at the hoster…",
    "invoices.not_payable": (
        "This invoice cannot be paid from the bot any more: it is paid, cancelled or changed."
    ),
    "balance.invoices": "Invoices {title}",
    "journal.action.topup": "top-up invoice",
    "journal.action.login": "login",
    "journal.action.start": "start",
    "journal.action.stop": "stop",
    "journal.action.restart": "restart",
    "journal.action.autorenew_on": "autorenewal on",
    "journal.action.autorenew_off": "autorenewal off",
    "journal.action.renew": "renewal",
    "journal.action.pay_invoice": "invoice payment",
    # Ordering servers (stage 13)
    "order.title_any": "<b>Order a server</b>",
    "order.title": "<b>Order a server · {title}</b>",
    "order.no_accounts": "No connected hoster where the bot can order servers.",
    "order.choose_account": "Where to order?",
    "order.choose_location": "Pick a location:",
    "order.location_button": "{name} · from {amount}/mo",
    "order.empty": "The hoster sells nothing right now.",
    "order.location": "Location: {name}",
    "order.choose_plan": "Pick a plan:",
    "order.plan_line": "• <b>{name}</b> — {specs}, from {amount}/mo",
    "order.plan_button": "{name} · {amount}/mo",
    "order.more_plans": "{count} more plans. The cheapest are shown.",
    "order.plan": "Plan: {name} ({specs})",
    "order.choose_os": "Pick an OS:",
    "order.no_os": "This plan has no OS the bot can order.",
    "order.os": "OS: {name}",
    "order.choose_period": "Pick a term:",
    "order.period_hourly": "Hourly · ≈{amount}/mo",
    "order.period_months": "{months} mo · {amount}",
    "order.period": "Term: {period}",
    "order.hourly": "hourly",
    "order.months": "{months} mo",
    "order.price": "Price: {price}",
    "order.price_hourly": "≈{amount} a month, charged by the hour ({hour} an hour)",
    "order.price_changed": "⚠️ The price changed: the catalog said {old}, the hoster asks {new}.",
    "order.balance": "Balance: {amount}",
    "order.key_set": "SSH key: I will add the key from the settings ({name}).",
    "order.key_missing": (
        "SSH key: none in the settings. You can log in with the password"
        " («Show password» on the server card)."
    ),
    "order.key_not_supported": (
        "SSH key: this hoster does not add a key with the order. Log in with the password."
    ),
    "order.limit_ok": "Order limit: {spent} of {limit} spent this month.",
    "order.limit_over": (
        "Order limit: {spent} of {limit} spent this month."
        " This order goes above it, I will ask once more."
    ),
    "order.untested": (
        "⚠️ Ordering at this hoster is not tested live yet: check the cabinet after it."
    ),
    "order.ask": "Order for {price}?",
    "order.second_title": "<b>The order goes above the monthly limit</b>",
    "order.what": "What: {what}",
    "order.second_ask": "Order anyway? This is the second confirmation.",
    "order.second_yes": "✅ Yes, order",
    "order.shortfall": (
        "Not enough money on the balance: {need} needed, {balance} there, {missing} missing."
    ),
    "order.topup_button": "Top up {amount} · {name}",
    "order.topup_hint": "Top up the balance and press «Check again».",
    "order.topup_none": "Top up the balance in the hoster's cabinet and press «Check again».",
    "order.check_again": "🔄 Check again",
    "order.loading": "Reading the hoster's catalog…",
    "order.asking_price": "Asking the hoster for the price and the balance…",
    "order.cancelled": "Order cancelled. Nothing was ordered.",
    "order.running": "Ordering…",
    "order.timeout": (
        "The hoster did not answer in time: {what}. The order may have gone through. Check the"
        " cabinet and do not order again until you are sure."
    ),
    "order.price_moved": (
        "Not ordered: {what}. The price changed before the order: {old} then, {new} now."
        " Start the order again."
    ),
    "order.failed": "Not ordered: {what}. Reason: {reason}.",
    "order.unclear": (
        "Unclear answer from the hoster: {what}. Check the cabinet.\nHoster's answer: {detail}"
    ),
    "order.invoice": (
        "The hoster issued invoice #{invoice} instead of taking the balance: {what}."
        " The server comes after it is paid.\nHoster's answer: {detail}"
    ),
    "order.done": (
        "Ordered: {what}, {price}. The server shows in the list after the next poll."
        "\nHoster's answer: {detail}"
    ),
    "order.password": (
        "🔑 Root password of {server}: <code>{password}</code>\n"
        "This message goes away in {minutes} min. The bot does not keep the password."
    ),
    "order.password_asking": "Asking the hoster for the password…",
    "order.button.password": "🔑 Show password",
    "order.journal.order": "server order",
    "order.journal.root_password": "password shown",
    "order.settings.button": "Orders: limit and SSH key",
    "order.settings.title": "<b>Ordering servers</b>",
    "order.settings.limit": "Order limit a month: {amount}",
    "order.settings.limit_note": (
        "An order above the limit takes two confirmations. With a limit of 0, every order does."
    ),
    "order.settings.key": "SSH key for new servers: {name}",
    "order.settings.no_key": "none",
    "order.settings.button.limit": "Change the limit",
    "order.settings.button.key": "Set the SSH key",
    "order.settings.button.no_key": "Remove the SSH key",
    "order.settings.ask_limit": (
        "Type the monthly order limit in {currency}, e.g. 1000. 0 asks twice for every order."
    ),
    "order.settings.bad_limit": "That is not a sum. Type a number of 0 or more, e.g. 1000.",
    "order.settings.ask_key": (
        "Send the public SSH key as one line, e.g. the contents of ~/.ssh/id_ed25519.pub."
    ),
    "order.settings.bad_key": (
        "This does not look like a public key. It is one line: «ssh-ed25519 AAAA… comment»."
    ),
    "order.settings.private_key": (
        "This is a private key: I deleted the message. Send the public one, the .pub file."
    ),
    # One menu in the chat (1.1)
    "menu.button": "☰ Menu",
    "menu.closed": "Menu closed: the current one is below.",
    "menu.stale": "This menu is closed, the current one is at the bottom",
    "bot.login.expired": "The {hoster} login link expired: nobody opened it within 10 minutes.",
    "topup.expired": "The invoice expired. If you did not pay in time, make a new one.",
    "topup.again": "New invoice",
    "alert.button.topup": "💰 Top up {title}",
    "alert.button.server": "🖥 {server}",
    "alert.button.account": "🔌 {title}",
}
