"""Finds plugins: subpackages of `hostwatch.plugins` and folders in PLUGINS_DIR."""

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import hostwatch.plugins
from hostwatch.core.plugin import Plugin

log = logging.getLogger(__name__)

LOCAL_PACKAGE = "hostwatch_local_plugins"


class UnknownPlugin(KeyError):
    pass


class Registry:
    def __init__(self, plugins: dict[str, type[Plugin]]) -> None:
        self._plugins = dict(plugins)

    def get(self, plugin_id: str) -> type[Plugin]:
        try:
            return self._plugins[plugin_id]
        except KeyError:
            raise UnknownPlugin(plugin_id) from None

    def __contains__(self, plugin_id: object) -> bool:
        return plugin_id in self._plugins

    def ids(self) -> list[str]:
        return sorted(self._plugins)


def discover(plugins_dir: Path | None = None) -> Registry:
    """A plugin that fails to import is logged and skipped; the others still load."""
    found: dict[str, type[Plugin]] = {}
    for module in _builtin_modules():
        _add(found, module)
    if plugins_dir is not None:
        for module in _local_modules(plugins_dir):
            _add(found, module)
    return Registry(found)


def _builtin_modules() -> Iterator[ModuleType]:
    for info in pkgutil.iter_modules(hostwatch.plugins.__path__):
        if not info.ispkg or info.name.startswith("_"):
            continue
        name = f"{hostwatch.plugins.__name__}.{info.name}"
        try:
            yield importlib.import_module(name)
        except Exception:
            log.exception("plugin %s failed to import", info.name)


def _local_modules(plugins_dir: Path) -> Iterator[ModuleType]:
    if not plugins_dir.is_dir():
        log.warning("PLUGINS_DIR %s is not a directory", plugins_dir)
        return
    for path in sorted(plugins_dir.iterdir()):
        init = path / "__init__.py"
        if path.name.startswith(("_", ".")) or not init.is_file():
            continue
        name = f"{LOCAL_PACKAGE}.{path.name}"
        try:
            yield _import_from(name, init)
        except Exception:
            log.exception("local plugin %s failed to import", path.name)


def _import_from(name: str, init: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        name, init, submodule_search_locations=[str(init.parent)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {init}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def _add(found: dict[str, type[Plugin]], module: ModuleType) -> None:
    folder = module.__name__.rsplit(".", 1)[-1]
    classes = [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj)
        and issubclass(obj, Plugin)
        and not inspect.isabstract(obj)
        and _defined_in(obj, module)
    ]
    if len(classes) != 1:
        log.error(
            "plugin %s must define exactly one Plugin subclass, found %d", folder, len(classes)
        )
        return
    cls = classes[0]
    if cls.meta.id != folder:
        log.error("plugin folder %s has meta.id %r; they must match", folder, cls.meta.id)
        return
    if cls.meta.id in found:
        log.error("plugin %s is already loaded; skipping the duplicate", cls.meta.id)
        return
    found[cls.meta.id] = cls


def _defined_in(cls: type, module: ModuleType) -> bool:
    return cls.__module__ == module.__name__ or cls.__module__.startswith(module.__name__ + ".")
