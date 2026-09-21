import pytest

from mybudongsan.storage.database import Database


@pytest.fixture
def database() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database
