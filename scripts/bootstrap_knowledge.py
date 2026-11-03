"""Provision dedicated local knowledge storage; never reuse simulator observer credentials."""

import os
import secrets
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy.engine import URL

from scripts.bootstrap_simulator import write_private_env


def main() -> None:
    path = Path(".env")
    values = {**dotenv_values(path), **os.environ}
    password = values.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("Set local Compose POSTGRES_PASSWORD in private .env")
    role, database = "atlas_knowledge_owner", "atlas_knowledge"
    owner_password = values.get("ATLAS_KNOWLEDGE_OWNER_PASSWORD") or secrets.token_urlsafe(32)
    host, port = "127.0.0.1", int(values.get("POSTGRES_PORT") or "55432")
    with psycopg.connect(
        dbname="postgres",
        user=values.get("POSTGRES_USER") or "atlas",
        password=password,
        host=host,
        port=port,
        autocommit=True,
    ) as c:
        existing = c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if existing and not values.get("ATLAS_KNOWLEDGE_OWNER_PASSWORD"):
            raise SystemExit(
                "Knowledge owner exists; restore its private password before bootstrap"
            )
        if not existing:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                ).format(sql.Identifier(role), sql.Literal(owner_password))
            )
        row = c.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if row and row[0] != role:
            raise SystemExit("Refusing to reuse a database owned by a different role")
        if not row:
            c.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
    url = URL.create(
        "postgresql+psycopg",
        username=role,
        password=owner_password,
        host=host,
        port=port,
        database=database,
    ).render_as_string(hide_password=False)
    updates = {
        "ATLAS_KNOWLEDGE_DATABASE_URL": url,
        "ATLAS_KNOWLEDGE_OWNER_PASSWORD": owner_password,
    }
    original = path.read_text() if path.exists() else ""
    retained = [line for line in original.splitlines() if line.split("=", 1)[0] not in updates]
    write_private_env(
        path,
        "\n".join(retained)
        + "\n"
        + "\n".join(f"{key}={value}" for key, value in updates.items())
        + "\n",
    )
    print(
        "Dedicated knowledge DB/owner provisioned; private settings saved without printing secrets."
    )


if __name__ == "__main__":
    try:
        main()
    except psycopg.Error:
        raise SystemExit(
            "Knowledge bootstrap failed; check local PostgreSQL provisioning"
        ) from None
