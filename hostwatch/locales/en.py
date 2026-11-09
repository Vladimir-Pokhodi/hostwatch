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
    "button.cancel": "✖️ Cancel",
    "button.refresh": "🔃 Refresh",
    "button.yes_delete": "🗑 Yes, delete",
    "bot.button.back": "‹ Back",
    "bot.button.home": "🏠 Home",
    "bot.servers.page": "Page {page} of {pages}",
    "bot.integrations.empty": "Press «＋ Connect» and pick a hoster.",
    "bot.integrations.connect": "＋ Connect",
    "bot.connect.title": "<b>＋ Connect a hoster</b>",
    "bot.connect.choose": "Pick a hoster.",
    "bot.connect.all_connected": "Every hoster is connected already.",
    "bot.connect.more": "Another account of the same hoster: the «＋ another» buttons.",
    "bot.connect.another": "＋ another {hoster}",
    "bot.login.link_gone": "This link no longer works.",
    "stub": "Coming at stage {stage}.",
    "menu.servers": "🖥 Servers",
    "menu.balance": "💰 Money",
    "menu.integrations": "🔌 Hosters",
    "menu.order": "🛒 Order",
    "menu.journal": "📜 Journal",
    "menu.settings": "⚙️ Settings",
    "onboarding.language": "Choose a language.",
    "onboarding.timezone": (
        "Time zone for dates and quiet hours. I suggest {suggested}; you can pick another."
    ),
    "onboarding.currency": "Which currency should totals across hosters use?",
    "onboarding.done": "✅ Done. Connect your first hoster.",
    "error.auth_expired": "the key or login no longer works",
    "error.rate_limited": "the hoster asks to slow down",
    "error.blocked": "the hoster blocked the request",
    "error.layout_changed": "the hoster changed its answers; the plugin is outdated",
    "error.browser_busy": "the browser is busy with a login; try again in a few minutes",
    "error.hoster_error": "the hoster refused",
    "error.plugin_error": "plugin error",
    "error.timeout": "the hoster did not answer in time",
    "error.unexpected": "unexpected error",
    "account.failed": "no connection ({reason}), data from {date}",
    "account.failed_never": "no connection ({reason})",
    "account.status.ok": "connected",
    "account.status.relogin": "login needed",
    "account.status.error": "error",
    "account.status.disabled": "disabled",
    "account.card.last_ok": "🕓 Last poll: {date}",
    "account.never": "🕓 No polls yet",
    "account.relogin": "🔑 Log in again",
    "account.change_key": "🗝 Change key",
    "account.disable": "⏸ Disable",
    "account.enable": "▶️ Enable",
    "account.delete": "🗑 Delete",
    "account.delete_confirm": (
        "<b>🗑 Delete {title}?</b>\nIts servers and history in the bot go too.\nNothing "
        "changes at the hoster."
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
    "summary.title": "<b>📰 Summary · {date}</b>",
    "servers.empty": "No hosters connected",
    "servers.gone": "The hoster no longer lists this server.",
    "servers.kind.vps": "VPS",
    "servers.kind.dedicated": "dedicated",
    "price.hourly": "{amount}/h · {month}/mo",
    "price.month": "{amount}/mo",
    "price.months": "{amount} per {months} mo",
    "card.ip": "IP <code>{ip}</code>",
    "card.price": "💳 {price}",
    "card.next_charge": "charge {date} ({left})",
    "card.ends": "paid until {date} ({left})",
    "card.autorenew": "🔁 Autorenewal: {value}",
    "card.usage_unsupported": "The hoster does not report load",
    "card.usage_none": "No load data yet",
    "card.disk": "Disk",
    "card.traffic": "traffic {gb} GB",
    "card.data_at": "🕓 Data from {date}",
    "action.start": "▶️ Start",
    "action.stop": "⏻ Stop",
    "action.restart": "🔄 Restart",
    "action.autorenew_on": "🔁 Autorenewal on",
    "action.autorenew_off": "🔁 Autorenewal off",
    "action.renew": "📅 Renew",
    "action.autopay": "💳 Autopay",
    "action.order_same": "🛒 Order same",
    "refresh.started": "🔃 Refreshing…",
    "refresh.too_often": "Refresh works once a minute per account. Try again a bit later.",
    "balance.title": "<b>Balance</b>",
    "balance.line": "<b>{title}</b>: {amount} (as of {date})",
    "balance.hours_left": "The hoster says it lasts {hours} h.",
    "balance.topup": "💰 Top up {title}",
    "topup.title": "<b>💰 Top-up · {title}</b>",
    "topup.balance": "Balance: {amount}",
    "topup.choose_coin": "Which coin?",
    "topup.choose_coin_network": "Which network for {coin}?",
    "topup.choose_after": "Pick a coin: I will take the address from the payment page.",
    "topup.getting_address": "⏳ Taking the address from the payment page, up to a minute…",
    "topup.address_failed": (
        "🔴 No address: {reason}.\nPick another coin or pay on the payment page."
    ),
    "topup.options_failed": (
        "🔴 Could not read the coins: {reason}.\nPick one on the payment page."
    ),
    "topup.sbp": "⚡ SBP · {name}",
    "topup.crypto": "🪙 Crypto",
    "topup.crypto_one": "🪙 {name}",
    "topup.method": "Method: {name}",
    "topup.minimum": "minimum {amount}",
    "topup.choose_amount": "How much?",
    "topup.amount_minimum": "{amount} · minimum",
    "topup.amount_until": "{amount} · to end of next month",
    "topup.amount_custom": "✏️ Other sum",
    "topup.ask_amount": "✏️ Type the sum in {currency}, e.g. 500.",
    "topup.below_minimum": "⚠️ That is below the minimum ({amount}).",
    "topup.creating": "⏳ Creating the invoice at the hoster…",
    "topup.invoice_title": "<b>💰 Invoice · {title}</b>",
    "topup.invoice_line": "{amount} · {method} · #{id}",
    "topup.pay_sbp": (
        "⏳ The link lives about 10 minutes. The QR is for paying from another device."
    ),
    "topup.pay_page": "The method can be chosen on the payment page.",
    "topup.send_exactly": "Send exactly {amount} to:",
    "topup.expires": "⏳ The address is valid until {date}.",
    "topup.press_paid": "Pay, then press «✅ Paid».",
    "topup.open": "💳 Pay",
    "topup.paid": "✅ Paid",
    "topup.page": "🌐 On the hoster's site",
    "topup.need_login": (
        "🔴 An invoice at {title} needs a cabinet login.\nLog in again or top up on the "
        "hoster's site."
    ),
    "topup.failed": "🔴 No invoice: {reason}.\nYou can top up on the hoster's site.",
    "topup.will_check": "🔃 I will check the balance in 2, 5 and 10 minutes.",
    "topup.already_checking": "Already checking the balance for this invoice.",
    "topup.arrived": "<b>🟢 Top-up arrived · {title}</b>\nBalance: {amount}",
    "topup.not_seen": (
        "<b>🟡 {title}: no top-up seen yet</b>\n{minutes} min passed. If the money left "
        "your account, it will arrive later: I read the balance on schedule."
    ),
    "integrations.ask_label": (
        "<b>🏷 Label for {hoster}</b>\nFor example «personal»: needed with several "
        "accounts.\nType it or press the button."
    ),
    "integrations.label_default": "No label ({name})",
    "integrations.label_taken": "⚠️ The label «{label}» is taken. Type another one.",
    "integrations.ask_key": (
        "<b>🗝 {hoster} {field}</b>\nSend it in one message.\nI delete it from the chat at "
        "once and keep it encrypted."
    ),
    "integrations.checking": "🔃 Key saved, checking access…",
    "journal.title": "📜 Journal",
    "journal.empty": "Empty so far: server and money actions will show up here.",
    "journal.line": "{mark} {date} · {who}{hoster} · {action}",
    "settings.title": "Settings",
    "settings.button.language": "🌐 Language",
    "settings.button.timezone": "🕓 Time zone",
    "settings.button.currency": "💱 Currency",
    "settings.timezone_other": "Other…",
    "settings.timezone_ask": "Type the time zone name, e.g. Asia/Omsk.",
    "settings.timezone_bad": "⚠️ Unknown time zone. Example: Europe/Berlin.",
    "threshold.button.balance_days": "🔔 Money alert",
    "threshold.button.load_percent": "📈 CPU and RAM",
    "threshold.button.disk_percent": "💾 Disk",
    "threshold.button.quiet_hours": "🌙 Quiet hours",
    "threshold.button.summary_time": "📰 Summary time",
    "threshold.ask.balance_days": "Now {value}. How many days ahead to warn? A number, 1 to 60.",
    "threshold.ask.load_percent": "Now {value}%. CPU and RAM threshold in percent, 1 to 100.",
    "threshold.ask.disk_percent": "Now {value}%. Disk threshold in percent, 1 to 100.",
    "threshold.ask.quiet_hours": (
        "Now {value}. Type quiet hours like 23:00-08:00.\nNormal alerts wait for their "
        "end; critical ones come at once."
    ),
    "threshold.ask.summary_time": "Now {value}. When to send the summary? E.g. 09:00.",
    "threshold.bad": "⚠️ I did not understand the value. Try again.",
    # Alerts, backups, releases
    "alert.balance_low": "💰 <b>{title}: money until {date}</b>\nBalance: {amount}.",
    "alert.balance_low_own": "hostwatch runs here, so I warn {days} days earlier.",
    "alert.server_status": "🔴 <b>{title} · {server}: {status}</b>",
    "alert.ends_soon": "⚠️ <b>{title} · {server}: ends {date}</b>\nAutorenewal is off.",
    "alert.ends_soon_short": (
        "⚠️ <b>{title} · {server}: ends {date}</b>\nNot enough money to renew."
    ),
    "alert.load": (
        "⚠️ <b>{title} · {server}: {metric} above {percent}%</b>\n{polls} polls in a row."
    ),
    "alert.disk": "⚠️ <b>{title} · {server}: disk {percent}%</b>",
    "alert.relogin": "🔴 <b>{title}: login needed</b>",
    "alert.plugin_broken": "🔴 <b>{title}: no data</b>\n{reason}.",
    "alert.reminder": "⏰ Reminder\n{text}",
    "alert.resolved": "🟢 Resolved: {subject}.",
    "alert.subject.balance_low": "{title} balance",
    "alert.subject.server": "{title}, server {server}",
    "alert.subject.ends_soon": "{title}, {server} ending",
    "alert.subject.load": "{title}, {metric} on {server}",
    "alert.subject.disk": "{title}, disk on {server}",
    "alert.subject.relogin": "{title}, login",
    "alert.subject.plugin_broken": "{title}, getting data",
    "release.new": "hostwatch {version} is out. What is new: {url}",
    "settings.button.backup": "📦 Backup",
    "backup.caption": (
        "Database backup of {date}. Secrets in it are encrypted; HOSTWATCH_KEY is not in it."
    ),
    "backup.failed": "Could not make a backup. See the logs.",
    "card.own": "ℹ️ This is hostwatch's server: I warn about money {days} days earlier",
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
        "<b>🔑 Log in to {hoster}</b>\nOpen the link on a phone or computer:\n{url}\nLog in "
        "to the site as usual and press «Done».\n⏳ One-time link, lives 10 minutes."
    ),
    "login.open": "🔑 Open the login",
    "login.no_address": (
        "I do not know this server's address. Set HOSTWATCH_IP or HOSTWATCH_DOMAIN in .env and "
        "restart the bot."
    ),
    "login.opened": "🔑 The {hoster} login link was opened: {ip}, {browser}.",
    "login.not_me": "🚫 Not me",
    "login.killed": "Login closed.",
    "login.connected": "🟢 {hoster} connected.",
    "login.connected_unchecked": (
        "🟡 {hoster}: session saved, but it could not be checked without a browser ({reason})."
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
    "control.confirm_title": "<b>❓ {what}?</b>",
    "control.where": "🖥 {where}",
    "control.how_much": "💰 {amount}",
    "control.yes": "✅ Yes",
    "control.charge": "{amount} will be charged from the balance",
    "control.charge_with_balance": "{amount} will be charged · balance {balance}",
    "control.later_charges": "Nothing now, then {price} at each renewal",
    "control.no_charge": "No charge",
    "control.what.start": "start the server",
    "control.what.stop": "stop the server",
    "control.what.restart": "restart the server",
    "control.what.autorenew_on": "turn autorenewal on",
    "control.what.autorenew_off": "turn autorenewal off",
    "control.what.renew": "renew for one period",
    "control.what.pay_invoice": "pay invoice #{id} from the balance",
    "control.need_login": "🔴 The {title} session ended. Log in again and retry.",
    "control.cannot": "🔴 Failed: {reason}.",
    "control.unavailable": "This action is not available now. Here is a fresh card.",
    "control.asking_price": "Asking the hoster for the price…",
    "control.expired": "This button is out of date or was pressed already. Nothing done.",
    "control.cancelled": "Cancelled. Nothing done.",
    "control.running": "Working…",
    "control.running_what": "⏳ Running: {what}…",
    "control.timeout": (
        "<b>🟡 Result unknown</b>\n{what} · {where}\nThe hoster did not answer in time. "
        "Check the cabinet and press «🔃 Refresh»."
    ),
    "control.failed": "<b>🔴 Failed</b>\n{what} · {where}\nReason: {reason}.",
    "control.done": "<b>✅ Done</b>\n{what} · {where}\nHoster's answer: {detail}",
    "control.unclear": (
        "<b>🟡 The hoster did not confirm</b>\n{what} · {where}\nAnswer: {detail}\nCheck "
        "the cabinet."
    ),
    "control.money_notice": "💸 Money action\n{text}",
    "autopay.button.on": "💳 Autopay on",
    "autopay.button.off": "💳 Autopay off",
    "autopay.button.pay": "✅ Pay",
    "autopay.button.skip": "✖️ Do not pay",
    "autopay.button.topup": "💰 Top up",
    "autopay.button.invoices": "🧾 Invoices",
    "autopay.card.on": "💳 Bot autopay: on",
    "autopay.card.off": "💳 Bot autopay: off",
    "autopay.card.native_hint": (
        "💡 The hoster has autorenewal: turn it on with the button below."
    ),
    "autopay.settings.button": "💳 Autopay",
    "autopay.settings.title": "<b>💳 Autopay</b>",
    "autopay.settings.about": (
        "The bot pays a renewal invoice from the balance shortly before the due "
        "date.\nOnly for marked servers, and only at hosters without their own "
        "autorenewal.\nIf the sum grew by more than 10 %, the bot asks."
    ),
    "autopay.settings.off": "Autopay is not available in this run.",
    "autopay.settings.days": "⏰ Pay {days} days before the due date",
    "autopay.settings.no_accounts": "No connected hoster whose invoices the bot can pay.",
    "autopay.settings.account": "{title}: limit {limit}/mo · spent {spent} · servers {count}",
    "autopay.settings.zero_limit": "  ⚠️ Limit 0: I pay nothing until you set it.",
    "autopay.settings.button.days": "⏰ Due: {days} d.",
    "autopay.settings.button.limit": "💰 {title} limit",
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
        "<b>✅ Autopay: invoice #{id}</b>\n{server} · {title} · {amount}\nHoster's answer: {detail}"
    ),
    "autopay.report.limit": "This month: {spent} of {limit}.",
    "autopay.report.unclear": (
        "<b>🟡 Autopay: invoice #{id} not confirmed</b>\n{server} · {title} · "
        "{amount}\nAnswer: {detail}\nCheck the cabinet. The bot will not touch this "
        "invoice again."
    ),
    "autopay.report.failed": (
        "<b>🔴 Autopay: invoice #{id} not paid</b>\n{server} · {title} · {amount}\nReason: "
        "{reason}.\nThe bot will not touch this invoice again: pay it by hand."
    ),
    "autopay.report.grown": (
        "<b>⚠️ Invoice #{id} grew</b>\n{server} · {title}: {amount} instead of "
        "{reference}\nAutopay did not pay it. Pay?"
    ),
    "autopay.report.new": (
        "<b>⚠️ Invoice #{id}: nothing to compare with</b>\n{server} · {title}: "
        "{amount}\nAutopay did not pay it. Pay?"
    ),
    "autopay.report.over_limit": (
        "<b>🟡 Invoice #{id} waits: limit</b>\n{server} · {title}: {amount}\nSpent {spent} "
        "of {limit} this month."
    ),
    "autopay.report.low_balance": (
        "<b>🟡 Invoice #{id} waits: low balance</b>\n{server} · {title}: {amount}\nBalance "
        "{balance}, {missing} short."
    ),
    "autopay.declined": "OK, the bot will not pay invoice #{id}.",
    "autopay.changed": "The invoice sum changed again, now {amount}. Nothing was paid.",
    "invoices.title": "<b>🧾 Invoices · {title} · {count}</b>",
    "invoices.none": "🟢 No unpaid invoices.",
    "invoices.line": "#{id} · {amount}",
    "invoices.due": "due {due}",
    "invoices.pay": "💳 #{id} · {amount}",
    "invoices.not_service": "Pay «not a renewal» invoices in the cabinet.",
    "invoices.loading": "Reading invoices at the hoster…",
    "invoices.not_payable": (
        "🟡 This invoice cannot be paid from the bot any more: paid, cancelled or changed."
    ),
    "balance.invoices": "🧾 {title} invoices",
    "journal.action.topup": "top-up invoice",
    "journal.action.login": "login",
    "journal.action.start": "start",
    "journal.action.stop": "stop",
    "journal.action.restart": "restart",
    "journal.action.autorenew_on": "autorenewal on",
    "journal.action.autorenew_off": "autorenewal off",
    "journal.action.renew": "renewal",
    "journal.action.pay_invoice": "invoice payment",
    "journal.action.connect": "hoster connected",
    "journal.action.disconnect": "hoster disconnected",
    "journal.action.account_status": "status: {status}",
    "journal.action.account_back": "working again",
    "journal.action.settings": "setting «{name}»: {old} → {new}",
    "journal.action.topup_paid": "«Paid» pressed",
    "journal.action.topup_arrived": "top-up arrived",
    "journal.action.topup_not_seen": "top-up not seen",
    "journal.action.autopay_on": "autopay on",
    "journal.action.autopay_off": "autopay off",
    "journal.action.autopay_hold": "autopay waits: {reason}",
    "journal.action.autopay_approved": "autopay approved",
    "journal.action.autopay_declined": "autopay declined",
    "journal.hold.ask_grown": "the sum grew",
    "journal.hold.ask_new": "a new invoice",
    "journal.hold.over_limit": "over the limit",
    "journal.hold.low_balance": "low balance",
    "journal.setting.lang": "language",
    "journal.setting.timezone": "time zone",
    "journal.setting.currency": "currency",
    "journal.setting.threshold.balance_days": "money warning, days",
    "journal.setting.threshold.load_percent": "CPU and RAM threshold",
    "journal.setting.threshold.disk_percent": "disk threshold",
    "journal.setting.threshold.quiet_hours": "quiet hours",
    "journal.setting.threshold.summary_time": "summary time",
    "journal.setting.order.limit": "order limit",
    "journal.setting.order.ssh_key": "SSH key for orders",
    "journal.setting.autopay.days": "autopay days ahead",
    "journal.setting.autopay.limit": "autopay limit",
    "journal.setting.set": "set",
    "journal.setting.cleared": "removed",
    "journal.who.owner": "you",
    "journal.who.autopay": "autopay",
    "journal.who.system": "bot",
    # Ordering servers (stage 13)
    "order.title_any": "<b>🛒 Order a server</b>",
    "order.title": "<b>🛒 Order · {title}</b>",
    "order.no_accounts": "No connected hoster where the bot can order servers.",
    "order.choose_account": "Pick a hoster.",
    "order.choose_location": "Pick a location.",
    "order.location_button": "{name} · from {amount}/mo",
    "order.empty": "The hoster sells nothing right now.",
    "order.location": "📍 {name}",
    "order.choose_plan": "Pick a plan.",
    "order.plan_line": "• <b>{name}</b> — {specs}, from {amount}/mo",
    "order.plan_button": "{name} · {amount}/mo",
    "order.more_plans": "{count} more plans. The cheapest are shown.",
    "order.plan": "📦 {name} · {specs}",
    "order.choose_os": "Pick an OS.",
    "order.no_os": "This plan has no OS the bot can order.",
    "order.os": "💿 {name}",
    "order.choose_period": "Pick a period.",
    "order.period_hourly": "Hourly · ≈{amount}/mo",
    "order.period_months": "{months} mo · {amount}",
    "order.period": "📅 {period}",
    "order.hourly": "hourly",
    "order.months": "{months} mo",
    "order.price": "💳 {price}",
    "order.price_hourly": "≈{amount}/mo, hourly ({hour}/h)",
    "order.price_changed": "⚠️ The price changed: the catalog said {old}, the hoster asks {new}.",
    "order.balance": "💰 Balance: {amount}",
    "order.key_set": "🔑 SSH key from the settings ({name})",
    "order.key_missing": "🔑 No key: log in by password (the «🔑 Password» button on the card)",
    "order.key_not_supported": "🔑 The hoster does not set a key: log in by password",
    "order.limit_ok": "Order limit: {spent} of {limit} this month",
    "order.limit_over": "⚠️ Above the limit: {spent} of {limit} this month, I will ask again",
    "order.untested": "⚠️ Ordering here is not tested live: check the cabinet afterwards.",
    "order.ask": "❓ Order for {price}?",
    "order.second_title": "<b>⚠️ Order above the monthly limit</b>",
    "order.what": "What: {what}",
    "order.second_ask": "Order for sure? This is the second confirmation.",
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
    "order.button.password": "🔑 Password",
    "order.journal.order": "server order",
    "order.journal.root_password": "password shown",
    "order.settings.button": "🛒 Orders",
    "order.settings.title": "<b>🛒 Ordering servers</b>",
    "order.settings.limit": "💰 Monthly limit: {amount}",
    "order.settings.limit_note": ("Above the limit: two confirmations. Limit 0: for every order."),
    "order.settings.key": "🔑 SSH key: {name}",
    "order.settings.no_key": "none",
    "order.settings.button.limit": "💰 Limit",
    "order.settings.button.key": "🔑 Set key",
    "order.settings.button.no_key": "🗑 Remove key",
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
    "bot.login.expired": (
        "<b>🔴 The {hoster} login link expired</b>\nNobody opened it in 10 minutes."
    ),
    "topup.expired": ("<b>🔴 The invoice expired</b>\nIf you did not pay in time, make a new one."),
    "topup.again": "💰 New invoice",
    "card.charts": "📊 Charts",
    "webapp.button": "📊 Analytics",
    "menu.problems": "⚠️ Problems ({count})",
    "menu.connect": "＋ Connect a hoster",
    "time.today": "today {time}",
    "time.tomorrow": "tomorrow {time}",
    "time.today_word": "today",
    "time.tomorrow_word": "tomorrow",
    "time.in_days": "in {n} d.",
    "time.days": "{n} d.",
    "plural.server": "server|servers",
    "plural.hoster": "hoster|hosters",
    "plural.problem": "problem|problems",
    "home.ok": "All good",
    "home.empty": "No hosters connected",
    "home.empty_hint": "Connect the first one: the button below.",
    "home.more": "…and {count} more",
    "home.balances": "💰 On balances: {amount}",
    "home.month": "💳 A month: {amount}",
    "problems.none": "No problems",
    "problem.relogin": "login needed",
    "problem.no_data": "no data: {reason}",
    "problem.money_until": "money until {date}",
    "problem.shortfall": "{date} {amount} short for {server}",
    "problem.ends": "ends {date}, does not renew",
    "problem.button.account": "🔌 {title}",
    "problem.button.topup": "💰 Top up {title}",
    "problem.button.server": "🖥 {server}",
    "summary.open": "🏠 Open the menu",
    "summary.problems": "⚠️ Problems",
    "servers.none": "No servers.",
    "servers.all_ok": "all good",
    "servers.pick": "Pick a hoster.",
    "servers.count.vps": "VPS: {n}",
    "servers.count.dedicated": "dedicated: {n}",
    "card.no_ip": "IP —",
    "money.title": "Money",
    "money.ok": "all good",
    "money.attention": "needs attention",
    "money.no_spending": "not spending",
    "money.beyond_horizon": "over a year",
    "money.until": "until {date}",
    "money.no_data": "no data",
    "money.data_at": "data from {date}",
    "money.shortfall": "{title}: {date} {amount} short for {server}",
    "money.rate_note": "≈ at the Bank of Russia rate of {date}",
    "money.button.info": "ℹ️ How I count",
    "money.info_title": "How the forecast is counted",
    "money.read_at": "{amount} at {date}",
    "money.info_rates": (
        "«≈» totals are in the currency from the settings, at the Bank of Russia rate."
    ),
    "topup.step": "Step {n} of {total} · {what}",
    "topup.step.method": "method",
    "topup.step.amount": "amount",
    "topup.step.pay": "payment",
    "topup.no_methods": "No top-up from the bot: only on the hoster's site.",
    "topup.sbp_plain": "⚡ SBP",
    "topup.invoice_short": "{amount} · #{id}",
    "integrations.none": "No hosters connected",
    "integrations.all_ok": "all connected",
    "integrations.can_add": "＋ Can connect: {names}",
    "journal.page": "page {page}",
    "settings.general": "🌐 {language} · {tz} · {currency}",
    "settings.alerts": "🔔 Money {days} d. ahead · CPU/RAM {load}% · disk {disk}%",
    "settings.times": "🌙 Quiet hours {quiet} · 📰 summary at {summary}",
    "invoices.other": "not a renewal",
    "topup.lasts": "💰 Lasts: {lasts}",
}
