# Этап 07: плагин Regxa (чтение)

Работаешь в `D:\Projects\hostwatch`.

## Прочитай

1. `docs/research.md`, раздел Regxa.
2. `docs/spec.md`: решения 3, 7, 9, 14; допущение про срок сессии.
3. `plugins/aeza/` как образец, `web/` и `BrowserAuth` из этапа 06.

## Сделай

1. `plugins/regxa/`: `BrowserAuth(start_url="https://regxa.com/...", allowed_domains=[...], cookies=[cookie NextAuth])`. Проверка успеха — `GET /api/auth/session`.
2. `servers()`: `/api/services/getservices?clientId=...` + `/api/services/vps/details/{vpsid}` (IP, `suspended`). Модель оплаты — `periodic` (`nextDueDate`, `amount`, `billingCycle`). Автопродления у Regxa нет: `autorenew = n/a`.
3. `balance()`: `/api/profile/client/{clientId}` → `data.credit` (USD).
4. `SupportsUsage`: `/api/services/vps/status/{vpsid}`.
5. `SupportsInvoices`: `/api/billing/invoices`, со связью счёта и услуги, если она есть в ответе (нужно для автооплаты на этапе 11).
6. `AuthExpired` при 401 или пустой сессии.
7. Фикстуры, тесты, контрактный тест.

## Проверь

- Владелец входит через ссылку на сервере. `hostwatch check regxa` на сервере (внутри контейнера) совпадает с кабинетом.
- Через сутки сессия ещё жива (опрос раз в 15 минут). Запиши результат в таблицу допущений в `docs/spec.md`.

## Готово, когда

`check regxa` совпадает с кабинетом, сессия держится сутки или проблема записана и показана владельцу. Строка в «Журнал этапов».
