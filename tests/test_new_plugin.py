import json
import shutil
from pathlib import Path

import httpx
import pytest
import respx

from hostwatch.__main__ import main
from hostwatch.core.plugin import PluginContext, Secrets
from hostwatch.core.registry import discover
from hostwatch.new_plugin import NewPluginFailed, create, names_for
from tests.contract import check_contract
from tests.replay import replay

REPO = Path(__file__).parent.parent


async def ignore(_: Secrets) -> None:
    pass


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The template files at the places they have in the repository."""
    shutil.copytree(
        REPO / "hostwatch" / "plugins" / "_template",
        tmp_path / "hostwatch" / "plugins" / "_template",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy(REPO / "tests" / "test_template_hoster.py", tmp_path / "tests" / ".")
    shutil.copytree(
        REPO / "tests" / "fixtures" / "template_hoster",
        tmp_path / "tests" / "fixtures" / "template_hoster",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def tests_folder(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()


def test_names() -> None:
    names = names_for("first_byte", None)

    assert (names.cls, names.name) == ("FirstByte", "First Byte")
    assert names_for("vdsina", "VDSina.ru").name == "VDSina.ru"


@pytest.mark.parametrize("bad", ["", "1host", "Host", "my-host", "template_hoster"])
def test_bad_id(bad: str) -> None:
    with pytest.raises(NewPluginFailed):
        names_for(bad, None)


async def test_new_plugin_loads_and_passes_contract(repo: Path) -> None:
    written = create(repo, "fifth_host")

    plugin_file = repo / "hostwatch" / "plugins" / "fifth_host" / "__init__.py"
    test_file = repo / "tests" / "test_fifth_host.py"
    assert {plugin_file, test_file} <= set(written)
    source = plugin_file.read_text(encoding="utf-8")
    assert 'id="fifth_host"' in source and "class FifthHostPlugin" in source
    assert 'name="Fifth Host"' in source
    tests = test_file.read_text(encoding="utf-8")
    assert "from hostwatch.plugins.fifth_host import BASE_URL, FifthHostPlugin" in tests
    assert 'load("fifth_host")' in tests
    assert "template" not in (source + tests).lower()

    plugin_cls = discover(repo / "hostwatch" / "plugins").get("fifth_host")
    fixtures = sorted((repo / "tests" / "fixtures" / "fifth_host").glob("*.json"))
    records = [json.loads(path.read_text(encoding="utf-8")) for path in fixtures]
    with respx.mock(assert_all_called=False) as router:
        replay(router, records)
        await check_contract(
            plugin_cls(PluginContext({"api_key": "k"}, httpx.AsyncClient(), ignore))
        )


def test_existing_plugin_is_not_overwritten(repo: Path) -> None:
    create(repo, "fifth_host")

    with pytest.raises(NewPluginFailed, match="already exists"):
        create(repo, "fifth_host")


def test_cli_outside_the_repository(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new-plugin", "fifth_host", "--root", str(tmp_path)]) == 1

    assert "run this in the hostwatch repository" in capsys.readouterr().err


def test_cli_writes_files(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new-plugin", "fifth_host", "--name", "Fifth", "--root", str(repo)]) == 0

    assert "test_fifth_host.py" in capsys.readouterr().out
