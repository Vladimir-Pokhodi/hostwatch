"""`hostwatch new-plugin <id>`: a new plugin folder, its test and fixtures from the template.

Run it in a checkout of the repository. It copies

- `hostwatch/plugins/_template/` to `hostwatch/plugins/<id>/`,
- `tests/test_template_hoster.py` to `tests/test_<id>.py`,
- `tests/fixtures/template_hoster/` to `tests/fixtures/<id>/`,

and puts the new id, class and name in place of the template ones. The copy passes its
tests right away; then the example requests are replaced with the hoster's real ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TEMPLATE_ID = "template_hoster"
TEMPLATE_CLASS = "TemplateHoster"
TEMPLATE_NAME = "Template Hoster"
TEMPLATE_PACKAGE = "hostwatch.plugins._template"
PLUGIN_ID = re.compile(r"[a-z][a-z0-9_]*")


class NewPluginFailed(Exception):
    pass


@dataclass(frozen=True)
class Names:
    id: str
    cls: str
    name: str

    def fill(self, text: str) -> str:
        return (
            text.replace(TEMPLATE_PACKAGE, f"hostwatch.plugins.{self.id}")
            .replace(TEMPLATE_CLASS, self.cls)
            .replace(TEMPLATE_NAME, self.name)
            .replace(TEMPLATE_ID, self.id)
        )


def names_for(plugin_id: str, name: str | None) -> Names:
    if not PLUGIN_ID.fullmatch(plugin_id) or plugin_id == TEMPLATE_ID:
        raise NewPluginFailed(
            f"bad plugin id {plugin_id!r}: use lowercase letters, digits and _, "
            "starting with a letter (for example `firstbyte`)"
        )
    words = [w for w in plugin_id.split("_") if w]
    return Names(
        id=plugin_id,
        cls="".join(w.capitalize() for w in words),
        name=name or " ".join(w.capitalize() for w in words),
    )


def create(root: Path, plugin_id: str, name: str | None = None) -> list[Path]:
    """Write the new files under the repository `root` and return their paths."""
    names = names_for(plugin_id, name)
    plugins = root / "hostwatch" / "plugins"
    tests = root / "tests"
    copies = {
        plugins / "_template": plugins / plugin_id,
        tests / f"test_{TEMPLATE_ID}.py": tests / f"test_{plugin_id}.py",
        tests / "fixtures" / TEMPLATE_ID: tests / "fixtures" / plugin_id,
    }
    for source, target in copies.items():
        if not source.exists():
            raise NewPluginFailed(f"{source} not found; run this in the hostwatch repository")
        if target.exists():
            raise NewPluginFailed(f"{target} already exists")
    written: list[Path] = []
    for source, target in copies.items():
        files = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in files:
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            out = target / path.relative_to(source) if source.is_dir() else target
            out.parent.mkdir(parents=True, exist_ok=True)
            text = path.read_text(encoding="utf-8")
            out.write_text(names.fill(text), encoding="utf-8", newline="\n")
            written.append(out)
    return written
