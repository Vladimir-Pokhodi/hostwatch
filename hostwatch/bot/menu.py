"""The one live menu in the chat (plan v1.1, part 1).

The chat holds one menu message. Every screen edits it; «☰ Menu» or /start sends a new one
at the bottom and deletes the old one (Telegram deletes only messages younger than 48
hours: an older menu loses its buttons instead). Its id lives in the `settings` table, so a
restart keeps it. A button pressed in any other message answers «this menu is closed».

Notifications (alerts, the summary, autopay, login news) are messages of their own. Their
buttons carry `callbacks.NOTE`: pressing one opens a new menu below and shows the screen.

Every message of the owner is deleted once read: commands, «☰ Menu» and typed answers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)

from hostwatch.locales import t

from .callbacks import NOTE
from .prefs import LANGUAGES, Prefs
from .views import Screen

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

NOT_MODIFIED = "message is not modified"
CAPTION_LIMIT = 1024


def menu_buttons() -> frozenset[str]:
    """The «☰ Menu» texts in every language: the owner may switch languages."""
    return frozenset(t("menu.button", lang) for lang in LANGUAGES)


class Menu:
    """Shows screens in the menu message of the owner's chat."""

    def __init__(self, bot: Bot, chat_id: int, prefs: Prefs) -> None:
        self._bot = bot
        self._chat = chat_id
        self._prefs = prefs
        self._lock = asyncio.Lock()
        self._photo = False
        # What the menu shows now, for screens that change by themselves (a login link, an
        # invoice): `update` edits the menu only while it still shows that. Lost on restart.
        self.tag: str | None = None
        # Menus the bot replaced itself (a photo, a message it could not edit): a handler
        # that started on the old one goes on in the new one.
        self._successor: dict[int, int] = {}

    @property
    def active_id(self) -> int | None:
        return self._prefs.menu_id

    def adopt(self, message_id: int) -> None:
        """Take a pressed message as the menu: there was none yet (the first run of 1.1)."""
        self._prefs.menu_id = message_id
        self._photo = False
        self.tag = None

    def follows(self, message_id: int) -> bool:
        """True if the menu is this message, or one the bot put in its place."""
        seen = set()
        current: int | None = message_id
        while current is not None and current not in seen:
            if current == self.active_id:
                return True
            seen.add(current)
            current = self._successor.get(current)
        return False

    async def show(self, screen: Screen, tag: str | None = None) -> None:
        """Edit the menu to `screen`; a new menu below if there is none or it cannot be."""
        async with self._lock:
            await self._show(screen, tag)

    async def open(self, screen: Screen, tag: str | None = None, keyboard: bool = False) -> Message:
        """A new menu at the bottom of the chat; the old one goes. `keyboard`: send the
        «☰ Menu» keyboard again (after /start: the owner may have cleared the chat)."""
        async with self._lock:
            self._successor.clear()
            return await self._open(screen, tag, keyboard)

    async def move_down(self, screen: Screen, tag: str | None = None) -> None:
        """The menu goes below a message the bot just sent (a notice, a file)."""
        async with self._lock:
            await self._open(screen, tag, replace=True)

    async def update(self, tag: str, screen: Screen, keep: bool = False) -> bool:
        """Edit the menu only while it shows the screen `tag`; False if it moved on.
        `keep`: the new screen still counts as `tag`."""
        async with self._lock:
            if self.tag != tag:
                return False
            await self._show(screen, tag if keep else None)
            return True

    async def notify(self, text: str, markup: InlineKeyboardMarkup | None = None) -> int | None:
        """A message of its own, not the menu; its id, or None if it did not go."""
        try:
            message = await self._bot.send_message(self._chat, text, reply_markup=markup)
        except TelegramAPIError as exc:
            log.warning("cannot message the owner: %s", exc.message)
            return None
        return message.message_id

    async def delete(self, message_id: int) -> bool:
        """Delete a notification (a password after its minutes); False if Telegram refuses."""
        try:
            return await self._bot.delete_message(self._chat, message_id)
        except TelegramAPIError:
            return False

    async def close(self, message_id: int) -> None:
        """Delete an old menu; if Telegram refuses (48 hours), take its buttons away."""
        with contextlib.suppress(TelegramAPIError):
            if await self._bot.delete_message(self._chat, message_id):
                return
        try:
            await self._bot.edit_message_text(
                t("menu.closed", self._prefs.lang), chat_id=self._chat, message_id=message_id
            )
            return
        except TelegramAPIError:
            pass
        # A photo has no text to edit; or the message is gone already.
        with contextlib.suppress(TelegramAPIError):
            await self._bot.edit_message_reply_markup(
                chat_id=self._chat, message_id=message_id, reply_markup=None
            )

    async def _show(self, screen: Screen, tag: str | None) -> None:
        active = self.active_id
        if active is None or self._photo or self._wants_photo(screen):
            # A text message cannot become a photo, nor a photo a text: a new menu.
            await self._open(screen, tag, replace=active is not None)
            return
        try:
            await self._bot.edit_message_text(
                screen.text, chat_id=self._chat, message_id=active, reply_markup=screen.markup
            )
        except TelegramBadRequest as exc:
            if NOT_MODIFIED not in exc.message:
                # Deleted by the owner, or not editable any more: a new one goes below.
                log.info("menu %s not edited (%s); sending a new one", active, exc.message)
                await self._open(screen, tag, replace=True)
                return
        self.tag = tag

    def _wants_photo(self, screen: Screen) -> bool:
        return screen.photo is not None and len(screen.text) <= CAPTION_LIMIT

    async def _open(
        self, screen: Screen, tag: str | None, keyboard: bool = False, replace: bool = False
    ) -> Message:
        lang = self._prefs.lang
        if keyboard or self._prefs.menu_keyboard != lang:
            await self._send_keyboard(lang)
        old = self.active_id
        if self._wants_photo(screen):
            assert screen.photo is not None
            sent = await self._bot.send_photo(
                self._chat,
                BufferedInputFile(screen.photo, filename="qr.png"),
                caption=screen.text,
                reply_markup=screen.markup,
            )
        else:
            sent = await self._bot.send_message(self._chat, screen.text, reply_markup=screen.markup)
        self._prefs.menu_id = sent.message_id
        self._photo = self._wants_photo(screen)
        self.tag = tag
        if old is not None and old != sent.message_id:
            if replace:
                self._successor[old] = sent.message_id
            await self.close(old)
        return sent

    async def _send_keyboard(self, lang: str) -> None:
        """The «☰ Menu» button under the input field. A reply keyboard comes with a
        message; the message is deleted right away and the keyboard stays."""
        label = t("menu.button", lang)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=label)]], resize_keyboard=True, is_persistent=True
        )
        try:
            carrier = await self._bot.send_message(self._chat, label, reply_markup=keyboard)
        except TelegramAPIError as exc:
            log.warning("cannot send the menu keyboard: %s", exc.message)
            return
        with contextlib.suppress(TelegramAPIError):
            await self._bot.delete_message(self._chat, carrier.message_id)
        self._prefs.menu_keyboard = lang


