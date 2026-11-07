import json
from pathlib import Path

import httpx

from hostwatch.core.record import Exchange, Recorder, Scrubber, write_fixtures

SECRET = "secret-value-for-tests"


def exchange(url: str, body: object) -> Exchange:
    return Exchange("GET", httpx.URL(url), 200, json.dumps(body).encode())


def test_scrubs_secrets_emails_ips_names_and_ids() -> None:
    scrubber = Scrubber([SECRET], names=["items.*.name"])
    me = exchange("https://h.test/api/me", {"id": 424242, "email": "a@b.ru", "token": SECRET})
    services = exchange(
        "https://h.test/api/services",
        {
            "items": [
                {
                    "id": 7777777,
                    "name": "my-node",
                    "ip": "203.0.113.77",
                    "note": f"key {SECRET} at 203.0.113.77, write to a.b@c.org",
                    "source": "manual:424242",
                    "product": {"id": 337, "name": "CLTs-1"},
                    "payload": {"profileId": "31337", "orderIds": [5555555], "invoiceId": 0},
                }
            ]
        },
    )
    stats = exchange("https://h.test/api/services/7777777/stats?token=" + SECRET, {"data": []})

    me_out, services_out, stats_out = scrubber.exchanges([me, services, stats])

    assert me_out["json"] == {"id": 100001, "email": "***", "token": "***"}
    item = services_out["json"]["items"][0]
    assert item["id"] == 100002
    assert item["name"] == "name-1"
    assert item["ip"] == "192.0.2.10"
    assert item["note"] == "key *** at 192.0.2.10, write to user@example.com"
    assert item["source"] == "manual:100001"
    assert item["product"]["name"] == "CLTs-1"
    assert item["payload"] == {"profileId": "100004", "orderIds": [100005], "invoiceId": 0}
    assert stats_out["url"] == "https://h.test/api/services/100002/stats"
    assert stats_out["query"] == {"token": "***"}


def test_ipv6_and_times() -> None:
    scrubber = Scrubber([])

    assert scrubber.text("at 2a01:4f8:c17:1::1 since 12:30:00") == "at 2001:db8::a since 12:30:00"


def test_non_json_body_is_text() -> None:
    scrubber = Scrubber([SECRET])
    page = Exchange("GET", httpx.URL("https://h.test/"), 502, f"<p>{SECRET}</p>".encode())

    [record] = scrubber.exchanges([page])

    assert record["text"] == "<p>***</p>"
    assert "json" not in record


async def test_recorder_captures_responses() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    recorder = Recorder()
    client = recorder.attach(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    async with client:
        await client.get("https://h.test/a?x=1")

    [captured] = recorder.exchanges
    assert captured.url == httpx.URL("https://h.test/a?x=1")
    assert json.loads(captured.body) == {"ok": True}


def test_write_fixtures_replaces_old_files(tmp_path: Path) -> None:
    (tmp_path / "99-old.json").write_text("{}")
    record = {"method": "GET", "url": "https://h.test/api/v2/accounts/me", "status": 200}

    [path] = write_fixtures([record], tmp_path)

    assert path.name == "01-get-api_v2_accounts_me.json"
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]
    assert json.loads(path.read_text(encoding="utf-8")) == record


def test_account_sums_become_made_up_numbers_of_the_same_type() -> None:
    scrubber = Scrubber([])
    answer = {
        "balance": 4810,
        "items": [
            {"amount": -487.79999999999995, "bonusAmount": 0, "price": 593},
            {"amount": -4810, "total_paid": "12.30", "hourly_cost": 2.0, "is_paid": True},
        ],
        "finances": {"totalReplenished": 63071.575, "permittedDebt": None},
    }

    clean = scrubber.value(answer, ())

    assert clean == {
        "balance": 1000,
        "items": [
            {"amount": -1125.5, "bonusAmount": 0, "price": 593},
            {"amount": -1000, "total_paid": "1250.00", "hourly_cost": 1375.0, "is_paid": True},
        ],
        "finances": {"totalReplenished": 1500.5, "permittedDebt": None},
    }
