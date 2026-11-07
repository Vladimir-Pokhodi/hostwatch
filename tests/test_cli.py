import pytest

from hostwatch.__main__ import main


def test_check_fake_prints_everything(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "fake"]) == 0

    out = capsys.readouterr().out
    assert "Fake hoster (fake)" in out
    assert "Response time: servers" in out
    assert "Balance: €20.00" in out
    assert (
        "Capabilities: autorenew, invoices, order, pay_invoice, power, renew, topup, usage" in out
    )
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
