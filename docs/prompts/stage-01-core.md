# Этап 01: ядро

Работаешь в `D:\Projects\hostwatch`.

## Прочитай

1. `docs/spec.md`: решения 3, 4, 7, 8, 9 и разделы «Архитектура», «Требования к коду и текстам».
2. `docs/research.md`, бегло: чтобы модель подошла всем четырём хостерам.
3. Код этапа 00.

## Сделай

1. `core/models.py`: `Money` (Decimal + валюта), `Server`, `ServerKind`, `ServerStatus`, `BillingModel` (`hourly` / `periodic`), `Balance`, `Usage`, `Invoice`, `Payment`, `PowerAction`, `ActionResult`. Поля — как в решении 4.
2. `core/plugin.py`: базовый `Plugin`, `PluginMeta`, `ApiKeyAuth`, `BrowserAuth`, Protocol'ы возможностей (`SupportsUsage`, `SupportsTopup`, `SupportsInvoices`, `SupportsPayInvoice`, `SupportsAutorenew`, `SupportsRenew`, `SupportsPower`, `SupportsOrder`) и функция `capabilities(plugin) -> set[Capability]`.
3. `core/errors.py`: `AuthExpired`, `RateLimited`, `Blocked`, `LayoutChanged`, `HosterError(reason)`.
4. `core/registry.py`: находит плагины сканированием `hostwatch.plugins` и папки из `PLUGINS_DIR` (для локальных плагинов).
5. `core/http.py`: фабрика `httpx.AsyncClient` для плагина: таймауты, User-Agent, прокси, event hooks без логирования тел и заголовков.
6. `storage/`: SQLite-схема из решения 4, миграции простым списком SQL по версиям, репозитории для аккаунтов (секреты шифруются через `core/secrets.py`), серверов, `balance_history`, `alerts`, `journal`, `settings`.
7. `core/accounts.py`: выполнение вызова плагина для аккаунта под `asyncio.Lock`; сохранение обновлённых секретов **до** возврата результата; перевод ошибок плагина в статус аккаунта.
8. `core/forecast.py`: прогноз из решения 9 (почасовой, лента периодических списаний, сверка с реальным расходом) с текстовым объяснением метода.
9. `core/rates.py`: курсы ЦБ (`cbr.ru`, запасной `cbr-xml-daily.ru`), кэш на сутки.
10. `scheduler/`: задачи опроса из решения 7 (интервалы, джиттер, повторы, пауза при `Blocked`, таймаут 60 с на аккаунт).
11. `hostwatch/__main__.py` с CLI: `hostwatch check <plugin_or_account> [--key ...]` печатает серверы, баланс, возможности, прогноз и время ответа, ничего не меняя.
12. `plugins/fake/`: фейковый плагин с настраиваемыми ответами для тестов и демонстрации.
13. `tests/contract.py`: общий контрактный тест плагина (решение 14) и его прогон на `fake`.
14. Логи: `logging` с фильтром, который заменяет на `***` значения загруженных секретов.

## Не делай

- Не пиши настоящие плагины и бота.
- Никаких «на всякий случай»: только то, что нужно по spec.

## Проверь

- Тесты прогноза: только почасовые, только периодические, смесь, нехватка на втором списании, сервер без продления, расхождение с реальным расходом > 20 %.
- Тест: `fake` с ошибкой на одном аккаунте не мешает опросу другого.
- Тест: ротация секретов сохраняется даже при ошибке после неё.
- Тест: секрет не появляется в логах.
- `hostwatch check fake` печатает понятный вывод.
- CI зелёный.

## Готово, когда

Все тесты зелёные, `check fake` работает, `mypy --strict` на `core` без ошибок. Строка в «Журнал этапов» в `docs/plan.md`.
