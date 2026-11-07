# Этап 02: плагин Aeza (чтение)

Работаешь в `D:\Projects\hostwatch`.

## Прочитай

1. `docs/research.md`, раздел Aeza.
2. `docs/spec.md`: решения 3, 9, 14.
3. `core/plugin.py`, `core/models.py`, `plugins/fake/`, `tests/contract.py`.

## Сделай

1. `plugins/aeza/`: `ApiKeyAuth(["api_key"])`, заголовок `X-API-Key`, база `https://my.aeza.net/api/v2/`.
2. `servers()` из `GET /services`: статус, IP, тариф, цена (центы → `Money`), `expiresAt`, `autoProlong`, `paymentTerm` → `BillingModel`. `Server.actions` — из `capabilities` услуги.
3. `balance()` из `GET /accounts/me` (центы, валюта аккаунта).
4. `SupportsUsage`: CPU, RAM, сеть из `/services/{id}/stats/...` за последние 15 минут.
5. `SupportsInvoices` (история списаний из `/billing/transactions`, нужна для сверки прогноза).
6. Строгие pydantic-модели ответов. Незнакомый статус → `ServerStatus.unknown` с исходным значением в `raw_status`.
7. Команда `hostwatch record <account>` (если её ещё нет — сделай в ядре): прогоняет плагин, сохраняет ответы в `tests/fixtures/<plugin>/` и заменяет в них ключи, email, имена и IP на заглушки.
8. Тесты на фикстурах через `respx` + контрактный тест.

## Не делай

- Пополнение, управление, заказ — следующие этапы. Методы, которые меняют что-то у хостера, не вызывай вообще.

## Проверь

- Попроси владельца дать API-ключ Aeza (через переменную окружения в своём терминале, не в чат). Запусти `hostwatch check aeza`, сравни с кабинетом и покажи владельцу.
- Посмотри записанные фикстуры глазами: никаких ключей, email, настоящих IP.
- `gitleaks` чист, CI зелёный.

## Готово, когда

`check aeza` совпадает с кабинетом, контрактный тест на фикстурах зелёный. Строка в «Журнал этапов».
