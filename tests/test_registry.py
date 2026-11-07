import logging
import textwrap
from pathlib import Path

import pytest

from hostwatch.core.registry import UnknownPlugin, discover
from hostwatch.plugins.fake import FakePlugin

LOCAL_PLUGIN = """
from hostwatch.core.models import Balance, Money
from hostwatch.core.plugin import ApiKeyAuth, Plugin, PluginMeta

from .helpers import ZERO


class Mine(Plugin):
    meta = PluginMeta(
        id="mine", name="Mine", site="https://mine.invalid",
        auth=(ApiKeyAuth(fields=("key",)),), currency="USD",
    )

    async def servers(self):
        return []

    async def balance(self):
        return Balance(money=Money.of(ZERO, "USD"))
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_finds_builtin_fake() -> None:
    registry = discover()

    assert registry.get("fake") is FakePlugin
    assert "fake" in registry


def test_unknown_plugin() -> None:
    with pytest.raises(UnknownPlugin):
        discover().get("nope")


def test_loads_local_plugin_with_relative_import(tmp_path: Path) -> None:
    write(tmp_path / "mine" / "__init__.py", LOCAL_PLUGIN)
    write(tmp_path / "mine" / "helpers.py", 'ZERO = "0"\n')

    registry = discover(tmp_path)

    assert registry.ids() == ["fake", "mine"]


def test_broken_local_plugin_is_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    write(tmp_path / "broken" / "__init__.py", "raise RuntimeError('boom')\n")
    write(tmp_path / "_template" / "__init__.py", "raise RuntimeError('never imported')\n")

    with caplog.at_level(logging.ERROR):
        registry = discover(tmp_path)

    assert registry.ids() == ["fake"]
    assert "broken" in caplog.text
    assert "_template" not in caplog.text


def test_local_plugin_id_must_match_folder(tmp_path: Path) -> None:
    write(
        tmp_path / "other" / "__init__.py", LOCAL_PLUGIN.replace("from .helpers", "ZERO = '0'\n#")
    )

    assert discover(tmp_path).ids() == ["fake"]


def test_local_plugin_cannot_replace_builtin(tmp_path: Path) -> None:
    text = LOCAL_PLUGIN.replace('id="mine"', 'id="fake"').replace("from .helpers", "ZERO = '0'\n#")
    write(tmp_path / "fake" / "__init__.py", text)

    assert discover(tmp_path).get("fake") is FakePlugin
