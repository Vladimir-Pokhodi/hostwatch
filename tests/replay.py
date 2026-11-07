"""Replays fixtures written by `hostwatch record` through respx.

Requests are matched by method and URL path; the query is ignored, because it holds
times. Several answers to one path come back in the order they were recorded.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
import respx

FIXTURES = Path(__file__).parent / "fixtures"


def load(plugin_id: str) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((FIXTURES / plugin_id).glob("*.json"))
    ]


def replay(router: respx.MockRouter, records: list[dict[str, Any]]) -> None:
    answers: defaultdict[tuple[str, str], list[httpx.Response]] = defaultdict(list)
    for record in records:
        answers[record["method"], record["url"]].append(_response(record))
    for (method, url), responses in answers.items():
        parsed = httpx.URL(url)
        router.route(method=method, scheme=parsed.scheme, host=parsed.host, path=parsed.path).mock(
            side_effect=responses
        )


def _response(record: dict[str, Any]) -> httpx.Response:
    if "json" in record:
        return httpx.Response(record["status"], json=record["json"])
    return httpx.Response(record["status"], text=record["text"])
