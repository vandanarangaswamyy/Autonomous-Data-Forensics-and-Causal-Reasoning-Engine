"""Reproduce documented setup in a temporary checkout and disposable PostgreSQL container.

Requires Docker and uv. Existing containers, volumes, .env and fixture state are untouched.
"""

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    name = "atlas-review-" + uuid4().hex
    password = secrets.token_urlsafe(32)
    started = False
    with tempfile.TemporaryDirectory(prefix="atlas-clean-review-") as temporary:
        checkout = Path(temporary) / "atlas"
        checkout.mkdir()
        paths = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=source
            )
            .decode()
            .split("\0")
        )
        for relative in set(paths) - {""}:
            original = source / relative
            if not original.is_file():
                continue
            copy_target = checkout / relative
            copy_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, copy_target)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ATLAS_", "POSTGRES_", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"))
        }
        environment["POSTGRES_PASSWORD"] = password
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--env",
                    "POSTGRES_USER=atlas",
                    "--env",
                    "POSTGRES_PASSWORD",
                    "--env",
                    "POSTGRES_DB=atlas",
                    "--publish",
                    "127.0.0.1::5432",
                    "pgvector/pgvector:pg17",
                ],
                env=environment,
                check=True,
                capture_output=True,
            )
            started = True
            port = (
                subprocess.check_output(["docker", "port", name, "5432/tcp"], text=True)
                .strip()
                .rsplit(":", 1)[1]
            )
            for _ in range(60):
                ready = subprocess.run(
                    ["docker", "exec", name, "pg_isready", "-U", "atlas"], capture_output=True
                )
                if ready.returncode == 0:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Disposable PostgreSQL did not become ready")
            # Keep credentials in private fixture config, not subprocess command arguments.
            (checkout / ".env").write_text(
                f"POSTGRES_USER=atlas\nPOSTGRES_PASSWORD={password}\nPOSTGRES_PORT={port}\n"
            )
            (checkout / ".env").chmod(0o600)
            environment.pop("POSTGRES_PASSWORD")
            for target in (
                "setup",
                "sim-bootstrap",
                "sim-migrate",
                "sim-generate",
                "mcp-demo",
                "sim-reset",
            ):
                result = subprocess.run(
                    ["make", target],
                    cwd=checkout,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode:
                    # Never print raw subprocess failures that could contain a DSN/credential.
                    raise RuntimeError(
                        f"Fresh-checkout make {target} failed with status {result.returncode}"
                    )
                if target == "mcp-demo":
                    payload = json.loads(result.stdout[result.stdout.index("{") :])
                    assert len(payload["responses"]) == 9
                    assert all(reply["status"] == "ok" for reply in payload["responses"].values())
                print(f"fresh checkout: make {target} passed", flush=True)
        finally:
            if started:
                subprocess.run(
                    ["docker", "rm", "--force", "--volumes", name], check=True, capture_output=True
                )
                print("Disposable PostgreSQL container and volume removed", flush=True)


if __name__ == "__main__":
    main()
