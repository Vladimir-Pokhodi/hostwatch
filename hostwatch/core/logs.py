"""Logging to stdout with every loaded account secret replaced by `***`."""

import logging
import sys
from collections.abc import Iterable
from typing import TextIO

# Shorter values would mangle ordinary words and numbers; real tokens and keys are much longer.
MIN_SECRET_LENGTH = 6
MASK = "***"


class SecretRedactor(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._secrets: set[str] = set()

    def add(self, values: Iterable[str]) -> None:
        self._secrets.update(v for v in values if len(v) >= MIN_SECRET_LENGTH)

    def redact(self, text: str) -> str:
        # Longest first, so a secret that contains another one is masked whole.
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, MASK)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self.redact(record.stack_info)
        return True


REDACTOR = SecretRedactor()


def register_secrets(values: Iterable[str]) -> None:
    REDACTOR.add(values)


def setup_logging(level: int = logging.INFO, stream: TextIO | None = None) -> logging.Handler:
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.addFilter(REDACTOR)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    # httpx logs every full URL at INFO, query string included; core/http.py logs without it.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler
