"""Run live integrations or the full suite without shell-expanding private settings."""

import argparse
import os
import subprocess

from dotenv import dotenv_values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all", action="store_true", help="Run all tests with live integrations enabled"
    )
    args = parser.parse_args()
    values = dotenv_values(".env")
    url = values.get("ATLAS_SIM_DATABASE_URL")
    reader = values.get("ATLAS_SIM_READER_URL")
    if not url or not reader:
        raise SystemExit("Run bootstrap_simulator.py and --grant-reader first")
    environment = {
        **os.environ,
        "ATLAS_TEST_POSTGRES_URL": url,
        "ATLAS_TEST_READER_URL": reader,
        "ATLAS_TEST_KAFKA_BOOTSTRAP": values.get("ATLAS_SIM_KAFKA_BOOTSTRAP") or "127.0.0.1:9092",
    }
    path = "tests" if args.all else "tests/integration"
    raise SystemExit(
        subprocess.call([".venv/bin/pytest", path, "-q" if args.all else "-v"], env=environment)
    )


if __name__ == "__main__":
    main()
