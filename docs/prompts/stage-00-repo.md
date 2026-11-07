# Этап 00: репозиторий и скелет

Сессию начинай в `D:\Projects\vpn`. Это единственный этап, где ты работаешь вне `D:\Projects\hostwatch`.

## Прочитай

1. `docs/spec.md` целиком, особенно решения 1, 2, 3, 5, 13, 14, 15 и раздел «Требования к коду и текстам».
2. `docs/plan.md`.

## Сделай

1. Создай `D:\Projects\hostwatch`, `git init`, ветка `main`.
2. Перенеси документы: `docs/research.md`, `spec.md`, `plan.md` и папку `prompts/` → `D:\Projects\hostwatch\docs\`. В `D:\Projects\vpn\docs\hostwatch\` оставь только `README.md` с одной строкой «Проект переехал в D:\Projects\hostwatch». Во всех перенесённых файлах замени пути `docs/...` на `docs/...`.
3. Скелет проекта:
   - `pyproject.toml`: Python 3.12, зависимости `aiogram>=3.13,<4`, `httpx`, `pydantic>=2`, `pydantic-settings`, `cryptography`, `segno`; dev — `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`. Управление окружением — `uv`.
   - Пакет `hostwatch/` с подпакетами `bot/`, `web/`, `scheduler/`, `core/`, `plugins/`, `storage/`, `locales/` (пустые `__init__.py` там, где кода ещё нет).
   - `hostwatch/core/config.py`: настройки из окружения (`BOT_TOKEN`, `OWNER_ID`, `HOSTWATCH_KEY`, `HOSTWATCH_DOMAIN`, `TG_PROXY`, `HEARTBEAT_URL`, `DATA_DIR`) через pydantic-settings, с понятной ошибкой, если обязательного нет.
   - `hostwatch/core/secrets.py`: `encrypt(bytes) -> bytes` / `decrypt(bytes) -> bytes` на AES-GCM, ключ — 32 байта из base64 `HOSTWATCH_KEY`, nonce случайный, хранится вместе с шифротекстом. Плюс `generate_key()`.
   - `hostwatch/locales/ru.py`, `en.py`, `hostwatch/locales/__init__.py` с `t(key, lang, **kwargs)`. Пока пара ключей для примера.
   - `.gitignore` (`.env`, `data/`, `__pycache__`, `.venv`), `.env.example` без значений, `LICENSE` (MIT), короткий `README.md`-заглушка на английском.
   - `.pre-commit-config.yaml` с `ruff` и `gitleaks`.
   - `.github/workflows/ci.yml`: `ruff check`, `ruff format --check`, `mypy --strict hostwatch/core`, `pytest`, `gitleaks`.
4. Тесты: шифрование туда и обратно, чужой ключ даёт ошибку, совпадение ключей `ru`/`en`, config падает без `BOT_TOKEN`.

## Не делай

- Ни бота, ни плагинов, ни Docker — это следующие этапы.
- Не создавай репозиторий на GitHub и не пушь. Это сделает владелец.

## Проверь

- `uv run pytest`, `uv run ruff check`, `uv run mypy --strict hostwatch/core` зелёные.
- `gitleaks detect` по репозиторию чист.
- В `D:\Projects\hostwatch` нет файлов из `D:\Projects\vpn`, кроме перенесённых документов.

## Готово, когда

Все проверки зелёные, документы перенесены, пути в них исправлены. В конце допиши строку в раздел «Журнал этапов» в `docs/plan.md` (создай раздел, если его нет): дата, этап, что сделано, что осталось.
