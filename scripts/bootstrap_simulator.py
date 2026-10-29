"""Create the dedicated local fixture DB and least-privilege observation login.

Reads local Compose credentials without printing secrets. Does not touch API storage.
"""

import argparse
import os
import secrets
from pathlib import Path

import psycopg
from atlas_database import schema as s
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy.engine import URL


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grant-reader", action="store_true", help="Run after Alembic upgrade")
    args = parser.parse_args()
    env_path = Path(".env")
    values = {**dotenv_values(env_path), **os.environ}
    host = "127.0.0.1"
    port = int(values.get("POSTGRES_PORT") or "55432")
    user = values.get("POSTGRES_USER") or "atlas"
    password = values.get("POSTGRES_PASSWORD")
    if not password:
        raise ValueError("Set POSTGRES_PASSWORD in .env")
    database = "atlas_simulator"
    role = "atlas_observer"
    if args.grant_reader:
        with psycopg.connect(
            dbname=database, host=host, port=port, user=user, password=password
        ) as connection:
            connection.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
            )
            connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
            )
            connection.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(
                    sql.Identifier(role)
                )
            )
            for table in s.BUSINESS_TABLES:
                connection.execute(
                    sql.SQL("GRANT SELECT ON {} TO {}").format(
                        sql.Identifier(table.name), sql.Identifier(role)
                    )
                )
        print("Observer granted SELECT on business and pipeline evidence tables only.")
        return
    reader_password = values.get("ATLAS_SIM_READER_PASSWORD") or secrets.token_urlsafe(32)
    with psycopg.connect(
        dbname="postgres", autocommit=True, host=host, port=port, user=user, password=password
    ) as connection:
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            # Password literal uses psycopg composition rather than shell interpolation.
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                ).format(sql.Identifier(role), sql.Literal(reader_password))
            )
        elif not values.get("ATLAS_SIM_READER_PASSWORD"):
            raise ValueError("Observer already exists; supply its password in the private .env")
    owner_url = URL.create(
        "postgresql+psycopg",
        database=database,
        username=user,
        password=password,
        host=host,
        port=port,
    ).render_as_string(hide_password=False)
    reader_url = URL.create(
        "postgresql+psycopg",
        username=role,
        password=reader_password,
        host=host,
        port=port,
        database=database,
    ).render_as_string(hide_password=False)
    updates = {
        "ATLAS_SIM_DATABASE_URL": owner_url,
        "ATLAS_SIM_READER_PASSWORD": reader_password,
        "ATLAS_SIM_READER_URL": reader_url,
    }
    original = env_path.read_text() if env_path.exists() else ""
    retained = [line for line in original.splitlines() if line.split("=", 1)[0] not in updates]
    content = (
        "\n".join(retained)
        + "\n"
        + "\n".join(f"{key}={value}" for key, value in updates.items())
        + "\n"
    )
    env_path.write_text(content)
    env_path.chmod(0o600)
    print("Dedicated database and observer created; private connection settings saved in .env.")


if __name__ == "__main__":
    main()
