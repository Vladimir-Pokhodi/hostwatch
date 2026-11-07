"""The bot answers its owner only. Everyone else gets silence, not even an error."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

log = logging.getLogger(__name__)


class OwnerOnly(BaseMiddleware):
    """Outer middleware on `Dispatcher.update`: it runs before any filter or handler."""

    def __init__(self, owner_id: int) -> None:
        self._owner_id = owner_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not isinstance(user, User) or user.id != self._owner_id:
            log.info("dropped an update from a stranger")
            return None
        return await handler(event, data)
