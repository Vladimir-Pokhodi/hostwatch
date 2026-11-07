"""Settings read from the environment and from `.env` in the working directory."""

from pathlib import Path

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hostwatch.core.secrets import load_key


class ConfigError(Exception):
    """A required variable is missing or a variable has an invalid value."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: SecretStr
    owner_id: int
    hostwatch_key: SecretStr
    hostwatch_domain: str | None = None
    tg_proxy: str | None = None
    heartbeat_url: str | None = None
    data_dir: Path = Path("/data")
    plugins_dir: Path | None = None

    @field_validator("hostwatch_key")
    @classmethod
    def _key_is_valid(cls, value: SecretStr) -> SecretStr:
        load_key(value.get_secret_value())
        return value

    @field_validator("hostwatch_domain", "tg_proxy", "heartbeat_url", "plugins_dir", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("data_dir", mode="before")
    @classmethod
    def _empty_is_default(cls, value: object) -> object:
        return "/data" if value == "" else value

    @property
    def encryption_key(self) -> bytes:
        return load_key(self.hostwatch_key.get_secret_value())


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    """Read settings or raise ConfigError naming the bad variables.

    The message never contains values: they may be secrets.
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        raise ConfigError(_describe(exc)) from None


def _describe(exc: ValidationError) -> str:
    missing: list[str] = []
    invalid: list[str] = []
    for error in exc.errors(include_input=False, include_url=False):
        name = str(error["loc"][0]).upper() if error["loc"] else "?"
        if error["type"] == "missing":
            missing.append(name)
        else:
            invalid.append(f"{name}: {error['msg']}")
    lines = []
    if missing:
        lines.append("missing environment variables: " + ", ".join(missing))
    if invalid:
        lines.append("invalid environment variables: " + "; ".join(invalid))
    lines.append("see .env.example")
    return "\n".join(lines)
