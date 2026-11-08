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
| Минимальное пополнение | 500 ₽ (101 и 200 ₽ не проходят) | €0.50 СБП, €5–10 крипта | $5 | 100 ₽ |

## Timeweb Cloud

Чтение и управление серверами идут через официальный API (`https://api.timeweb.cloud/api/v1/...`). Токен выпускается в панели, раздел «API и Терраформ». Что полезно в API:

- `GET /api/v1/account/finances` — `balance`, `hourly_cost`, `monthly_cost`, `hours_left`. Прогноз считать не нужно, Timeweb отдаёт его сам.
- `GET /api/v1/servers`, `/api/v1/dedicated-servers` — серверы и дедики.

**Счёт на пополнение через API создать нельзя.** Его создаёт только панель, своим внутренним запросом. Для этого нужна сессия панели:

- Cookie `refresh_token` живёт 30 дней. Cookie `device_token` живёт год.
- `POST https://timeweb.cloud/api/v1/auth/update-token` с этими cookie отдаёт `tokens.access_token` на 15 минут и новый `refresh_token`. **Старый refresh-токен после этого недействителен.** Новый нужно сохранять каждый раз, иначе сессия пропадёт. По той же причине бот и браузер человека не могут делить одну сессию.
- Запросы панели идут на `https://timeweb.cloud/portal/...` с заголовком `Authorization: Bearer <access_token>`. Логин аккаунта — claim `user` в access-токене (это JWT).
- Плательщик: `GET /portal/v1/accounts/<логин аккаунта>/payers?type=person` — голый список, нужен `id` (проверено 08.11).
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

  В ответе `invoice_id` (номер счёта) и `payment_info.confirmation.confirmation_url` — ссылка на страницу ЮKassa. Как из неё получить ссылку СБП, описано ниже в разделе «ЮKassa». Если сумма меньше минимальной, приходит 400 `{"error_code": "amount_less_minimal"}`: 08.11 так отказали 101 и 200 ₽, 500 ₽ прошли.
- `update-token` (проверено 08.11 с сервера бота): 201, новые `refresh_token` и `device_token` приходят в `Set-Cookie`, access-токен — в теле. Плагин сохраняет cookie сразу, до разбора тела.

QRATOR пропускает запросы из Python и с IP сервера бота (2.56.122.88, проверено 08.11).

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
  - В ответе ещё `id` счёта, `status: "created"`, `amount` в центах, `createdAt`. Списка счетов (`GET /billing/invoices`) нет: 404.
  - Минимумы — поле `min` (в центах) в `/billing/payment-methods`: СБП 50, большинство монет 500, BTC/ETH/USDT ERC20 1000.

Управление (автопродление, продление, питание) я нашёл в коде страниц, но не вызывал.

## Regxa

Сайт на Next.js, внутренний API лежит на `https://regxa.com/api/...`. Официального API нет. Авторизация — cookie сессии NextAuth. `GET /api/auth/session` показывает срок её действия, около суток. Судя по всему, срок продлевается при каждом запросе.

- `GET /api/profile/client/{clientId}` → `data.credit` (баланс в USD) и `data.stats` (неоплаченные счета).
- `GET /api/services/getservices?clientId={id}` — услуги: `status`, `nextDueDate`, `amount`, `billingCycle`. ID виртуалки лежит в `customfields`, в поле `vpsid`.
- `GET /api/services/vps/status/{vpsid}` — нагрузка (`used_cpu`, `used_ram`, `used_disk`, `used_bandwidth`) и `status`. Под капотом Virtualizor.
- `GET /api/services/vps/details/{vpsid}` — IP, ресурсы, флаг `suspended`.
- Питание: `POST /api/services/vps/power/{start|stop|restart|poweroff}/{vpsid}`.
- Счета: `GET /api/billing/invoices`. Оплатить счёт с баланса: `POST /api/billing/invoices/apply-credit` `{"invoiceid", "amount"}`.
  - Список (WHMCS GetInvoices) отдаёт 25 последних, новые сверху, без строк счёта. Сайт показывает те же 25.
  - Строки счёта: `GET /api/billing/invoices/{id}` → `items.item[]` с `type` и `relid`. У продления `type = "Hosting"`, `relid` = ID услуги из `getservices`. У пополнения `type = ""`, `relid = 0`.
- Сессия (проверено 08.11 на этапе 07): каждый `GET /api/auth/session` выдаёт новый `rgx.session-token` со сроком +24 ч. Плагин сохраняет его, поэтому сессия живёт, пока идёт опрос. ID клиента — `user.client_id` из того же ответа.
- Без сессии: `/api/auth/session` → `{}`, остальное → 401 или 403 с `{"error": "..."}`.
- Нагрузка в `vps/status`: `used_cpu` в %, `used_ram` / `ram` в МБ, `used_disk` / `disk` в ГБ, `used_bandwidth` в ГБ за месяц (`bandwidth = 0` — без лимита). Состояние `status`: 1 включён, 0 выключен, 2 приостановлен. Пока VPS создаётся, вместо объекта приходит одно число.
- Автопродления как функции нет. Сайт сам выставляет счёт перед окончанием срока.
- Пополнение:
  1. `POST /api/billing/addfunds` `{"amount": 5}` → `data.invoiceid`.
  2. `POST /api/billing/cryptomus/create-payment` `{"invoiceId", "returnUrl"}` → ссылка `https://pay.cryptomus.com/pay/<uuid>`.

  Ответы (08.11): `addfunds` → `{"success": true, "data": {"invoiceid", "total", "status"}}`; `create-payment` → `{"success": true, "url": "https://pay.cryptomus.com/pay/<uuid>"}`.

