import string

import pytest

from hostwatch.locales import LANGUAGES, en, ru, t


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_ru_and_en_have_same_keys() -> None:
    assert set(ru.MESSAGES) == set(en.MESSAGES)


@pytest.mark.parametrize("lang", sorted(LANGUAGES))
def test_placeholders_match_english(lang: str) -> None:
    for key, text in LANGUAGES[lang].items():
        assert placeholders(text) == placeholders(en.MESSAGES[key]), key


def test_t_formats() -> None:
    assert t("greeting", "en", name="Vova") == "Hi, Vova! I keep an eye on your servers."


def test_t_unknown_language() -> None:
    with pytest.raises(ValueError):
        t("greeting", "de", name="x")


def test_t_unknown_key() -> None:
    with pytest.raises(KeyError):
        t("no.such.key", "ru")
