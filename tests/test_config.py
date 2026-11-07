from pathlib import Path

import pytest

from hostwatch.core.config import ConfigError, load_settings
from hostwatch.core.secrets import generate_key

ALL_VARS = (
    "BOT_TOKEN",
    "OWNER_ID",
    "HOSTWATCH_KEY",
    "HOSTWATCH_DOMAIN",
    "TG_PROXY",
    "HEARTBEAT_URL",
    "DATA_DIR",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)


def set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:test-token")
    monkeypatch.setenv("OWNER_ID", "42")
    monkeypatch.setenv("HOSTWATCH_KEY", generate_key())


def test_loads_required(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    settings = load_settings(env_file=None)

    assert settings.owner_id == 42
    assert len(settings.encryption_key) == 32
    assert settings.data_dir == Path("/data")
    assert settings.tg_proxy is None


def test_fails_without_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.delenv("BOT_TOKEN")

    with pytest.raises(ConfigError, match="missing environment variables: BOT_TOKEN"):
        load_settings(env_file=None)


def test_error_does_not_leak_values(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("HOSTWATCH_KEY", "leaky-secret-value")

    with pytest.raises(ConfigError) as info:
        load_settings(env_file=None)

    assert "HOSTWATCH_KEY" in str(info.value)
    assert "leaky-secret-value" not in str(info.value)


def test_empty_optional_values(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("TG_PROXY", "")
    monkeypatch.setenv("DATA_DIR", "")

    settings = load_settings(env_file=None)

    assert settings.tg_proxy is None
    assert settings.data_dir == Path("/data")
