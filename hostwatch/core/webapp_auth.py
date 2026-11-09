"""Checks the `initData` a Telegram Mini App sends (1.1, part 5).

Telegram signs it with the bot token: secret = HMAC_SHA256(key="WebAppData", msg=token),
hash = hex(HMAC_SHA256(key=secret, msg=data_check_string)), where data_check_string is
every field but `hash`, as `key=value`, sorted by key and joined by line feeds.
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

MAX_AGE = timedelta(hours=24)
# Clocks of Telegram and of this server may differ a little.
CLOCK_SKEW = timedelta(minutes=5)
# Real initData is well under 1 KB; anything much longer is not worth hashing.
MAX_LENGTH = 4096


class InvalidInitData(Exception):
    """The data is malformed, not signed by this bot, too old, or from someone else."""


@dataclass(frozen=True)
class WebAppUser:
    id: int
    language_code: str | None
    auth_date: datetime


def _secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def _check_string(fields: dict[str, str]) -> str:
    return "\n".join(f"{key}={fields[key]}" for key in sorted(fields) if key != "hash")


def sign(fields: dict[str, str], bot_token: str) -> str:
    """The `hash` Telegram would put on these fields; for tests and local previews."""
    return hmac.new(_secret(bot_token), _check_string(fields).encode(), hashlib.sha256).hexdigest()


def validate(
    init_data: str,
    bot_token: str,
    owner_id: int,
    now: datetime | None = None,
    max_age: timedelta = MAX_AGE,
) -> WebAppUser:
    """The owner who opened the Mini App, or InvalidInitData."""
    if not init_data or len(init_data) > MAX_LENGTH:
        raise InvalidInitData("empty or too long")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise InvalidInitData("not a query string") from None
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise InvalidInitData("repeated field")
    received = fields.get("hash", "")
    if not hmac.compare_digest(sign(fields, bot_token), received):
        raise InvalidInitData("bad signature")
    try:
        auth_date = datetime.fromtimestamp(int(fields["auth_date"]), UTC)
        user = json.loads(fields["user"])
        user_id = user["id"]
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        raise InvalidInitData("no auth_date or user") from None
    now = now or datetime.now(UTC)
    if auth_date > now + CLOCK_SKEW or now - auth_date > max_age:
        raise InvalidInitData("stale")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id != owner_id:
        raise InvalidInitData("not the owner")
    lang = user.get("language_code")
    return WebAppUser(user_id, lang if isinstance(lang, str) else None, auth_date)
