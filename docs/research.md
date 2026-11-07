# hostwatch: как устроены личные кабинеты хостеров

Разбор сделан 7 ноября 2026 года вручную: смотрел сетевые запросы в браузере после входа и код страниц. На каждом сайте создал по тестовому счёту на пополнение и ни одного не оплатил. Все запросы ниже проверены, если рядом не написано другое.

Сайты меняются без предупреждения. Перед тем как полагаться на какой-то запрос, прогоните `check <хостер>`.

## Сводка

| | Timeweb Cloud | Aeza | Regxa | AdminVPS |
|---|---|---|---|---|
| Чем авторизуемся | API-токен + сессия панели для счетов | API-ключ `X-API-Key` | сессия сайта (cookie) | сессия сайта (cookie) |
| Защита от ботов | QRATOR, обычные запросы пропускает | нет | нет | DDoS-Guard, с сервера не проверяли |
| Серверы и статусы | официальный API | API | JSON | HTML |
| Нагрузка | API | API: CPU, RAM, сеть | JSON: CPU, RAM, диск, трафик | только трафик; CPU и RAM картинками |
| Баланс | API, и сразу «хватит на N часов» | API, в центах | JSON | HTML, шапка страницы |
| СБП | да, через ЮKassa | да, через ЮKassa | нет | да, через PayMaster |
| Крипта | нет | да, адрес отдаёт сам API | да, через Cryptomus | нет |
| Автопродление | не нужно, оплата почасовая | да | нет | да |
| Питание | API | да | да | да |
| Минимальное пополнение | больше 100 ₽ (500 ₽ проходит) | €0.50 СБП, €5 крипта | $5 | 100 ₽ |

## Timeweb Cloud

Чтение и управление серверами идут через официальный API (`https://api.timeweb.cloud/api/v1/...`). Токен выпускается в панели, раздел «API и Терраформ». Что полезно в API:

- `GET /api/v1/account/finances` — `balance`, `hourly_cost`, `monthly_cost`, `hours_left`. Прогноз считать не нужно, Timeweb отдаёт его сам.
- `GET /api/v1/servers`, `/api/v1/dedicated-servers` — серверы и дедики.

**Счёт на пополнение через API создать нельзя.** Его создаёт только панель, своим внутренним запросом. Для этого нужна сессия панели:

- Cookie `refresh_token` живёт 30 дней. Cookie `device_token` живёт год.
- `POST https://timeweb.cloud/api/v1/auth/update-token` с этими cookie отдаёт `tokens.access_token` на 15 минут и новый `refresh_token`. **Старый refresh-токен после этого недействителен.** Новый нужно сохранять каждый раз, иначе сессия пропадёт. По той же причине бот и браузер человека не могут делить одну сессию.
- Плательщик: `GET /portal/v1/accounts/<логин аккаунта>/payers?type=person`, нужен `id`.
- Счёт: `POST /portal/v4/invoices`

```json
{
  "total_amount": 500,
  "payer": {"id": 1234567},
  "payment_method": {
    "type": "sbp",
    "params": {"bind": false, "return_url": "...", "fail_url": "..."}
  },
  "items": []
}
```

  В ответе `payment_info.confirmation.confirmation_url` — ссылка на страницу ЮKassa. Как из неё получить ссылку СБП, описано ниже в разделе «ЮKassa». Если сумма меньше минимальной, приходит `amount_less_minimal`; 100 ₽ сайт уже не принимает.

QRATOR запросы из Python пропустил. С IP дата-центра это ещё не проверялось.

## Aeza

Сайт работает на том же API, который доступен по ключу: `https://my.aeza.net/api/v2/`. Ключ создаётся в «Настройки → API-ключи» и передаётся в заголовке `X-API-Key`. К ключу можно привязать белый список IP. Сайт сам использует `Authorization: Bearer <сессия>`, сессия живёт 30 дней.

- `GET /accounts/me` — `balance` в центах валюты аккаунта (6323 = €63.23).
- `GET /services?offset=0&limit=50` — услуги. У каждой есть `ip`, `status`, `expiresAt`, `autoProlong`, `price` (в центах за `paymentTerm`), `product.prices` по всем периодам и `capabilities`. По `capabilities` видно, что можно делать с услугой: `ctl.restart`, `ctl.force_off`, `charts`, `remote.vnc` и так далее. Отсюда бот решает, какие кнопки показывать.
- `GET /billing/transactions?offset=0&limit=N` — история списаний, из неё считается реальный расход.
- Нагрузка: `GET /services/{id}/stats/{cpu_usage|memory_usage|net_read_mbits|net_write_mbits}?resolution=300&fromDate=...&toDate=...` → `{"data": [[unix, значение], ...]}`.
- Автопродление: `PATCH /services/{id}` `{"autoProlong": true}`.
- Продление: `GET services/{id}/prices`, затем `POST services/{id}/prolong`.
- Питание: `POST /services/{id}/ctl/restart`, `/ctl/suspend` `{"force": false}`, `/ctl/resume`.
- Способы оплаты: `GET /billing/payment-methods`. Нам нужны `yookassa:sbp` и `pawnow:*` (крипта: `usdt_trc20`, `usdt_ton`, `usdt_bsc`, `btc_btc` и другие).
- Счёт: `POST /billing/invoices` `{"method": "...", "amount": <центы>}`.
  - Для СБП в `payload.url` приходит ссылка ЮKassa.
  - Для крипты в `payload` сразу приходят `address`, `amount`, `currency` и строка для QR вида `адрес?amount=6.51`. Сумма в крипте больше суммы в евро на комиссию.

