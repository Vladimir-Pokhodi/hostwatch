# hostwatch: как заказать сервер у каждого хостера

Разбор сделан 8 ноября 2026 года. Бюджет нулевой, поэтому пробных заказов не было. Финальный шаг заказа (создание заказа, счёта или сервера) не выполнялся ни у одного хостера. Он описан по документации, коду страниц и формам, и у каждого пункта стоит пометка, проверен он или нет.

Как смотрел:

- Timeweb — официальная документация API (`https://timeweb.cloud/api-docs-data/bundle.json`) и GET-запросы каталога с токеном.
- Aeza — код кабинета `my.aeza.net` и встроенное описание старого API (`/api/docs`, «v1 compat layer»). Живые GET шли с ключом с сервера бота.
- Regxa — код страниц кабинета (`/dashboard/order/cloud`, `/dashboard/services/cloud/[id]`). Живые GET шли с сессией из бота, с сервера.
- AdminVPS — витрина и корзина WHMCS открыты без входа. Корзину прошёл гостем с ПК до страницы оформления, потом очистил. В корзину владельца ничего не добавлял.

Пароли существующих серверов не читались и не выводились. Проверялось только, есть ли поле и пустое ли оно.

## Сводка

| | Timeweb Cloud | Aeza | Regxa | AdminVPS |
|---|---|---|---|---|
| Каталог | API: тарифы, ОС, локации | API: продукты, группы-локации, ОС, цена | JSON кабинета: планы, локации, ОС, сроки | HTML витрины WHMCS, без входа |
| Заказ | `POST /api/v1/servers` | `POST /services/orders`, `method: "balance"` | `POST /api/orders/cloud/create-order` | корзина WHMCS: конфигуратор → оформление |
| Оплата | почасовая, с баланса сама | с баланса | кредитом, если его хватает; иначе счёт | `applycredit=1` при оформлении |
| SSH-ключ при заказе | да, `ssh_keys_ids` | да, `parameters.sshKeyIds` | нет | нет; в аккаунте есть модуль ключей, действует ли он на новый сервер — неизвестно |
| Root-пароль | да: `root_pass` в карточке сервера | да: `secureParameters.password` | да: `GET /api/services/vps/password/{vpsid}` | нет; пароль задаём сами при заказе, потом только смена |
| Самый дешёвый вариант | 350 ₽/мес (≈0,5 ₽/ч), Екатеринбург и Казань | €0.04/ч (MSKs-1 и другие «-1»), €4.50/мес Custom | $4.50/мес, X1 | 349 ₽/мес Lite (Россия), 499 ₽/мес Promo |
| Уверенность | высокая | выше средней | средняя | средняя |
| Вывод | делаем в v1 | делаем в v1 | нужен пробный заказ | нужен пробный заказ |

«Нужен пробный заказ» значит: код заказа пишем в v1 по этому разбору, но включаем его только после пробного заказа владельцем. Что именно проверить, написано в разделе хостера.

## Timeweb Cloud

Всё ниже — по официальной документации API. Каталог прочитан живыми запросами 08.11. Все запросы требуют токен: без него `presets/servers` отвечает 401.

**Каталог (проверено):**

- `GET /api/v1/presets/servers` — тарифы, `server_presets[]`: `id`, `location` (`ru-1`…`ru-5`, `nl-1`, `de-1`, `us-4`, `kz-1`), `price` (₽ в месяц), `cpu`, `ram` и `disk` в МБ, `disk_type`, `bandwidth`, `description_short` («Cloud EKB 15»), `tags`. 08.11 пришло 108 тарифов. Дешевле всех 350 ₽: 1 CPU, 1 ГБ, 15 ГБ NVMe в `ru-4` (Екатеринбург) и `ru-5` (Казань).
- `GET /api/v1/os/servers` — 30 ОС: `id`, `name`, `version`, `requirements` (минимум CPU, RAM, диска). Например, Ubuntu 26.04 — `id` 145.
- `GET /api/v2/locations` — 10 локаций с `location_code` (ISO) и `availability_zones`.
- `GET /api/v1/configurator/servers` — конфигураторы для произвольной сборки: шаги и пределы CPU, RAM, диска и цены за единицу. Боту хватит готовых тарифов.
- Периодов нет. Оплата почасовая, цена в каталоге месячная. В `Offer` ставим `period_months = 1`.

**Заказ (по документации, не вызывался):** `POST /api/v1/servers`.

