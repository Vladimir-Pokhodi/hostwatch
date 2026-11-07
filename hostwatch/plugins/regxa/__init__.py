"""Regxa (regxa.com): a stub until stage 07 — browser login only, no polling yet.

The site is Next.js with NextAuth; the session lives in the `rgx.session-token` cookie
(split into `.0`, `.1` when long). `GET /api/auth/session` returns `{}` without a session.
"""

from __future__ import annotations

from hostwatch.core.errors import AuthExpired, HosterError, LayoutChanged
from hostwatch.core.models import Balance, Server
from hostwatch.core.plugin import BrowserAuth, Plugin, PluginMeta, cookie_header

SITE = "https://regxa.com"
AUTH = BrowserAuth(
    start_url=f"{SITE}/login",
    # Google and GitHub: the site offers them as login options.
    allowed_domains=("regxa.com", "accounts.google.com", "github.com"),
    cookies=("rgx.session-token*",),
)


class RegxaPlugin(Plugin):
    meta = PluginMeta(id="regxa", name="Regxa", site=SITE, auth=(AUTH,), currency="USD")
    login_only = True

    async def check_login(self) -> None:
        response = await self.ctx.http.get(
            f"{SITE}/api/auth/session",
            headers={"Cookie": cookie_header(self.ctx.secrets, AUTH), "Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise HosterError(f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise LayoutChanged from None
        if not isinstance(data, dict) or not data.get("user"):
            raise AuthExpired

    async def servers(self) -> list[Server]:
        raise HosterError("the Regxa plugin arrives at stage 07")

    async def balance(self) -> Balance:
        raise HosterError("the Regxa plugin arrives at stage 07")
