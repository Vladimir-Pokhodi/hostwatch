"""Errors a plugin raises. The core turns them into account statuses and polling pauses."""


class PluginError(Exception):
    code = "plugin_error"


class AuthExpired(PluginError):
    """The key or session no longer works: the owner has to log in again."""

    code = "auth_expired"


class RateLimited(PluginError):
    """The hoster asked to slow down (HTTP 429 and the like)."""

    code = "rate_limited"


class Blocked(PluginError):
    """Anti-bot protection or HTTP 403 stopped the request."""

    code = "blocked"


class LayoutChanged(PluginError):
    """The hoster's answer no longer matches what the plugin expects."""

    code = "layout_changed"


class HosterError(PluginError):
    """The hoster refused with a reason that is safe to show to the owner."""

    code = "hoster_error"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def describe(error: BaseException) -> str:
    """Short text for the account card.

    Never includes arbitrary exception text: it may hold secrets.
    """
    if isinstance(error, HosterError):
        return f"{error.code}: {error.reason}"
    if isinstance(error, PluginError):
        return error.code
    if isinstance(error, TimeoutError):
        return "timeout"
    return f"unexpected: {type(error).__name__}"
