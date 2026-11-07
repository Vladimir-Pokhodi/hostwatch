"""AdminVPS (my.adminvps.ru): a stub until stage 08 — browser login only, no polling yet.

The cabinet is WHMCS behind DDoS-Guard. The WHMCS session cookie is `WHMCS<random>`;
DDoS-Guard sets `__ddg*` cookies tied to the visitor's IP, kept for later requests.
"""

from __future__ import annotations

from hostwatch.core.errors import AuthExpired, Blocked, HosterError
from hostwatch.core.models import Balance, Server
from hostwatch.core.plugin import BrowserAuth, Plugin, PluginMeta, cookie_header

SITE = "https://my.adminvps.ru"
AUTH = BrowserAuth(
    start_url=f"{SITE}/login",
    allowed_domains=("adminvps.ru",),
    cookies=("WHMCS*",),
    extra_cookies=("__ddg*",),
)


class AdminVpsPlugin(Plugin):
    meta = PluginMeta(id="adminvps", name="AdminVPS", site=SITE, auth=(AUTH,), currency="RUB")
    login_only = True

    async def check_login(self) -> None:
        # WHMCS gives a session cookie to every visitor; only the client area proves a login.
        response = await self.ctx.http.get(
            f"{SITE}/clientarea.php",
            headers={"Cookie": cookie_header(self.ctx.secrets, AUTH)},
            follow_redirects=False,
        )
        if response.status_code in (301, 302, 303):
            raise AuthExpired
        if response.status_code == 403 or "ddos-guard" in response.text[:2000].lower():
            raise Blocked
        if response.status_code >= 400:
            raise HosterError(f"HTTP {response.status_code}")
        if "logout.php" not in response.text:
            raise AuthExpired

    async def servers(self) -> list[Server]:
        raise HosterError("the AdminVPS plugin arrives at stage 08")

    async def balance(self) -> Balance:
        raise HosterError("the AdminVPS plugin arrives at stage 08")
