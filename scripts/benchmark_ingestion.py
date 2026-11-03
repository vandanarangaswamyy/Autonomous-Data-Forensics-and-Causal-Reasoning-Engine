"""Actual PostgreSQL source/chunk throughput. Not a retrieval-quality benchmark."""

import asyncio
import hashlib
import json
import platform
import statistics
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from atlas_contracts.documents import (
    ChunkConfig,
    DocumentKind,
    SourceDescriptor,
    SourceSubmission,
)
from atlas_database import documents_schema as s
from atlas_database.documents import PostgreSQLDocumentStore
from atlas_documents.settings import DocumentSettings
from atlas_documents.worker import process_one
from sqlalchemy import Connection, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine


def migrate(connection: Connection) -> None:
    config = Config("knowledge-alembic.ini")
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def benchmark() -> dict[str, object]:
    settings = DocumentSettings()
    admin = settings.engine()
    sources = [
        (
            f"# Source {i}\n## Retail\n" + "Table public.orders stores account payments. "
            "Pipeline ingest consumes payment_events. "
            f"Incident INC-{i:04d} describes service payments in schema v1.\n" * 16
        ).encode()
        for i in range(100)
    ]
    results: list[dict[str, object]] = []
    try:
        async with admin.connect() as c:
            database_version = (await c.execute(text("SHOW server_version"))).scalar_one()
        for strategy in ("recursive", "markdown"):
            schema = "ingestion_benchmark_" + uuid4().hex
            async with admin.begin() as c:
                await c.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(
                settings.database_url.get_secret_value(),
                connect_args={"options": f"-csearch_path={schema}", "connect_timeout": 3},
            )
            try:
                async with engine.begin() as c:
                    await c.run_sync(migrate)
                store = PostgreSQLDocumentStore(engine)
                latencies = []
                started = time.perf_counter()
                for i, content in enumerate(sources):
                    before = time.perf_counter()
                    receipt = await store.submit(
                        SourceSubmission(
                            descriptor=SourceDescriptor(
                                collection="ingestion-benchmark",
                                source_uri=f"atlas://benchmark/doc-{i}",
                                source_version="fixture-v1",
                                kind=list(DocumentKind)[i % len(DocumentKind)],
                                media_type="text/markdown",
                                observed_at=datetime(2025, 1, 1, tzinfo=UTC),
                            ),
                            content=content,
                            chunking=ChunkConfig(strategy=strategy, max_chars=512),
                        )
                    )
                    assert await process_one(store)
                    assert (await store.status(receipt.job_id))["status"] == "succeeded"
                    latencies.append((time.perf_counter() - before) * 1000)
                elapsed = time.perf_counter() - started
                async with store.transaction() as c:
                    count = (
                        await c.execute(select(func.count()).select_from(s.chunks))
                    ).scalar_one()
                results.append(
                    {
                        "strategy": strategy,
                        "max_chars": 512,
                        "documents": len(sources),
                        "chunks": count,
                        "elapsed_seconds": elapsed,
                        "documents_per_second": len(sources) / elapsed,
                        "chunks_per_second": count / elapsed,
                        "end_to_end_ms_p50": statistics.median(latencies),
                        "end_to_end_ms_p95": sorted(latencies)[94],
                    }
                )
            finally:
                await engine.dispose()
                async with admin.begin() as c:
                    await c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        return {
            "benchmark": "ingestion-v1",
            "dataset_sha256": hashlib.sha256(b"".join(sources)).hexdigest(),
            "source_bytes": sum(len(source) for source in sources),
            "algorithm": "offset-v1",
            "recorded_at": datetime.now(UTC).isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "postgresql": database_version,
            "sqlalchemy": version("sqlalchemy"),
            "psycopg": version("psycopg"),
            "models": [],
            "workers": 1,
            "results": results,
            "scope": (
                "Local PostgreSQL submit + segment + commit + status; "
                "excludes migration/setup. No retrieval or NLP metrics."
            ),
        }
    finally:
        await admin.dispose()


def main() -> None:
    result = asyncio.run(benchmark())
    path = Path("evaluation/results/ingestion-v1.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