```json
{"name": "hw-1", "preset_id": 6767, "os_id": 145, "ssh_keys_ids": [123]}
```

- Обязательно `name`. Ещё нужен `preset_id` или `configuration` и `os_id` или `image_id`.
- Необязательные поля: `bandwidth`, `is_ddos_guard`, `comment`, `cloud_init`, `project_id`, `hostname`, `network`.
- Ответ 201: `server` — тот же объект, что в `GET /servers/{id}`: `id`, `status` (`installing`, затем `on`), `root_pass`, `networks[].ips[]`, `os`, `preset_id`, `location`. Ошибки — 400, 403, 409, 429 в общем формате API.
- Денег не списывают при заказе: идёт почасовое списание с баланса. Какую ошибку API вернёт при пустом балансе, по документации не видно.

**Готовность и IP.** Опрашивать `GET /api/v1/servers/{id}`, пока `status` не станет `on`. IP лежит в `networks[]` с `type = "public"`, в `ips[]` с `type = "ipv4"` (структуру видно на двух существующих серверах).

**SSH-ключ: да.** `POST /api/v1/ssh-keys` `{"name", "body", "is_default"}` → `id`, его передаём в `ssh_keys_ids`. Ключ добавляется и на готовый сервер: `POST /api/v1/servers/{id}/ssh-keys`. Сейчас в аккаунте ключей 0, поэтому бот при первом заказе создаёт ключ из настроек.

**Root-пароль: да.** `root_pass` приходит в `GET /api/v1/servers/{id}` и в списке серверов. 08.11 поле есть и не пустое у обоих серверов аккаунта. Сброс пароля — `POST /api/v1/servers/{id}/reset-password` (204), в бот его не берём.

**Не проверено:** сам `POST /servers` и ответ при нехватке денег. Тест у владельца на этапе 13 стоит около 0,5 ₽ в час.

**Вывод: делаем в v1.**

## Aeza

Кабинет работает на том же API v2, что и ключ (`https://my.aeza.net/api/v2/`, `X-API-Key`). Все GET ниже прочитаны 08.11 с сервера бота.

**Каталог (проверено):**

- `GET services/products` → `{items, total}`, 405 продуктов: VPS, домены, дедики и другие. Нужны `type.slug` `vps` и `hicpu`, 143 продукта. Поля продукта:
  - `id`, `name` («MSKs-1»), `groupId`, `group.name` («Shared», «Dedicated», «Promo»);
  - `parameters`: `cpu`, `ram` и `rom` в ГБ, `net`;
  - `isAvailable`, `isPublic`, `tags` (`custom`, `promo`, `windows_disabled`);
  - `prices.base` в центах евро по срокам: `hour`, `week`, `month`, `quarter_year`, `half_year`, `year`. Не у всех продуктов есть все сроки: у Custom нет `hour`.
  - У продуктов с тегом `custom` есть `resources` (цена за ядро, ГБ RAM и ГБ диска). В v1 их не берём.
- `GET services/groups` — 51 группа. Локация — группа с `role: "location"`: `name` («Москва»), `payload.code` (`ru`, `de`, `nl`…). Группа продуктов ссылается на неё через `parentId`. В `payload` есть `label` («RU-SHARED»), `mode` (`shared`/`dedicated`) и `isDisabled`: Варшава сейчас закрыта.
- `GET services/operating-systems` — 32 ОС: `slug` (`ubuntu_2604`), `name`, `enabled`, `username` (`root`), `supportsSshKeys`.
- `GET services/operating-systems/recipes` — готовые приложения. Не нужны.
- Цена перед подтверждением: `GET services/orders/price?productId=153&paymentTerm=month&isBackupsEnabled=false&termCount=1` → `totalPrice` (центы), `items`, `prolongPrice`, `availability: {status: "ok", resourcesAvailable, ipv4Available, ipv6Available}`. Для MSKs-1: 593 за месяц, 4 за час. Это и есть «перечитать цену перед подтверждением», заодно с наличием.
- `GET services/limits` — сколько услуг каждого вида ещё можно заказать (`available`, `used`). Shared VDS: 30, занято 4.

**Заказ (по коду кабинета, не вызывался):** `POST services/orders`.

```json
{
  "orders": [{
    "count": 1, "term": "month", "termCount": 1, "name": "hw-1", "productId": 153,
    "parameters": {"os": "ubuntu_2604", "sshKeyIds": [1]},
    "autoProlong": true, "backups": false
  }],
  "method": "balance"
}
```

