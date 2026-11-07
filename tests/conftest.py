import sqlite3
from collections.abc import Iterator

import pytest

pytest.register_assert_rewrite("tests.contract")

from hostwatch.core.secrets import generate_key, load_key  # noqa: E402
from hostwatch.storage.db import connect  # noqa: E402
from hostwatch.storage.repositories import AccountRepository  # noqa: E402


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def key() -> bytes:
    return load_key(generate_key())


@pytest.fixture
def accounts(conn: sqlite3.Connection, key: bytes) -> AccountRepository:
    return AccountRepository(conn, key)
