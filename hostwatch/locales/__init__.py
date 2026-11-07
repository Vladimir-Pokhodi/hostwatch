from hostwatch.locales import en, ru

LANGUAGES: dict[str, dict[str, str]] = {"ru": ru.MESSAGES, "en": en.MESSAGES}


def t(key: str, lang: str, **kwargs: object) -> str:
    """Translate `key` into `lang`. An unknown language or key raises, so typos fail loudly."""
    try:
        messages = LANGUAGES[lang]
    except KeyError:
        raise ValueError(f"unknown language: {lang}") from None
    return messages[key].format(**kwargs)
