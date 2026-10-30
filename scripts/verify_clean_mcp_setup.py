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
from typing import Any
from uuid import uuid4


def verify_demo_contents(payload: dict[str, Any]) -> None:
    """Check actual nine-tool content, rather than a process exit or status alone."""
    responses = payload["responses"]
    expected = {
        "analytics.get_table_schema",
        "analytics.execute_readonly_sql",
        "analytics.profile_column",
        "analytics.get_query_plan",
        "lineage.trace_upstream_lineage",
        "lineage.trace_downstream_lineage",
        "telemetry.get_pipeline_execution",
        "telemetry.search_incident_history",
        "knowledge.search_technical_documents",
    }
    assert set(payload["tools"]) == set(responses) == expected
    request_ids, trace_ids = set(), set()
    for reply in responses.values():
        assert reply["status"] == "ok" and reply["error"] is None
        assert reply["investigation_id"] == payload["investigation_id"]
        request_ids.add(reply["request_id"])
        trace_ids.add(reply["trace_id"])
    assert len(request_ids) == len(trace_ids) == 9
    results = {name: reply["result"] for name, reply in responses.items()}
    schema = results["analytics.get_table_schema"]
    assert schema["reference"] == {"schema_name": "public", "table": "orders"}
    assert {c["name"] for c in schema["columns"]} >= {"id", "account_id", "total", "currency"}
    assert schema["primary_key"] == ["id"] and schema["indexes"]
    query = results["analytics.execute_readonly_sql"]
    assert query["columns"] == ["currency", "orders"] and not query["truncated"]
    assert query["row_count"] == len(query["rows"]) > 0
    assert all(
        len(row) == 2 and isinstance(row[0], str) and int(row[1]) > 0 for row in query["rows"]
    )
    profile = results["analytics.profile_column"]
    assert profile["column"] == "total" and profile["total_rows"] > 0
    assert profile["null_rows"] == 0 and profile["numeric_mean"] is not None
    assert float(profile["numeric_mean"]) > 0
    assert sum(int(row[1]) for row in query["rows"]) == profile["total_rows"]
    plan = results["analytics.get_query_plan"]
    assert plan["dialect"] == "postgres" and plan["executes_query"] is False
    assert plan["plan"][0]["Plan"]["Node Type"]
    for name, source, destination in (
        ("lineage.trace_upstream_lineage", "staged_payments", "daily_financials"),
        ("lineage.trace_downstream_lineage", "payment_events", "staged_payments"),
    ):
        lineage = results[name]
        assert not lineage["truncated"] and lineage["edges"]
        assert any(
            edge["source_dataset"] == source and edge["destination_dataset"] == destination
            for edge in lineage["edges"]
        )
        assert all(
            edge["source_column"] and edge["destination_column"] and edge["expression"]
            for edge in lineage["edges"]
        )
    executions = results["telemetry.get_pipeline_execution"]["executions"]
    assert executions and all(row["pipeline_id"] == "ingest" for row in executions)
    assert all(
        row["status"] in {"success", "degraded"} and row["output_rows"] > 0 and row["quality"]
        for row in executions
    )
    incidents = results["telemetry.search_incident_history"]["incidents"]
    assert incidents and all("source" in row["code"].lower() for row in incidents)
    documents = results["knowledge.search_technical_documents"]
    assert documents["retrieval_method"] == "keyword" and documents["matches"]
    assert any(row["document_id"] == "payment-ingestion" for row in documents["matches"])
    assert all(
        row["source_uri"].startswith("atlas-public://technical/")
        and row["excerpt"]
        and row["score"] > 0
        for row in documents["matches"]
    )


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
                    verify_demo_contents(payload)
                print(f"fresh checkout: make {target} passed", flush=True)
        finally:
            if started:
                subprocess.run(
                    ["docker", "rm", "--force", "--volumes", name], check=True, capture_output=True
                )
                print("Disposable PostgreSQL container and volume removed", flush=True)


if __name__ == "__main__":
    main()