def menu_of(hub: Hub, bot: Bot, chat_id: int) -> Menu:
    """The hub's menu; made on the first update when the app did not make it (tests)."""
    if hub.menu is None:
        hub.menu = Menu(bot, chat_id, hub.prefs)
    return hub.menu


Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class DeleteOwnerMessages(BaseMiddleware):
    """Outer middleware on messages: the owner's message goes from the chat at once; its
    handler has read it already from the update. Keys and cookies go the same way."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, Message):
            menu_of(data["hub"], data["bot"], event.chat.id)
            with contextlib.suppress(TelegramAPIError):
                await event.delete()
        return await handler(event, data)


class MenuGuard(BaseMiddleware):
    """Outer middleware on button presses: only the live menu and notifications work.

    A notification button opens a new menu below (with `home` until the handler shows its
    screen) and the handler then edits that one, as if the button were in it."""

    def __init__(self, home: Callable[[Hub], Awaitable[Screen]]) -> None:
        self._home = home

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)
        hub: Hub = data["hub"]
        bot: Bot = data["bot"]
        menu = menu_of(hub, bot, event.from_user.id)
        raw = event.data or ""
        if raw.startswith(NOTE):
            state = data.get("state")
            if isinstance(state, FSMContext):
                await state.clear()  # a question the menu asked is over
            sent = await menu.open(await self._home(hub))
            moved = event.model_copy(update={"data": raw[len(NOTE) :], "message": sent})
            return await handler(moved.as_(bot), data)
        message = event.message
        active = menu.active_id
        if message is not None and active is None:
            menu.adopt(message.message_id)
        elif message is None or message.message_id != active:
            with contextlib.suppress(TelegramBadRequest):
                await event.answer(t("menu.stale", hub.prefs.lang), show_alert=True)
            return None
        return await handler(event, data)