**Адрес кошелька Cryptomus без браузера не получить.** Страница выбирает сеть запросом `POST https://api-pay.cryptomus.com/v1/payment/method`, а в нём зашифрованный «отпечаток браузера». Поэтому адрес достаёт браузер без окна в контейнере `browser` (проверено 08.11): «Select currency» → монета → «Select network» → сеть («TRON (TRC-20)», «BSC (BEP-20)», «TON») → «Proceed to the payment». Ответ `payment/method` содержит `address`, `payer_amount`, `payer_currency`, `network` (`tron`, `bsc`, `ton`), `expired_at` (час). Если сеть уже выбрана, адрес есть сразу в `payment/info` при загрузке. Названия сетей страница пишет через CSS заглавными («Ton» → «TON»), искать текст надо без учёта регистра. Запасной вариант — отправить человеку ссылку.

## AdminVPS

Личный кабинет на WHMCS, перед ним защита DDoS-Guard. Данные отдаёт готовым HTML. Авторизация — cookie сессии WHMCS, сколько она живёт, пока неизвестно.

- DDoS-Guard (проверено 08.11 на этапе 08): обычный `httpx` с сервера бота с cookie входа через трансляцию проходит. На каждый ответ приходят новые `__ddg8_`, `__ddg9_`, `__ddg10_`. Гость получает редирект на вход.
- Баланс — в шапке любой страницы: `a.balance-link`, текст «4321.09 руб.», ссылка на `/payments.php`.
- Услуги — таблица `#tableServicesList` на `clientarea.php?action=services`, все строки сразу (DataTables на клиенте): название в `<strong>`, под ним hostname ссылкой; цена в `data-order` и период («ежемесячно»); дата следующего платежа в `<span class="hidden">ГГГГ-ММ-ДД</span>`; статус классом `status-active|suspended|pending|terminated|cancelled`; ID услуги в ссылке `productdetails&id=`. Удалённые и отменённые услуги остаются в списке.
- Карточка услуги: `clientarea.php?action=productdetails&id={id}`, ~280 КБ. ID сервера SolusVM — в скрипте `var vserverid = N;`, IP — `input#dedicatedip-hidden`.
  - IP, статус и трафик: `GET modules/servers/solusvmpro/get_client_data.php?vserverid=...` отдаёт JSON (`state`: `online`/`offline`, `mainip`, `bandwidthused` вида «417.7 MB»). Иногда отвечает дольше 20 с.
  - Каждый запрос к кабинету идёт ~1,3 с, поэтому опрос 6 услуг (1 + 2 × 6 запросов) занимает ~18 с.
  - CPU и RAM есть только картинками графиков.
- Питание: POST на карточку услуги, поля `token` (CSRF со страницы), `id`, `modop=custom`, `a=boot|shutdown|reboot`.
- Автопродление: модуль ModulesGarden HostingRenewals.
  - Состояние видно по тому, какая из кнопок `mg-hosting-renewals-turn-on-button` / `turn-off-button` не скрыта.
  - Действие открывается модальным окном: `loadModal(... 'turnOnRenewals' | 'turnOffRenewals' | 'renewService' ...)`. Как отправляется форма подтверждения, ещё нужно посмотреть на тестовом сервере.
- Пополнение:
  1. `POST clientarea.php?action=addfunds` с полями `token`, `amount` (от 100 до 200 000) и `paymentmethod=paymaster_sbp`. Ответ 200 — страница с формой PayMaster (`https://paymaster.ru/Payment/Init`, поля `LMI_*`; номер счёта — `LMI_PAYMENT_NO`). Счёт виден как `viewinvoice.php?id=N`, там та же форма.
  2. Бот отправляет форму сам и получает `302` на `https://paymaster.ru/cpay/<id>` (32 hex без дефисов).
  3. `POST https://paymaster.ru/cpay/api/Auth/AuthPaymentExternal` с заголовком `x-public-id: <id>` (как в ссылке, без дефисов) и телом `{"paymentMethodAlias": "SBP"}` → `data.authorizationResultExt.externalConfirmationInfoExt.paymentUrl` = ссылка `qr.nspk.ru`, `status: "AwaitingPayment"`. Все три шага проверены из Python с сервера бота 08.11.

## ЮKassa: как получить ссылку СБП

Подходит для Timeweb и Aeza. Проверено из Python, без браузера.

1. `GET https://yoomoney.ru/checkout/payments/v2/contract/sbp?orderId=<id>`. В HTML страницы есть CSRF-токен вида `<40 hex>:<13 цифр>`.
2. `POST https://yoomoney.ru/checkout/payments/v2/payment/sbp/start` `{"orderId": ...}` с заголовком `x-csrf-token`.
3. `POST https://yoomoney.ru/checkout/payments/v2/payment/status` `{"orderId": ...}`. Повторять, пока в ответе не появится `payload.qrCodeData`. Это и есть ссылка `https://qr.nspk.ru/...`. 08.11 она пришла с первого опроса (`payload.status: "SbpConfirmationRequired"`).

Ссылка ЮKassa от хостера имеет вид `https://yoomoney.ru/checkout/payments/v2/contract/sbp?orderId=<id>` (Aeza и Timeweb).

Ссылка `qr.nspk.ru` открывает выбор банка прямо на телефоне. Из неё же рисуется QR-картинка для оплаты с другого устройства. Оплата через ЮKassa живёт около 10 минут, поэтому ссылку стоит запрашивать в момент, когда человек нажал «Пополнить», а не заранее.
