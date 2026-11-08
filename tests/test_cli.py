from pathlib import Path

import pytest
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.methods import GetMe

from hostwatch.__main__ import main
from hostwatch.core.secrets import generate_key


def test_check_fake_prints_everything(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "fake"]) == 0

    out = capsys.readouterr().out
    assert "Fake hoster (fake)" in out
    assert "Response time: servers" in out
    assert "Balance: €20.00" in out
    caps = "autorenew, invoices, order, pay_invoice, power, renew, root_password, topup, usage"
    assert f"Capabilities: {caps}" in out
    assert "Servers: 3" in out
    assert "beaver [1] vps running" in out
    assert "Forecast:" in out
    assert "short for beaver" in out


def test_check_fake_with_key_and_russian(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "fake", "--key", "balance=100", "--lang", "ru"]) == 0

    out = capsys.readouterr().out
    assert "Balance: €100.00" in out
    assert "Метод: будущие списания" in out


def test_check_reports_plugin_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "fake", "--key", "fail=auth_expired"]) == 1

    assert "servers: auth_expired" in capsys.readouterr().err


def test_check_unknown_target_without_database(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.chdir("/")

    assert main(["check", "nope"]) == 1

    assert "'nope' is not a plugin" in capsys.readouterr().err


def test_bot_with_rejected_token_says_so(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123456:not-a-real-token")
    monkeypatch.setenv("OWNER_ID", "1")
    monkeypatch.setenv("HOSTWATCH_KEY", generate_key())

    async def rejected(_: object) -> None:
        raise TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")

    monkeypatch.setattr("hostwatch.bot.app.run", rejected)

    assert main([]) == 1

    err = capsys.readouterr().err
    assert "Telegram does not accept BOT_TOKEN" in err
    assert "not-a-real-token" not in err