- Так собирает тело мастер заказа (`order-wizard`). `method: "balance"` окно оплаты ставит, когда выбран баланс. С другим методом приходит ссылка на оплату — это нам не нужно.
- Имена полей совпадают со старым API: `V1OrderRequestDto` с `productId`, `count`, `name`, `term`, `autoProlong`, `backups`, `method: "balance"`, `parameters: {os}`. Там же написано: «use Core API `POST /v2/services/orders`». И совпадают с `payload` прошлых заказов владельца в `GET billing/transactions`: `productId`, `term`, `termCount`, `parameters`, `autoProlong`, `backups`, `name`, `additionalResources`.
- Ответ, по коду кабинета: транзакция `{id, serviceId, orderInfo: {createdServiceIds}}` или `{transactions: [...]}`. Если `serviceId` ещё нет, кабинет до 90 с опрашивает `GET billing/transactions/{id}`. Ошибка — `{error: {message}}` в теле или HTTP 4xx `{statusCode, message, error}`.

**Готовность и IP.** `GET services/{id}`: `status`, `ip`, `currentTask`, `capabilities`.

**SSH-ключ: да.** У ОС есть `supportsSshKeys`. Ключи хранятся в аккаунте: `GET/POST services/ssh-keys` `{"name", "publicKey", "autoAssign"}`. Их `id` передаём в `parameters.sshKeyIds`. Сейчас ключей 0.

**Root-пароль: да.** `GET services/{id}` → `secureParameters.password`, логин в `parameters.username`. 08.11 поле есть и не пустое у VPS аккаунта, а у услуги-домена `secureParameters` пустой. Смена пароля — `POST services/{id}/change-password`, в бот не берём.

**Не проверено:**

- Можно ли заказывать по API-ключу. Ключ должен работать: в старом API у `createServiceOrder` указан `X-API-KEY`.
- Точный ответ `POST services/orders` и ответ при нехватке баланса.

Первый заказ владельца на этапе 13 закрывает оба вопроса: MSKs-1 почасово, €0.04 в час.

**Вывод: делаем в v1.**

## Regxa

Заказ VPS в кабинете — страница `/dashboard/order/cloud` (Regxa Cloud, Virtualizor). Витрину публичного сайта (`/linux-vps` и т. п.) не разбирал: кабинет заказывает через свой JSON. CSRF-токена нет, как и в остальных запросах Regxa: cookie сессии и JSON.

**Каталог (проверено):** `GET /api/cloudvps/order-plans` → `{data, error}`.

- `data.locations` — 11 локаций: `id`, `country`, `city`, `status`, `base_price_multiplier` (сейчас везде 1.00).
- `data.plans` — 63 записи. Один план повторяется для каждой своей локации (`location_id`). Поля: `id`, `plan_id`, `name`, `status`, `base_price`, `final_price`. CPU, RAM и диск — строками: `cpu_cores` («1 Core»), `ram_gb` («1 GB»), `storage_gb` («30 GB»). Дешевле всех «Cloud VPS X1» за $4.50/мес: 1 ядро, 1 ГБ, 30 ГБ.
- `data.operatingSystems` — 25 ОС: `id`, `name` («Ubuntu»), `version` («24.04»), `template_id`. Какие ОС есть у плана — `data.planOSMap[plan.id]`.
- `data.billingTerms` — сроки: 1, 3, 6, 12 месяцев без скидки, 24 со скидкой 5 %, 36 — 10 %.
- Цену страница считает сама: `base_price × location.base_price_multiplier × (1 − discount_percentage/100) × quantity × duration_months`, с округлением до центов.

**Заказ (по коду страницы, не вызывался):** `POST /api/orders/cloud/create-order`, JSON.

```json
{
  "selectedLocation": {"id": 25, "country": "Poland", "city": "Warsaw", "...": "объект из locations"},
  "selectedPlan": {"id": 31, "plan_id": 527, "...": "объект из plans, числа приведены к Number"},
  "selectedOS": "Ubuntu", "selectedOSVersion": "24.04",
  "selectedDuration": {"id": 1, "duration_months": 1, "...": "объект из billingTerms"},
  "quantity": 1, "ipv6Enabled": false,
  "operatingSystems": ["ОС этого плана, объекты из operatingSystems"],
  "paymentMethod": "stripe"
}
```

