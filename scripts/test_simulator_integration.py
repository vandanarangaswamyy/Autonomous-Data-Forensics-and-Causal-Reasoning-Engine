"""Run opt-in integrations using private local settings without shell secret expansion."""

import os
import subprocess

from dotenv import dotenv_values

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
raise SystemExit(subprocess.call([".venv/bin/pytest", "tests/integration", "-v"], env=environment))
