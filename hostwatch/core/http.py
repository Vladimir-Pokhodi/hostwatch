"""HTTP client the core hands to a plugin. Logs method, host, path and status only."""

import logging

import httpx

# Same browser as the login page (Chromium of Playwright 1.64), so the hoster sees one client.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def make_client(plugin_id: str, proxy: str | None = None) -> httpx.AsyncClient:
    log = logging.getLogger(f"hostwatch.http.{plugin_id}")

    async def log_response(response: httpx.Response) -> None:
        request = response.request
        # No query string: some hosters put ids or tokens there.
        log.debug(
            "%s %s%s -> %s",
            request.method,
            request.url.host,
            request.url.path,
            response.status_code,
        )

    return httpx.AsyncClient(
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        proxy=proxy,
        event_hooks={"response": [log_response]},
    )
