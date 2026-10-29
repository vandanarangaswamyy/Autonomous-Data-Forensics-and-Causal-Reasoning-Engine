import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from atlas_database.connection import engine_for
from sqlalchemy import Engine, text


def migrate(engine: Engine, revision: str = "head") -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    engine = engine_for(f"sqlite:///{tmp_path / 'fixture.sqlite'}")
    migrate(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def postgres_db() -> Iterator[Engine]:
    url = os.environ.get("ATLAS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set ATLAS_TEST_POSTGRES_URL to run real PostgreSQL integration")
    admin = engine_for(url)
    schema = f"test_{uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = engine_for(url)
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def set_schema(dbapi: object, record: object) -> None:
        import psycopg

        assert isinstance(dbapi, psycopg.Connection)
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
        dbapi.commit()

    try:
        migrate(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
