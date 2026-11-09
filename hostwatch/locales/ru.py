MESSAGES: dict[str, str] = {
    "greeting": "Привет, {name}! Я слежу за вашими серверами.",
    "balance.low": "На балансе {hoster} осталось {amount}. Хватит до {date}.",
    "forecast.method.hoster_hours": "Метод: срок, который считает сам хостер.",
    "forecast.method.hourly_rate": "Метод: баланс, делённый на сумму почасовых ставок.",
    "forecast.method.charges": "Метод: будущие списания по серверам с продлением ({count}).",
    "forecast.method.mixed": (
        "Метод: почасовые ставки и будущие списания по серверам с продлением ({count})."
    ),
    "forecast.method.no_spending": "Метод: списаний не ожидается.",
    "forecast.no_spending": "Баланс не тратится.",
    "forecast.beyond_horizon": "Денег хватит больше чем на год.",
    "forecast.until": "Хватит до {date}.",
    "forecast.charge": "{date} спишется {amount} ({server}).",
    "forecast.more_charges": "И ещё списаний: {count}.",
    "forecast.shortfall": "{date} не хватит {amount} на {server}.",
    "forecast.not_renewing": "{server} не продлевается.",
    "forecast.not_renewing_until": "{server} не продлевается, закончится {date}.",
    "forecast.mismatch": (
        "Реальный расход за 14 дней {actual}, по прогнозу {predicted}. Прогноз может быть неточным."
    ),
    # Bot
    "button.cancel": "✖️ Отмена",
    "button.refresh": "🔃 Обновить",
    "button.yes_delete": "🗑 Да, удалить",
    "bot.button.back": "‹ Назад",
    "bot.button.home": "🏠 Главная",
    "bot.servers.page": "Стр. {page} из {pages}",
    "bot.integrations.empty": "Нажмите «＋ Подключить» и выберите хостера.",
    "bot.integrations.connect": "＋ Подключить",
    "bot.connect.title": "<b>＋ Подключить хостера</b>",
    "bot.connect.choose": "Выберите хостера.",
    "bot.connect.all_connected": "Все хостеры уже подключены.",
    "bot.connect.more": "Второй аккаунт того же хостера — кнопки «＋ ещё».",
    "bot.connect.another": "＋ ещё {hoster}",
    "bot.login.link_gone": "Ссылка больше не действует.",
    "stub": "Будет на этапе {stage}.",
    "menu.servers": "🖥 Серверы",
    "menu.balance": "💰 Деньги",
    "menu.integrations": "🔌 Хостеры",
    "menu.order": "🛒 Заказать",
    "menu.journal": "📜 Журнал",
    "menu.settings": "⚙️ Настройки",
    "onboarding.language": "Выберите язык.",
    "onboarding.timezone": (
        "Часовой пояс для дат и тихих часов. Предлагаю {suggested}, можно выбрать другой."
    ),
    "onboarding.currency": "В какой валюте считать итоги по всем хостерам?",
    "onboarding.done": "✅ Готово. Подключите первого хостера.",
    "error.auth_expired": "ключ или вход больше не действует",
    "error.rate_limited": "хостер просит обращаться реже",
    "error.blocked": "хостер заблокировал запрос",
    "error.layout_changed": "хостер изменил ответ, плагин устарел",
    "error.browser_busy": "браузер занят входом, попробуйте через несколько минут",
    "error.hoster_error": "хостер отказал",
    "error.plugin_error": "ошибка плагина",
    "error.timeout": "хостер не ответил вовремя",
    "error.unexpected": "непредвиденная ошибка",
    "account.failed": "нет связи ({reason}), данные от {date}",
    "account.failed_never": "нет связи ({reason})",
    "account.status.ok": "подключён",
    "account.status.relogin": "нужен вход",
    "account.status.error": "ошибка",
    "account.status.disabled": "отключён",
    "account.card.last_ok": "🕓 Опрос: {date}",
    "account.never": "🕓 Опросов ещё не было",
    "account.relogin": "🔑 Войти заново",
    "account.change_key": "🗝 Сменить ключ",
    "account.disable": "⏸ Отключить",
    "account.enable": "▶️ Включить",
    "account.delete": "🗑 Удалить",
    "account.delete_confirm": (
        "<b>🗑 Удалить {title}?</b>\nСерверы и история в боте удалятся.\nУ хостера ничего "
        "не изменится."
    ),
    "account.gone": "Такого аккаунта уже нет.",
    "status.running": "работает",
    "status.stopped": "остановлен",
    "status.suspended": "приостановлен",
    "status.unpaid": "не оплачен",
    "status.unknown": "неизвестно",
    "autorenew.on": "вкл",
    "autorenew.off": "выкл",
    "autorenew.na": "у хостера нет",
    "summary.title": "<b>📰 Сводка · {date}</b>",
    "servers.empty": "Хостеры не подключены",
    "servers.gone": "Этого сервера больше нет в данных хостера.",
    "servers.kind.vps": "VPS",
    "servers.kind.dedicated": "выделенный",
    "price.hourly": "{amount}/ч · {month}/мес",
    "price.month": "{amount}/мес",
    "price.months": "{amount} за {months} мес.",
    "card.ip": "IP <code>{ip}</code>",
    "card.price": "💳 {price}",
    "card.next_charge": "списание {date} ({left})",
    "card.ends": "оплачен до {date} ({left})",
    "card.autorenew": "🔁 Автопродление: {value}",
    "card.usage_unsupported": "Нагрузку хостер не отдаёт",
    "card.usage_none": "Нагрузки пока нет",
    "card.disk": "Диск",
    "card.traffic": "трафик {gb} ГБ",
    "card.data_at": "🕓 Данные от {date}",
    "action.start": "▶️ Включить",
    "action.stop": "⏻ Выключить",
    "action.restart": "🔄 Перезагрузить",
    "action.autorenew_on": "🔁 Вкл. продление",
    "action.autorenew_off": "🔁 Выкл. продление",
    "action.renew": "📅 Продлить",
    "action.autopay": "💳 Автооплата",
    "action.order_same": "🛒 Ещё такой",
    "refresh.started": "🔃 Обновляю…",
    "refresh.too_often": "Обновлять можно раз в минуту на аккаунт. Попробуйте чуть позже.",
    "balance.title": "<b>Баланс</b>",
    "balance.line": "<b>{title}</b>: {amount} (на {date})",
    "balance.hours_left": "Хостер считает: хватит на {hours} ч.",
    "balance.topup": "💰 Пополнить {title}",
    "topup.title": "<b>💰 Пополнение · {title}</b>",
    "topup.balance": "Баланс: {amount}",
    "topup.choose_coin": "Какая монета?",
    "topup.choose_coin_network": "Какая сеть для {coin}?",
    "topup.choose_after": "Выберите монету — я возьму адрес со страницы оплаты.",
    "topup.getting_address": "⏳ Беру адрес со страницы оплаты, это до минуты…",
    "topup.address_failed": (
        "🔴 Адрес не получен: {reason}.\nВыберите другую монету или оплатите на странице."
    ),
    "topup.options_failed": "🔴 Не прочитал монеты: {reason}.\nВыберите на странице оплаты.",
    "topup.sbp": "⚡ СБП · {name}",
    "topup.crypto": "🪙 Криптовалюта",
    "topup.crypto_one": "🪙 {name}",
    "topup.method": "Способ: {name}",
    "topup.minimum": "минимум {amount}",
    "topup.choose_amount": "Какую сумму?",
    "topup.amount_minimum": "{amount} · минимум",
    "topup.amount_until": "{amount} · до конца след. мес.",
    "topup.amount_custom": "✏️ Своя сумма",
    "topup.ask_amount": "✏️ Напишите сумму в {currency}, например 500.",
    "topup.below_minimum": "⚠️ Это меньше минимума ({amount}).",
    "topup.creating": "⏳ Создаю счёт у хостера…",
    "topup.invoice_title": "<b>💰 Счёт · {title}</b>",
    "topup.invoice_line": "{amount} · {method} · №{id}",
    "topup.pay_sbp": "⏳ Ссылка живёт около 10 минут. QR — для оплаты с другого устройства.",
    "topup.pay_page": "Способ можно выбрать на странице оплаты.",
    "topup.send_exactly": "Переведите ровно {amount} на адрес:",
    "topup.expires": "⏳ Адрес действует до {date}.",
    "topup.press_paid": "Оплатите и нажмите «✅ Оплатил».",
    "topup.open": "💳 Оплатить",
    "topup.paid": "✅ Оплатил",
    "topup.page": "🌐 На сайте хостера",
    "topup.need_login": (
        "🔴 Для счёта в {title} нужен вход в кабинет.\nВойдите заново или пополните на "
        "сайте хостера."
    ),
    "topup.failed": "🔴 Счёт не создался: {reason}.\nМожно пополнить на сайте хостера.",
    "topup.will_check": "🔃 Проверю баланс через 2, 5 и 10 минут.",
    "topup.already_checking": "Уже проверяю баланс по этому счёту.",
    "topup.arrived": "<b>🟢 Пополнение дошло · {title}</b>\nБаланс: {amount}",
    "topup.not_seen": (
        "<b>🟡 {title}: пополнения пока не видно</b>\nПрошло {minutes} мин. Если деньги "
        "списались, они придут позже: баланс я читаю по расписанию."
    ),
    "integrations.ask_label": (
        "<b>🏷 Подпись для {hoster}</b>\nНапример «личный»: нужна, если аккаунтов "
        "несколько.\nНапишите её или нажмите кнопку."
    ),
    "integrations.label_default": "Без подписи ({name})",
    "integrations.label_taken": "⚠️ Подпись «{label}» уже занята. Напишите другую.",
    "integrations.ask_key": (
        "<b>🗝 {field} от {hoster}</b>\nПришлите одним сообщением.\nЯ сразу удалю его из "
        "чата и сохраню зашифрованным."
    ),
    "integrations.checking": "🔃 Ключ сохранён, проверяю доступ…",
    "journal.title": "📜 Журнал",
    "journal.empty": "Пока пусто: здесь будут действия с серверами и деньгами.",
    "journal.line": "{mark} {date} · {who}{hoster} · {action}",
    "settings.title": "Настройки",
    "settings.button.language": "🌐 Язык",
    "settings.button.timezone": "🕓 Часовой пояс",
    "settings.button.currency": "💱 Валюта",
    "settings.timezone_other": "Другой…",
    "settings.timezone_ask": "Напишите название часового пояса, например Asia/Omsk.",
    "settings.timezone_bad": "⚠️ Не знаю такого пояса. Пример: Europe/Moscow.",
    "threshold.button.balance_days": "🔔 Порог денег",
    "threshold.button.load_percent": "📈 CPU и RAM",
    "threshold.button.disk_percent": "💾 Диск",
    "threshold.button.quiet_hours": "🌙 Тихие часы",
    "threshold.button.summary_time": "📰 Время сводки",
    "threshold.ask.balance_days": "Сейчас {value}. За сколько дней предупреждать? От 1 до 60.",
    "threshold.ask.load_percent": "Сейчас {value}%. Порог CPU и RAM в процентах, от 1 до 100.",
    "threshold.ask.disk_percent": "Сейчас {value}%. Порог диска в процентах, от 1 до 100.",
    "threshold.ask.quiet_hours": (
        "Сейчас {value}. Напишите тихие часы так: 23:00-08:00.\nОбычные уведомления в эти "
        "часы ждут их конца, критические приходят сразу."
    ),
    "threshold.ask.summary_time": "Сейчас {value}. Во сколько присылать сводку? Например, 09:00.",
    "threshold.bad": "⚠️ Не понял значение. Попробуйте ещё раз.",
    # Alerts, backups, releases
    "alert.balance_low": "💰 <b>{title}: деньги до {date}</b>\nНа балансе {amount}.",
    "alert.balance_low_own": (
        "Здесь работает hostwatch, поэтому предупреждаю на {days} дн. раньше."
    ),
    "alert.server_status": "🔴 <b>{title} · {server}: {status}</b>",
    "alert.ends_soon": ("⚠️ <b>{title} · {server}: закончится {date}</b>\nАвтопродление выключено."),
    "alert.ends_soon_short": (
        "⚠️ <b>{title} · {server}: закончится {date}</b>\nНа продление не хватит денег."
    ),
    "alert.load": ("⚠️ <b>{title} · {server}: {metric} выше {percent}%</b>\n{polls} опроса подряд."),
    "alert.disk": "⚠️ <b>{title} · {server}: диск {percent}%</b>",
    "alert.relogin": "🔴 <b>{title}: нужен вход</b>",
    "alert.plugin_broken": "🔴 <b>{title}: нет данных</b>\n{reason}.",
    "alert.reminder": "⏰ Напоминание\n{text}",
    "alert.resolved": "🟢 Решено: {subject}.",
    "alert.subject.balance_low": "баланс {title}",
    "alert.subject.server": "{title}, сервер {server}",
    "alert.subject.ends_soon": "{title}, окончание {server}",
    "alert.subject.load": "{title}, {metric} на {server}",
    "alert.subject.disk": "{title}, диск на {server}",
    "alert.subject.relogin": "{title}, вход",
    "alert.subject.plugin_broken": "{title}, получение данных",
    "release.new": "Вышла версия hostwatch {version}. Что нового: {url}",
    "settings.button.backup": "📦 Бэкап",
    "backup.caption": (
        "Бэкап базы на {date}. Секреты в нём зашифрованы, ключа HOSTWATCH_KEY в нём нет."
    ),
    "backup.failed": "Не получилось сделать бэкап. Подробности в логах.",
    "card.own": "ℹ️ Это сервер hostwatch: о деньгах предупрежу на {days} дн. раньше",
    "servers.own": "hostwatch",
    # Login page and browser login
    "web.title": "Вход: {hoster}",
    "web.gone": (
        "Ссылка недействительна: её уже открыли, она устарела или вход закончен. Попросите в "
        "боте новую."
    ),
    "web.placeholder": "Текст для поля на сайте",
    "web.send": "Ввести",
    "web.back": "Назад",
    "web.cancel": "Отмена",
    "web.done": "Готово",
    "web.loading": "Открываю сайт…",
    "web.checking": "Проверяю вход…",
    "web.reconnecting": "Связь прервалась, подключаюсь снова…",
    "web.blocked": "Переход на {host} заблокирован",
    "web.load_failed": "Сайт не открылся. Нажмите «Назад» или «Отмена».",
    "web.notdone_missing": "Вход ещё не завершён: войдите на сайте и нажмите «Готово».",
    "web.notdone_check": "Сайт не подтвердил вход. Войдите и нажмите «Готово» ещё раз.",
    "web.saved": "{hoster} подключён. Страницу можно закрыть.",
    "web.fail_browser": "Браузер не запустился. Попробуйте позже, подробности в логах бота.",
    "web.end_cancelled": "Вход отменён. Страницу можно закрыть.",
    "web.end_idle": "Вход закрыт: 15 минут без действий. Попросите в боте новую ссылку.",
    "web.end_rejected": "Вход закрыт из бота кнопкой «Это не я».",
    "web.end_replaced": "Этот вход закрыт: в боте попросили новую ссылку.",
    "web.end_failed": "Вход прервался из-за ошибки. Попросите в боте новую ссылку.",
    "login.link": (
        "<b>🔑 Вход в {hoster}</b>\nОткройте ссылку на телефоне или "
        "компьютере:\n{url}\nВойдите на сайт как обычно и нажмите «Готово».\n⏳ Ссылка "
        "одноразовая, живёт 10 минут."
    ),
    "login.open": "🔑 Открыть вход",
    "login.no_address": (
        "Не знаю адрес этого сервера. Задайте HOSTWATCH_IP или HOSTWATCH_DOMAIN в .env и "
        "перезапустите бота."
    ),
    "login.opened": "🔑 Ссылку входа в {hoster} открыли: {ip}, {browser}.",
    "login.not_me": "🚫 Это не я",
    "login.killed": "Вход закрыт.",
    "login.connected": "🟢 {hoster} подключён.",
    "login.connected_unchecked": (
        "🟡 {hoster}: сессия сохранена, но проверить её без браузера не удалось ({reason})."
    ),
    "login.ended.cancelled": "Вход в {hoster} отменён.",
    "login.ended.idle": "Вход в {hoster} закрыт: 15 минут без действий.",
    "login.ended.rejected": "Вход в {hoster} закрыт кнопкой «Это не я».",
    "login.ended.replaced": "Прошлый вход в {hoster} закрыт: выдана новая ссылка.",
    "login.ended.failed": "Вход в {hoster} прервался: браузер не запустился. Подробности в логах.",
    "cookie.usage": (
        "Запасной путь входа: вставить cookie руками.\n<code>/cookie подпись</code> или "
        "<code>/cookie id</code> — для подключённого аккаунта, <code>/cookie {plugins}</code> "
        "— новый аккаунт.\nАккаунты со входом через браузер: {accounts}"
    ),
    "cookie.unknown": "Не нашёл аккаунт или хостера «{target}». Список: /cookie",
    "cookie.ask": (
        "Пришлите cookie <b>{hoster}</b> одним сообщением: строку заголовка "
        "<code>имя=значение; имя2=значение2</code> или JSON из расширения. Нужны: {names}.\n"
        "Сообщение я сразу удалю. Учтите: сессия, созданная на другом IP, может не работать."
    ),
    "cookie.missing": "Не хватает cookie: {names}. Пришлите ещё раз или /cookie для отмены.",
    "cookie.bad": "Не разобрал cookie. Нужна строка <code>имя=значение; …</code> или JSON.",
    "control.confirm_title": "<b>❓ {what}?</b>",
    "control.where": "🖥 {where}",
    "control.how_much": "💰 {amount}",
    "control.yes": "✅ Да",
    "control.charge": "Спишется {amount} с баланса",
    "control.charge_with_balance": "Спишется {amount} · на балансе {balance}",
    "control.later_charges": "Сейчас без списания, дальше {price} за каждое продление",
    "control.no_charge": "Без списания",
    "control.what.start": "включить сервер",
    "control.what.stop": "выключить сервер",
    "control.what.restart": "перезагрузить сервер",
    "control.what.autorenew_on": "включить автопродление",
    "control.what.autorenew_off": "выключить автопродление",
    "control.what.renew": "продлить на один период",
    "control.what.pay_invoice": "оплатить счёт #{id} с баланса",
    "control.need_login": "🔴 Сессия {title} закончилась. Войдите заново и повторите.",
    "control.cannot": "🔴 Не получилось: {reason}.",
    "control.unavailable": "Это действие сейчас недоступно. Показываю свежую карточку.",
    "control.asking_price": "Узнаю цену у хостера…",
    "control.expired": "Кнопка устарела или уже нажата. Ничего не сделал.",
    "control.cancelled": "Отменено. Ничего не сделал.",
    "control.running": "Выполняю…",
    "control.running_what": "⏳ Выполняю: {what}…",
    "control.timeout": (
        "<b>🟡 Результат неизвестен</b>\n{what} · {where}\nХостер не ответил вовремя. "
        "Проверьте в кабинете и нажмите «🔃 Обновить»."
    ),
    "control.failed": "<b>🔴 Не получилось</b>\n{what} · {where}\nПричина: {reason}.",
    "control.done": "<b>✅ Готово</b>\n{what} · {where}\nОтвет хостера: {detail}",
    "control.unclear": (
        "<b>🟡 Хостер не подтвердил</b>\n{what} · {where}\nОтвет: {detail}\nПроверьте в кабинете."
    ),
    "control.money_notice": "💸 Действие с деньгами\n{text}",
    "autopay.button.on": "💳 Вкл. автооплату",
    "autopay.button.off": "💳 Выкл. автооплату",
    "autopay.button.pay": "✅ Оплатить",
    "autopay.button.skip": "✖️ Не платить",
    "autopay.button.topup": "💰 Пополнить",
    "autopay.button.invoices": "🧾 Счета",
    "autopay.card.on": "💳 Автооплата бота: вкл",
    "autopay.card.off": "💳 Автооплата бота: выкл",
    "autopay.card.native_hint": "💡 У хостера есть автопродление — включите его кнопкой ниже.",
    "autopay.settings.button": "💳 Автооплата",
    "autopay.settings.title": "<b>💳 Автооплата</b>",
    "autopay.settings.about": (
        "Бот оплачивает с баланса счёт на продление незадолго до срока.\nТолько для "
        "отмеченных серверов и хостеров без своего автопродления.\nЕсли сумма выросла "
        "больше чем на 10 %, бот спросит."
    ),
    "autopay.settings.off": "Автооплата недоступна в этом запуске.",
    "autopay.settings.days": "⏰ Платить за {days} дн. до срока",
    "autopay.settings.no_accounts": "Нет хостеров, у которых бот умеет оплачивать счета.",
    "autopay.settings.account": (
        "{title}: лимит {limit}/мес · потрачено {spent} · серверов {count}"
    ),
    "autopay.settings.zero_limit": "  ⚠️ Лимит 0: ничего не оплачу, пока не зададите.",
    "autopay.settings.button.days": "⏰ Срок: {days} дн.",
    "autopay.settings.button.limit": "💰 Лимит {title}",
    "autopay.ask_days": "За сколько дней до срока платить? Сейчас {days}. Число от 1 до {max}.",
    "autopay.ask_limit": (
        "Месячный лимит автооплаты для {title}. Сейчас {limit}. Напишите сумму в валюте"
        " хостера, например 15 или 15.50. 0 выключает оплату."
    ),
    "autopay.bad_days": "Нужно целое число от 1 до {max}.",
    "autopay.bad_limit": "Нужна сумма от 0 до 1 000 000 с точностью до копеек, например 15.50.",
    "autopay.marked_off": "Автооплата для {server} выключена.",
    "autopay.marked_on": (
        "Автооплата для {server} включена: счёт на продление оплачу за {days} дн. до срока,"
        " не больше {limit} в месяц."
    ),
    "autopay.marked_on_no_limit": (
        "Автооплата для {server} включена, но месячный лимит 0: ничего не оплачу, пока вы"
        " не зададите его в «Настройки → Автооплата»."
    ),
    "autopay.report.paid": (
        "<b>✅ Автооплата: счёт #{id}</b>\n{server} · {title} · {amount}\nОтвет хостера: {detail}"
    ),
    "autopay.report.limit": "Лимит месяца: {spent} из {limit}.",
    "autopay.report.unclear": (
        "<b>🟡 Автооплата: счёт #{id} не подтверждён</b>\n{server} · {title} · "
        "{amount}\nОтвет: {detail}\nПроверьте в кабинете. Бот этот счёт больше не тронет."
    ),
    "autopay.report.failed": (
        "<b>🔴 Автооплата: счёт #{id} не оплачен</b>\n{server} · {title} · "
        "{amount}\nПричина: {reason}.\nБот этот счёт больше не тронет: оплатите вручную."
    ),
    "autopay.report.grown": (
        "<b>⚠️ Счёт #{id} вырос</b>\n{server} · {title}: {amount} вместо "
        "{reference}\nАвтооплата не платила. Оплатить?"
    ),
    "autopay.report.new": (
        "<b>⚠️ Счёт #{id}: не с чем сравнить</b>\n{server} · {title}: {amount}\nАвтооплата "
        "не платила. Оплатить?"
    ),
    "autopay.report.over_limit": (
        "<b>🟡 Счёт #{id} ждёт: лимит</b>\n{server} · {title}: {amount}\nПотрачено {spent} "
        "из {limit} за месяц."
    ),
    "autopay.report.low_balance": (
        "<b>🟡 Счёт #{id} ждёт: мало денег</b>\n{server} · {title}: {amount}\nНа балансе "
        "{balance}, не хватает {missing}."
    ),
    "autopay.declined": "Хорошо, счёт #{id} бот оплачивать не будет.",
    "autopay.changed": "Сумма счёта снова изменилась, теперь {amount}. Ничего не оплачено.",
    "invoices.title": "<b>🧾 Счета · {title} · {count}</b>",
    "invoices.none": "🟢 Неоплаченных счетов нет.",
    "invoices.line": "#{id} · {amount}",
    "invoices.due": "до {due}",
    "invoices.pay": "💳 #{id} · {amount}",
    "invoices.not_service": "Счета «не продление» оплатите в кабинете.",
    "invoices.loading": "Читаю счета у хостера…",
    "invoices.not_payable": (
        "🟡 Этот счёт уже нельзя оплатить из бота: он оплачен, отменён или изменился."
    ),
    "balance.invoices": "🧾 Счета {title}",
    "journal.action.topup": "счёт на пополнение",
    "journal.action.login": "вход",
    "journal.action.start": "включение",
    "journal.action.stop": "выключение",
    "journal.action.restart": "перезагрузка",
    "journal.action.autorenew_on": "автопродление вкл.",
    "journal.action.autorenew_off": "автопродление выкл.",
    "journal.action.renew": "продление",
    "journal.action.pay_invoice": "оплата счёта",
    # Ordering servers (stage 13)
    "order.title_any": "<b>🛒 Заказ сервера</b>",
    "order.title": "<b>🛒 Заказ · {title}</b>",
    "order.no_accounts": "Нет подключённых хостеров, у которых бот умеет заказывать серверы.",
    "order.choose_account": "Выберите хостера.",
    "order.choose_location": "Выберите локацию.",
    "order.location_button": "{name} · от {amount}/мес",
    "order.empty": "Хостер сейчас ничего не продаёт.",
    "order.location": "📍 {name}",
    "order.choose_plan": "Выберите тариф.",
    "order.plan_line": "• <b>{name}</b> — {specs}, от {amount}/мес",
    "order.plan_button": "{name} · {amount}/мес",
    "order.more_plans": "И ещё тарифов: {count}. Показаны самые дешёвые.",
    "order.plan": "📦 {name} · {specs}",
    "order.choose_os": "Выберите ОС.",
    "order.no_os": "Для этого тарифа нет ОС, которую бот умеет заказать.",
    "order.os": "💿 {name}",
    "order.choose_period": "Выберите срок.",
    "order.period_hourly": "Почасово · ≈{amount}/мес",
    "order.period_months": "{months} мес. · {amount}",
    "order.period": "📅 {period}",
    "order.hourly": "почасово",
    "order.months": "{months} мес.",
    "order.price": "💳 {price}",
    "order.price_hourly": "≈{amount}/мес, почасово ({hour}/ч)",
    "order.price_changed": "⚠️ Цена изменилась: в каталоге было {old}, хостер просит {new}.",
    "order.balance": "💰 Баланс: {amount}",
    "order.key_set": "🔑 SSH-ключ из настроек ({name})",
    "order.key_missing": "🔑 Ключа нет: вход по паролю (кнопка «🔑 Пароль» на карточке)",
    "order.key_not_supported": "🔑 Хостер не ставит ключ: вход по паролю",
    "order.limit_ok": "Лимит заказов: {spent} из {limit} за месяц",
    "order.limit_over": "⚠️ Выше лимита: {spent} из {limit} за месяц — спрошу ещё раз",
    "order.untested": "⚠️ Заказ здесь не проверен вживую: после заказа проверьте кабинет.",
    "order.ask": "❓ Заказать за {price}?",
    "order.second_title": "<b>⚠️ Заказ выше месячного лимита</b>",
    "order.what": "Что: {what}",
    "order.second_ask": "Точно заказать? Это второе подтверждение.",
    "order.second_yes": "✅ Да, заказать",
    "order.shortfall": (
        "На балансе не хватает денег: нужно {need}, на балансе {balance}, не хватает {missing}."
    ),
    "order.topup_button": "Пополнить на {amount} · {name}",
    "order.topup_hint": "Пополните баланс и нажмите «Проверить ещё раз».",
    "order.topup_none": "Пополните баланс в кабинете хостера и нажмите «Проверить ещё раз».",
    "order.check_again": "🔄 Проверить ещё раз",
    "order.loading": "Читаю каталог у хостера…",
    "order.asking_price": "Узнаю цену и баланс у хостера…",
    "order.cancelled": "Заказ отменён. Ничего не заказал.",
    "order.running": "Заказываю…",
    "order.timeout": (
        "Хостер не ответил вовремя: {what}. Заказ мог пройти. Проверьте кабинет"
        " и не заказывайте повторно, пока не убедитесь."
    ),
    "order.price_moved": (
        "Не заказал: {what}. Цена изменилась перед заказом: было {old}, стало {new}."
        " Начните заказ заново."
    ),
    "order.failed": "Не заказал: {what}. Причина: {reason}.",
    "order.unclear": (
        "Хостер ответил непонятно: {what}. Проверьте кабинет.\nОтвет хостера: {detail}"
    ),
    "order.invoice": (
        "Хостер выставил счёт #{invoice} вместо оплаты с баланса: {what}."
        " Сервер появится после оплаты.\nОтвет хостера: {detail}"
    ),
    "order.done": (
        "Заказано: {what}, {price}. Сервер появится в списке после ближайшего опроса."
        "\nОтвет хостера: {detail}"
    ),
    "order.password": (
        "🔑 Root-пароль {server}: <code>{password}</code>\n"
        "Сообщение удалится через {minutes} мин. Бот пароль не хранит."
    ),
    "order.password_asking": "Запрашиваю пароль у хостера…",
    "order.button.password": "🔑 Пароль",
    "order.journal.order": "заказ сервера",
    "order.journal.root_password": "показ пароля",
    "order.settings.button": "🛒 Заказ",
    "order.settings.title": "<b>🛒 Заказ серверов</b>",
    "order.settings.limit": "💰 Лимит в месяц: {amount}",
    "order.settings.limit_note": (
        "Выше лимита — двойное подтверждение. При лимите 0 — каждый заказ."
    ),
    "order.settings.key": "🔑 SSH-ключ: {name}",
    "order.settings.no_key": "не задан",
    "order.settings.button.limit": "💰 Лимит",
    "order.settings.button.key": "🔑 Задать ключ",
    "order.settings.button.no_key": "🗑 Удалить ключ",
    "order.settings.ask_limit": (
        "Напишите лимит заказов в месяц в {currency}, например 1000."
        " 0 — подтверждать дважды каждый заказ."
    ),
    "order.settings.bad_limit": "Не понял сумму. Напишите число 0 или больше, например 1000.",
    "order.settings.ask_key": (
        "Пришлите публичный SSH-ключ одной строкой, например содержимое ~/.ssh/id_ed25519.pub."
    ),
    "order.settings.bad_key": (
        "Это не похоже на публичный ключ. Нужна строка вида «ssh-ed25519 AAAA… комментарий»."
    ),
    "order.settings.private_key": (
        "Это приватный ключ: удалил сообщение. Пришлите публичный, файл с окончанием .pub."
    ),
    # One menu in the chat (1.1)
    "menu.button": "☰ Меню",
    "menu.closed": "Меню закрыто: актуальное ниже.",
    "menu.stale": "Это меню закрыто, актуальное внизу",
    "bot.login.expired": ("<b>🔴 Ссылка входа в {hoster} истекла</b>\nЕё не открыли за 10 минут."),
    "topup.expired": "<b>🔴 Срок счёта истёк</b>\nЕсли не успели оплатить, выставите новый.",
    "topup.again": "💰 Новый счёт",
    "menu.problems": "⚠️ Проблемы ({count})",
    "menu.connect": "＋ Подключить хостера",
    "time.today": "сегодня {time}",
    "time.tomorrow": "завтра {time}",
    "time.today_word": "сегодня",
    "time.tomorrow_word": "завтра",
    "time.in_days": "через {n} дн.",
    "time.days": "{n} дн.",
    "plural.server": "сервер|сервера|серверов",
    "plural.hoster": "хостер|хостера|хостеров",
    "plural.problem": "проблема|проблемы|проблем",
    "home.ok": "Всё в порядке",
    "home.empty": "Хостеры не подключены",
    "home.empty_hint": "Подключите первого — кнопка ниже.",
    "home.more": "…и ещё {count}",
    "home.balances": "💰 На балансах: {amount}",
    "home.month": "💳 В месяц: {amount}",
    "problems.none": "Проблем нет",
    "problem.relogin": "нужен вход",
    "problem.no_data": "нет данных: {reason}",
    "problem.money_until": "деньги до {date}",
    "problem.shortfall": "{date} не хватит {amount} на {server}",
    "problem.ends": "закончится {date}, не продлевается",
    "problem.button.account": "🔌 {title}",
    "problem.button.topup": "💰 Пополнить {title}",
    "problem.button.server": "🖥 {server}",
    "summary.open": "🏠 Открыть меню",
    "summary.problems": "⚠️ Проблемы",
    "servers.none": "Серверов нет.",
    "servers.all_ok": "всё в порядке",
    "servers.pick": "Выберите хостера.",
    "servers.count.vps": "VPS: {n}",
    "servers.count.dedicated": "выделенных: {n}",
    "card.no_ip": "IP —",
    "money.title": "Деньги",
    "money.ok": "всё в порядке",
    "money.attention": "нужно внимание",
    "money.no_spending": "не тратится",
    "money.beyond_horizon": "больше года",
    "money.until": "до {date}",
    "money.no_data": "нет данных",
    "money.data_at": "данные от {date}",
    "money.shortfall": "{title}: {date} не хватит {amount} на {server}",
    "money.rate_note": "≈ по курсу ЦБ РФ на {date}",
    "money.button.info": "ℹ️ Как считаю",
    "money.info_title": "Как считаю прогноз",
    "money.read_at": "{amount} на {date}",
    "money.info_rates": "Итоги «≈» — в валюте из настроек по курсу ЦБ РФ.",
    "topup.step": "Шаг {n} из {total} · {what}",
    "topup.step.method": "способ",
    "topup.step.amount": "сумма",
    "topup.step.pay": "оплата",
    "topup.no_methods": "Из бота пополнить нельзя — только на сайте хостера.",
    "topup.sbp_plain": "⚡ СБП",
    "topup.invoice_short": "{amount} · №{id}",
    "integrations.none": "Хостеры не подключены",
    "integrations.all_ok": "все на связи",
    "integrations.can_add": "＋ Можно подключить: {names}",
    "journal.page": "стр. {page}",
    "settings.general": "🌐 {language} · {tz} · {currency}",
    "settings.alerts": "🔔 Деньги за {days} дн. · CPU/RAM {load}% · диск {disk}%",
    "settings.times": "🌙 Тихие часы {quiet} · 📰 сводка в {summary}",
    "invoices.other": "не продление",
    "topup.lasts": "💰 Хватит: {lasts}",
}
