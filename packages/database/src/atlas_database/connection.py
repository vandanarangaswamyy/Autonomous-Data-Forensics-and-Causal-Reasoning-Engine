from sqlalchemy import Connection as SAConnection
from sqlalchemy import Engine, create_engine, event


def engine_for(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(connection: object, record: object) -> None:
            # DBAPI connection is intentionally kept behind this infrastructure adapter.
            from sqlite3 import Connection

            assert isinstance(connection, Connection)
            connection.isolation_level = None
            connection.execute("PRAGMA foreign_keys=ON")

        @event.listens_for(engine, "begin")
        def sqlite_begin(connection: SAConnection) -> None:
            connection.exec_driver_sql("BEGIN")

    return engine
