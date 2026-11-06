"""Run actual PostgreSQL ingestion integrations without shell-expanding private settings."""

import argparse
import os
import subprocess

from dotenv import dotenv_values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    values = dotenv_values(".env")
    url = values.get("ATLAS_KNOWLEDGE_DATABASE_URL")
    if not url:
        raise SystemExit("Run make documents-bootstrap and make documents-migrate first")
    environment = {**os.environ, "ATLAS_TEST_KNOWLEDGE_URL": url}
    environment["ATLAS_TEST_PDF_PARSER"] = "1"
    if args.all:
        required = {
            "ATLAS_TEST_POSTGRES_URL": "ATLAS_SIM_DATABASE_URL",
            "ATLAS_TEST_READER_URL": "ATLAS_SIM_READER_URL",
        }
        for target, source in required.items():
            value = values.get(source)
            if not value:
                raise SystemExit("Provision simulator integration settings first")
            environment[target] = value
        environment["ATLAS_TEST_KAFKA_BOOTSTRAP"] = (
            values.get("ATLAS_SIM_KAFKA_BOOTSTRAP") or "127.0.0.1:9092"
        )
    raise SystemExit(
        subprocess.call(
            [
                ".venv/bin/pytest",
                *(
                    ["tests"]
                    if args.all
                    else [
                        "tests/integration/test_documents.py",
                        "tests/integration/test_pdf_runtime.py",
                    ]
                ),
                "-q",
            ],
            env=environment,
        )
    )


if __name__ == "__main__":
    main()
