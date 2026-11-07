import io
import logging

import respx

from hostwatch.core.http import USER_AGENT, make_client
from hostwatch.core.logs import setup_logging


@respx.mock
async def test_client_sends_browser_user_agent_and_logs_no_secrets() -> None:
    route = respx.post("https://api.example.invalid/v1/things").respond(json={"token": "answer"})
    stream = io.StringIO()
    handler = setup_logging(logging.DEBUG, stream)
    try:
        async with make_client("example") as http:
            await http.post(
                "https://api.example.invalid/v1/things?session=query-secret",
                headers={"Authorization": "Bearer header-secret"},
                json={"password": "body-secret"},
            )
    finally:
        logging.getLogger().removeHandler(handler)

    output = stream.getvalue()
    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT
    assert "POST api.example.invalid/v1/things -> 200" in output
    for secret in ("query-secret", "header-secret", "body-secret", "answer"):
        assert secret not in output