Управление (автопродление, продление, питание) я нашёл в коде страниц, но не вызывал.

## Regxa

Сайт на Next.js, внутренний API лежит на `https://regxa.com/api/...`. Официального API нет. Авторизация — cookie сессии NextAuth. `GET /api/auth/session` показывает срок её действия, около суток. Судя по всему, срок продлевается при каждом запросе.

- `GET /api/profile/client/{clientId}` → `data.credit` (баланс в USD) и `data.stats` (неоплаченные счета).
- `GET /api/services/getservices?clientId={id}` — услуги: `status`, `nextDueDate`, `amount`, `billingCycle`. ID виртуалки лежит в `customfields`, в поле `vpsid`.
- `GET /api/services/vps/status/{vpsid}` — нагрузка (`used_cpu`, `used_ram`, `used_disk`, `used_bandwidth`) и `status`. Под капотом Virtualizor.
- `GET /api/services/vps/details/{vpsid}` — IP, ресурсы, флаг `suspended`.
- Питание: `POST /api/services/vps/power/{start|stop|restart|poweroff}/{vpsid}`.
- Счета: `GET /api/billing/invoices`. Оплатить счёт с баланса: `POST /api/billing/invoices/apply-credit` `{"invoiceid", "amount"}`.
- Автопродления как функции нет. Сайт сам выставляет счёт перед окончанием срока.
- Пополнение:
  1. `POST /api/billing/addfunds` `{"amount": 5}` → `data.invoiceid`.
  2. `POST /api/billing/cryptomus/create-payment` `{"invoiceId", "returnUrl"}` → ссылка `https://pay.cryptomus.com/pay/<uuid>`.

**Адрес кошелька Cryptomus без браузера не получить.** Страница выбирает сеть запросом `POST https://api-pay.cryptomus.com/v1/payment/method`, а в нём зашифрованный «отпечаток браузера». Поэтому адрес достаёт только браузер без окна на сервере. Запасной вариант — отправить человеку ссылку.

## AdminVPS

Личный кабинет на WHMCS, перед ним защита DDoS-Guard. Данные отдаёт готовым HTML. Авторизация — cookie сессии WHMCS, сколько она живёт, пока неизвестно.

- Баланс — в шапке любой страницы, ссылка на `/payments.php`.
- Услуги — таблица на `clientarea.php?action=services`: название, цена и период, дата следующего платежа, статус, ID услуги.
- Карточка услуги: `clientarea.php?action=productdetails&id={id}`.
  - IP, статус и трафик: `GET modules/servers/solusvmpro/get_client_data.php?vserverid=...` отдаёт JSON (`state`, `mainip`, `bandwidthused`).
  - CPU и RAM есть только картинками графиков.
- Питание: POST на карточку услуги, поля `token` (CSRF со страницы), `id`, `modop=custom`, `a=boot|shutdown|reboot`.
- Автопродление: модуль ModulesGarden HostingRenewals.
  - Состояние видно по тому, какая из кнопок `mg-hosting-renewals-turn-on-button` / `turn-off-button` не скрыта.
  - Действие открывается модальным окном: `loadModal(... 'turnOnRenewals' | 'turnOffRenewals' | 'renewService' ...)`. Как отправляется форма подтверждения, ещё нужно посмотреть на тестовом сервере.
- Пополнение:
  1. `POST clientarea.php?action=addfunds` с полями `token`, `amount` (от 100) и `paymentmethod=paymaster_sbp`. Появляется счёт `viewinvoice.php?id=N`.
  2. На странице счёта есть форма PayMaster (`https://paymaster.ru/Payment/Init`). Бот отправляет её сам и получает редирект на `https://paymaster.ru/cpay/<id>`.
  3. `POST https://paymaster.ru/cpay/api/Auth/AuthPaymentExternal` с заголовком `x-public-id: <id с дефисами>` и телом `{"paymentMethodAlias": "SBP"}`. После этого страница показывает ссылку `qr.nspk.ru`. Шаги 1 и 2 проверены из Python; шаг 3 пока только в браузере.

## ЮKassa: как получить ссылку СБП

Подходит для Timeweb и Aeza. Проверено из Python, без браузера.

1. `GET https://yoomoney.ru/checkout/payments/v2/contract/sbp?orderId=<id>`. В HTML страницы есть CSRF-токен вида `<40 hex>:<13 цифр>`.
2. `POST https://yoomoney.ru/checkout/payments/v2/payment/sbp/start` `{"orderId": ...}` с заголовком `x-csrf-token`.
3. `POST https://yoomoney.ru/checkout/payments/v2/payment/status` `{"orderId": ...}`. Повторять, пока в ответе не появится `payload.qrCodeData`. Это и есть ссылка `https://qr.nspk.ru/...`.

Ссылка `qr.nspk.ru` открывает выбор банка прямо на телефоне. Из неё же рисуется QR-картинка для оплаты с другого устройства. Оплата через ЮKassa живёт около 10 минут, поэтому ссылку стоит запрашивать в момент, когда человек нажал «Пополнить», а не заранее.