- Страница передаёт целые объекты из каталога. Пересчитывает ли сервер цену сам, не видно.
- Оплата кредитом решается на сервере. Если кредита хватает, страница пишет «Using Account Credit» и сама ставит `paymentMethod: "stripe"`. Другие значения — `paypal` и `cryptomus`.
- Ответ:
  - `{success: true, useCredit: true, productIds}` — оплачено кредитом, страница уходит на `/dashboard/services/cloud/{productIds}`;
  - `{success: true, invoiceId}` — выставлен счёт, страница открывает его;
  - `{success: false, error}`.
- Если пришёл счёт, его можно оплатить кредитом: `POST /api/billing/invoices/apply-credit` (`research.md`, не вызывался).

**Готовность и IP.** Новая услуга появится в `GET /api/services/getservices` (сначала `Pending`, потом `Active`) с полем `vpsid`. IP — `vps/details/{vpsid}`. Пока VPS создаётся, `vps/status` отдаёт число (`research.md`).

**SSH-ключ: нет.** В теле заказа ключа нет. Среди запросов страницы сервера тоже нет: `hostname`, `iso/*`, `password`, `power/*`, `rebuild`, `rescue-mode`, `stats`, `status`, `templates`, `vnc`.

**Root-пароль: да.** `GET /api/services/vps/password/{vpsid}` → `{username, password}`. 08.11 ответ 200, оба поля не пустые у существующего VPS. Смена пароля — `PUT` на тот же адрес `{password}`, в бот не берём.

**Вывод: заказ требует пробного заказа владельцем.** Что проверить:

1. Проходит ли заказ с `paymentMethod: "stripe"`, когда кредита хватает. Что приходит в ответ: `useCredit` и `productIds` или счёт.
2. Если пришёл счёт, оплачивается ли он `apply-credit` и запускается ли после этого VPS.
3. Хватает ли серверу объектов из каталога как есть, или он ждёт полей, которых нет в `order-plans`.
4. Через сколько появляется `vpsid` и IP, и отдаёт ли `vps/password` пароль нового сервера.

План минимальный: X1 на 1 месяц за $4.50. Заказ делается обычным кабинетом, бот при этом смотрит сеть.

## AdminVPS

Заказ идёт через стандартную корзину WHMCS (шаблон `standard_cart`, тема `six_by_adminvps`). Витрина и корзина открыты без входа. DDoS-Guard пропустил запросы с ПК без cookie.

**Каталог (проверено, без входа):**

- Список групп — `select#gidForm` на любой странице `/store/...`. Локация = группа: «VPS в России», «VPS в Нидерландах», «VPS в Германии», «VPS в Финляндии», «VPS в Польше» и другие, всего 25 групп VPS, включая «Все включено», Bitrix, Windows и GPU.
- Группа `/store/<slug>`: карточки `#product{N}`:
  - название `#productN-name` («Виртуальный сервер: Promo (Польша)»);
  - характеристики текстом `#productN-description`;
  - цена `#productN-price` («Начиная от 499.00 руб. ежемесячно»);
  - ссылка `cart.php?a=add&pid=N`.
  - В части карточек есть «0 Доступно». Похоже на остаток: `a=add&pid=470` (Lite, Россия) ответил 200 без перехода в конфигуратор. Не проверял.
- Дешевле всех: Lite (Россия) за 349 ₽ (pid 470, похоже, нет в наличии) и Promo за 499 ₽: 1 ядро, 2 ГБ, 15 ГБ, в Польше (626), Германии (539), Финляндии (593) и Нидерландах (326).
- ОС и сроки видны только в конфигураторе, то есть каждый тариф требует отдельного `a=add`. Каталог дорогой: ~1,3 с на запрос. Кэш на час обязателен.

**Корзина (проверено гостем 08.11, шаги 1–5 и 7):**

1. `GET cart.php?a=add&pid=626` → 302 на `cart.php?a=confproduct&i=0`.
2. `GET cart.php?a=confproduct&i=0`: форма `#frmConfigureProduct`.
   - `billingcycle` — сроки с ценой: `monthly` 499, `quarterly` 1452, `semiannually` 2844, `annually` 5389, `biennially` 10180.
   - `hostname` — обязательное, `[A-Za-z0-9.-]{4,}`.
   - `rootpw` — обязательное.
   - `ns1prefix`, `ns2prefix`.
   - `configoption[37]` — ОС, например `3103` = Ubuntu 24.04, `3197` = Ubuntu 26.04.
   - `configoption[12]` маркетплейс, `[356]` Bitninja, `[54]` доп. IPv4 (0–10), `[354]` бэкапы. Значения по умолчанию бесплатные.
   - CSRF — переменная `csrfToken` в скрипте страницы. Её же надо слать полем `token`.
