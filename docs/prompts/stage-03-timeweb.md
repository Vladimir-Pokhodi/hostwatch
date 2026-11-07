# Этап 03: плагин Timeweb Cloud (чтение по API)

Работаешь в `D:\Projects\hostwatch`.

## Прочитай

1. `docs/research.md`, раздел Timeweb Cloud.
2. `docs/spec.md`: решения 3, 9, 14.
3. `plugins/aeza/` как образец, `tests/contract.py`.
4. Официальная документация API Timeweb Cloud (`https://timeweb.cloud/api-docs`): найди сам эндпоинты статистики серверов и дедиков.

## Сделай

1. `plugins/timeweb/`: `ApiKeyAuth(["api_token"])`, `Authorization: Bearer`, база `https://api.timeweb.cloud/api/v1/`. Вход через браузер для счетов будет на этапе 09: заложи в `meta`, что у плагина два способа входа, но сейчас реализуй только токен.
2. `servers()`: `/servers` и `/dedicated-servers`. Дедики — `ServerKind.dedicated`. Модель оплаты — `hourly`.
3. `balance()`: `/account/finances` (`balance`, `hourly_cost`, `monthly_cost`, `hours_left`). Прогноз берёт `hours_left`, если он есть.
4. `SupportsUsage` — по статистике из API, если она там есть. Если нет — возможности нет.
5. Фикстуры через `hostwatch record`, тесты, контрактный тест.

## Не делай

- Ничего, что меняет состояние у хостера.

## Проверь

- Попроси у владельца API-токен Timeweb (через переменную окружения). `hostwatch check timeweb` совпадает с панелью, прогноз совпадает с «хватит на N часов» в панели.
- Фикстуры чистые, `gitleaks` чист, CI зелёный.

## Готово, когда

`check timeweb` совпадает с панелью, контрактный тест зелёный. Строка в «Журнал этапов».