3. Цена: `POST cart.php` `ajax=1&a=confproduct&calctotal=true&<поля формы>` → HTML-итог: позиции, НДС 7 % и «К оплате сегодня» 499.00 руб.
4. `POST cart.php` `ajax=1&a=confproduct&<поля>&token=…`:
   - пустой ответ — позиция настроена;
   - иначе HTML со списком ошибок: «Вы должны ввести имя вашего сервера», «…пароль администратора (root)»;
   - «Произошла ошибка, пожалуйста, попробуйте еще раз.» — если позиции `i` в корзине уже нет.
5. `GET cart.php?a=checkout`: форма `#frmCheckout`, `POST /cart.php?a=checkout`.
   - Поля: `token`, `submit=true`, `custtype`, `applycredit` (радио: `1` выбрано по умолчанию, `0`), `paymentmethod` (скрытое, по умолчанию `paymaster_ng_direct`), `accepttos`, `applybonus`.
   - Способы оплаты в `data-payment-method`: `paymaster_sbp`, `paymaster`, `yandexauto_sberbank`, `yandexauto_yoo_money`, `assistby`, `bankinvoicerugw`.
   - Гостю показаны ещё поля регистрации. Как форма выглядит у вошедшего клиента, не смотрел: для этого пришлось бы класть товар в корзину владельца.
6. **Не выполнялось:** `POST /cart.php?a=checkout` с `applycredit=1`. По WHMCS это создаёт заказ и счёт, списывает кредит и, если кредита хватило, отмечает счёт оплаченным. Создаст ли модуль SolusVM сервер сразу или заказ ждёт ручной проверки, неизвестно.
7. Очистить корзину: `POST cart.php` `a=empty&token=…` → 302 на `cart.php?a=view`, корзина пуста.

**Готовность и IP.** Новая строка в `clientarea.php?action=services` (`status-pending`, потом `status-active`). Дальше как в плагине: `vserverid` из карточки и `get_client_data.php` → `mainip`, `state`.

**SSH-ключ: нет.** В форме заказа ключа нет. В аккаунте есть модуль `index.php?m=ssh_keys` («У Вас еще нет добавленных ключей», кнопка «Добавить»). Справка (`knowledgebase/229`) не говорит, ставятся ли эти ключи на новый сервер.

**Root-пароль: задаём сами, потом получить нельзя.** Пароль — поле `rootpw` формы заказа, бот его генерирует и показывает один раз. Позже кабинет пароль не отдаёт:

- в `get_client_data.php` поля `displayrootpassword` и `displayvncpassword` — флаги (`1`), а не пароли;
- `#rootpassword-hidden` на карточке пустой.

Есть только смена пароля через модуль SolusVM, а это изменение сервера.

**Вывод: заказ требует пробного заказа владельцем.** Что проверить:

1. Форму `#frmCheckout` у вошедшего клиента: `custtype`, есть ли `applycredit` при балансе ≥ суммы, какой `paymentmethod` нужен.
2. Ответ на `POST /cart.php?a=checkout`: переход на `cart.php?a=complete` или на счёт. Оплачен ли счёт кредитом целиком.
3. Создаётся ли сервер сам после оплаты и за сколько. Не висит ли заказ на ручной проверке.
4. Подходит ли наш `rootpw` для входа. Ставит ли модуль `m=ssh_keys` ключ на новый сервер, если добавить ключ заранее.

План: Promo на месяц, 499 ₽, Польша или Германия.

## Что дальше для этапа 13

- `catalog()`: Timeweb и Aeza — по API. Regxa — один запрос `order-plans`. AdminVPS — витрина и конфигуратор, с часовым кэшем.
- Перечитать цену перед подтверждением:
  - Aeza — `services/orders/price`;
  - AdminVPS — `calctotal`;
  - Timeweb — `presets/servers`;
  - Regxa — снова `order-plans`.
- «Показать пароль»: у Timeweb, Aeza и Regxa — запросом. У AdminVPS — только сразу после заказа (бот знает `rootpw`) или через смену пароля, которой в v1 нет.
- SSH-ключ владельца: у Timeweb и Aeza сначала найти или создать ключ в аккаунте, потом передать его `id` в заказ. Найти — по совпадению тела ключа.
