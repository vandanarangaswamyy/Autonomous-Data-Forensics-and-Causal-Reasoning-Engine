"""Actual PostgreSQL integrations. No document store/queue driver doubles."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from atlas_contracts.documents import (
    Chunk,
    ChunkConfig,
    DocumentKind,
    IngestionError,
    SourceDescriptor,
    SourceSubmission,
)
from atlas_core.documents import DocumentStore
from atlas_database import documents_schema as s
from atlas_database.documents import PostgreSQLDocumentStore
from atlas_documents.chunking import decode, segment
from atlas_documents.worker import process_one
from sqlalchemy import Connection, func, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


def configuration() -> Config:
    return Config(str(Path(__file__).resolve().parents[2] / "knowledge-alembic.ini"))


def migrate(connection: Connection) -> None:
    config = configuration()
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


@pytest.fixture
async def store() -> AsyncIterator[PostgreSQLDocumentStore]:
    url = os.environ.get("ATLAS_TEST_KNOWLEDGE_URL")
    if not url:
        pytest.skip("Set ATLAS_TEST_KNOWLEDGE_URL for actual PostgreSQL ingestion tests")
    admin = create_async_engine(url)
    schema = "ingestion_test_" + uuid4().hex
    async with admin.begin() as c:
        await c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        async with engine.begin() as c:
            await c.run_sync(migrate)
        yield PostgreSQLDocumentStore(engine)
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def submission(
    value: bytes = b"# Orders\nSchema and payment lineage.\n", uri: str = "atlas://docs/orders"
) -> SourceSubmission:
    return SourceSubmission(
        descriptor=SourceDescriptor(
            collection="retail",
            source_uri=uri,
            source_version="v1",
            kind=DocumentKind.TECHNICAL,
            media_type="text/markdown",
            observed_at=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        content=value,
        chunking=ChunkConfig(strategy="markdown"),
    )


async def test_real_migrations_match_and_roundtrip(store: PostgreSQLDocumentStore) -> None:
    assert isinstance(store, DocumentStore)
    async with store.engine.begin() as c:
        diff = await c.run_sync(
            lambda connection: compare_metadata(MigrationContext.configure(connection), s.metadata)
        )
        assert diff == []
        config = configuration()

        def roundtrip(connection: Connection) -> None:
            config.attributes["connection"] = connection
            command.downgrade(config, "base")
            command.upgrade(config, "head")

        await c.run_sync(roundtrip)


async def test_concurrent_dedup_content_versions_and_processing(
    store: PostgreSQLDocumentStore,
) -> None:
    request = submission()
    receipts = await asyncio.gather(*(store.submit(request) for _ in range(6)))
    assert len({r.document_id for r in receipts}) == len({r.job_id for r in receipts}) == 1
    assert sum(not r.deduplicated for r in receipts) == 1
    assert await process_one(store)
    assert not await process_one(store)
    async with store.transaction() as c:
        chunks = (await c.execute(select(s.chunks))).mappings().all()
        assert "".join(row["text"] for row in sorted(chunks, key=lambda r: r["start"])) == decode(
            request.content
        )
        assert all(row["content_sha256"] == request.content_sha256 for row in chunks)
        assert (await c.execute(select(s.blobs.c.content))).scalar_one() == request.content
    updated = await store.submit(submission(b"# Orders\nChanged total column.\n"))
    assert updated.document_id == receipts[0].document_id and updated.version == 2
    assert await process_one(store)
    assert (await store.status(updated.job_id))["status"] == "succeeded"
    # Identical bytes from a different source share blobs, never document identity/provenance.
    other = await store.submit(submission(uri="atlas://docs/other"))
    assert other.document_id != updated.document_id
    async with store.transaction() as c:
        assert (await c.execute(select(func.count()).select_from(s.blobs))).scalar_one() == 2
        assert (await c.execute(select(func.count()).select_from(s.versions))).scalar_one() == 3


async def test_update_delete_and_replay_fence_stale_workers(store: PostgreSQLDocumentStore) -> None:
    receipt = await store.submit(submission())
    old = await store.claim()
    assert old
    second = await store.submit(submission(b"# Orders\nNew revision\n"))
    original_chunks = segment(
        decode(old.content), old.document_id, old.version, submission().content_sha256, old.chunking
    )
    assert not await store.complete(old, original_chunks)
    assert (await store.status(receipt.job_id))["status"] == "superseded"
    assert await process_one(store)
    rebuild = await store.reprocess(second.document_id)
    assert rebuild != second.job_id
    assert await process_one(store)
    assert (await store.status(rebuild))["status"] == "succeeded"
    leased_id = await store.reprocess(second.document_id)
    leased = await store.claim()
    assert leased
    assert await store.delete(second.document_id)
    assert not await store.delete(second.document_id)
    assert not await store.complete(leased, [])
    assert (await store.status(leased_id))["status"] == "superseded"
    async with store.transaction() as c:
        assert (await c.execute(select(func.count()).select_from(s.chunks))).scalar_one() == 0
        assert (await c.execute(select(func.count()).select_from(s.versions))).scalar_one() == 2
    with pytest.raises(IngestionError):
        await store.reprocess(second.document_id)
    restored = await store.submit(submission())
    assert restored.version == 3 and not restored.deduplicated
    assert await process_one(store)


async def expire(store: PostgreSQLDocumentStore, job_id: str) -> None:
    async with store.transaction() as c:
        await c.execute(
            update(s.jobs)
            .where(s.jobs.c.id == job_id)
            .values(
                lease_until=datetime.now(UTC) - timedelta(seconds=1),
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )


async def test_restart_reclaims_expired_leases_with_fencing_and_attempt_bound(
    store: PostgreSQLDocumentStore,
) -> None:
    receipt = await store.submit(submission())
    first = await store.claim(5)
    assert first and await store.claim(5) is None
    # Controlled database-clock fixture models process death; store/driver remain real.
    await expire(store, str(receipt.job_id))
    restarted = PostgreSQLDocumentStore(store.engine)
    second = await restarted.claim(5)
    assert second and second.token != first.token and second.attempts == 2
    stale_chunks = segment(
        decode(first.content),
        first.document_id,
        first.version,
        submission().content_sha256,
        first.chunking,
    )
    assert not await restarted.complete(first, stale_chunks)
    await expire(store, str(receipt.job_id))
    third = await restarted.claim(5)
    assert third and third.attempts == 3
    await expire(store, str(receipt.job_id))
    assert await restarted.claim(5) is None
    assert (await restarted.status(receipt.job_id))["status"] == "failed"
    next_receipt = await restarted.submit(submission(uri="atlas://docs/ready"))
    assert await process_one(restarted)
    assert (await restarted.status(next_receipt.job_id))["status"] == "succeeded"


async def test_retry_backoff_and_safe_failure_codes(store: PostgreSQLDocumentStore) -> None:
    receipt = await store.submit(submission())
    job = await store.claim()
    assert job
    await store.fail(job, "private-connection-password", True)
    status = await store.status(receipt.job_id)
    assert status["status"] == "retry" and status["error_code"] == "processing_failed"
    assert await store.claim() is None
    await expire(store, str(job.id))
    assert await process_one(store)
    assert (await store.status(receipt.job_id))["status"] == "succeeded"
    bad = await store.submit(submission(b"\xff"))
    assert await process_one(store)
    assert (await store.status(bad.job_id))["error_code"] == "invalid_encoding"
    assert (await store.status(bad.job_id))["attempts"] == 1
    assert await store.claim() is None


async def test_cancelled_worker_recovers_and_reprocessing_is_idempotent(
    store: PostgreSQLDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = await store.submit(submission())
    entered = asyncio.Event()
    original_complete = store.complete

    async def paused_complete(job: object, chunks: list[Chunk]) -> bool:
        # Application pause seam only; claiming, storage and leases use actual PostgreSQL.
        entered.set()
        await asyncio.Event().wait()
        return False

    monkeypatch.setattr(store, "complete", paused_complete)
    task = asyncio.create_task(process_one(store))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await store.status(receipt.job_id))["status"] == "running"
    monkeypatch.setattr(store, "complete", original_complete)
    await expire(store, str(receipt.job_id))
    assert await process_one(PostgreSQLDocumentStore(store.engine))
    async with store.transaction() as c:
        original = (await c.execute(select(s.chunks.c.id))).scalars().all()
    for _ in range(2):
        await store.reprocess(receipt.document_id)
        assert await process_one(store)
    async with store.transaction() as c:
        assert (await c.execute(select(s.chunks.c.id))).scalars().all() == original


async def test_invalid_or_tampered_provenance_cannot_publish(
    store: PostgreSQLDocumentStore,
) -> None:
    request = submission()
    receipt = await store.submit(request)
    job = await store.claim()
    assert job
    chunks = segment(
        decode(job.content), job.document_id, job.version, request.content_sha256, job.chunking
    )
    for invalid in ([], [chunks[0].model_copy(update={"text": "forged"})]):
        with pytest.raises(IngestionError) as error:
            await store.complete(job, invalid)
        assert error.value.code == "invalid_chunks"
    with pytest.raises(IngestionError):
        await store.complete(job.model_copy(update={"content": b"forged"}), chunks)
    assert await store.complete(job, chunks)
    assert not await store.complete(job, chunks)
    assert (await store.status(receipt.job_id))["status"] == "succeeded"


async def test_exhausted_job_does_not_hide_ready_work(store: PostgreSQLDocumentStore) -> None:
    exhausted = await store.submit(submission())
    async with store.transaction() as c:
        await c.execute(
            update(s.jobs)
            .where(s.jobs.c.id == str(exhausted.job_id))
            .values(
                status="running",
                attempts=3,
                token=str(uuid4()),
                lease_until=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    ready = await store.submit(submission(uri="atlas://docs/ready"))
    assert await process_one(store)
    assert (await store.status(exhausted.job_id))["status"] == "failed"
    assert (await store.status(ready.job_id))["status"] == "succeeded"


async def test_parallel_claims_are_exclusive(store: PostgreSQLDocumentStore) -> None:
    receipts = [await store.submit(submission(uri=f"atlas://docs/parallel-{i}")) for i in range(8)]
    # Contending actual connections exercise FOR UPDATE SKIP LOCKED, not a Python mock lock.
    jobs = await asyncio.gather(*(store.claim() for _ in range(8)))
    claimed = [job for job in jobs if job]
    assert len(claimed) == 8 and len({job.id for job in claimed}) == 8
    for job in claimed:
        chunks = segment(
            decode(job.content),
            job.document_id,
            job.version,
            submission().content_sha256,
            job.chunking,
        )
        assert await store.complete(job, chunks)
    statuses = [await store.status(receipt.job_id) for receipt in receipts]
    assert all(status["attempts"] == 1 for status in statuses)


@pytest.fixture
def pdf_parser() -> None:
    if os.environ.get("ATLAS_TEST_PDF_PARSER") != "1":
        pytest.skip("Set ATLAS_TEST_PDF_PARSER=1 after make pdf-parser-build")


async def test_pdf_pages_atomic_history_reprocessing_and_deletion(
    store: PostgreSQLDocumentStore, pdf_parser: None
) -> None:
    from atlas_contracts.extraction import ExtractionArtifact, ExtractionConfig

    request = submission(
        await asyncio.to_thread(Path("evaluation/nlp/source-v1/retail.pdf").read_bytes)
    )
    request = request.model_copy(
        update={
            "descriptor": request.descriptor.model_copy(update={"media_type": "application/pdf"})
        }
    )
    receipt = await store.submit(request)
    assert await process_one(store)
    assert (await store.status(receipt.job_id))["status"] == "succeeded"
    history = await store.extraction_history(receipt.document_id, active_only=True)
    assert len(history) == 1
    artifact = ExtractionArtifact.model_validate(history[0]["artifact"])
    assert len(artifact.pages) == 2 and "public.orders" in artifact.extracted_text
    async with store.transaction() as c:
        before = (await c.execute(select(s.chunks.c.id))).scalars().all()
        assert before
        assert all(
            row == str(artifact.id)
            for row in (await c.execute(select(s.chunks.c.extraction_id))).scalars()
        )
    await store.reprocess(receipt.document_id)
    assert not await store.extraction_history(receipt.document_id, active_only=True)
    assert await process_one(store)
    assert len(await store.extraction_history(receipt.document_id)) == 1
    # Explicit normalizer upgrade preserves source/version/chunk IDs and retains old artifact.
    upgraded = await store.reprocess(
        receipt.document_id, ExtractionConfig(normalization="identity-v1")
    )
    assert await process_one(store)
    assert (await store.status(upgraded))["status"] == "succeeded"
    assert len(await store.extraction_history(receipt.document_id)) == 2
    async with store.transaction() as c:
        assert (await c.execute(select(s.chunks.c.id))).scalars().all() == before
    await store.delete(receipt.document_id)
    assert not await store.extraction_history(receipt.document_id, active_only=True)
    assert len(await store.extraction_history(receipt.document_id)) == 2


@pytest.mark.parametrize(
    "name,code",
    [
        ("scanned", "ocr_required"),
        ("inline-scanned", "ocr_required"),
        ("encrypted", "encrypted_pdf"),
        ("malformed", "malformed_pdf"),
    ],
)
async def test_real_pdf_failures_are_structured_and_not_published(
    store: PostgreSQLDocumentStore, pdf_parser: None, name: str, code: str
) -> None:
    request = submission(
        await asyncio.to_thread(Path(f"evaluation/nlp/source-v1/{name}.pdf").read_bytes)
    )
    request = request.model_copy(
        update={
            "descriptor": request.descriptor.model_copy(update={"media_type": "application/pdf"})
        }
    )
    receipt = await store.submit(request)
    assert await process_one(store)
    status = await store.status(receipt.job_id)
    assert status["status"] == "failed" and status["error_code"] == code
    assert "private-parser-error-canary" not in str(status)
    assert not await store.extraction_history(receipt.document_id)


async def test_text_extraction_fencing_and_history_tampering(
    store: PostgreSQLDocumentStore,
) -> None:
    from atlas_contracts.extraction import ExtractionConfig
    from atlas_documents.extraction import SourceExtractor, segment_artifact

    request = submission(b"A\r\nB")
    receipt = await store.submit(request)
    assert await process_one(store)
    async with store.transaction() as c:
        before = (await c.execute(select(s.chunks.c.id))).scalars().all()
    await store.reprocess(receipt.document_id, ExtractionConfig())
    job = await store.claim()
    assert job and job.extraction_config
    artifact = await SourceExtractor().extract(
        job.document_id, job.version, job.content, job.descriptor.media_type, job.extraction_config
    )
    chunks = segment_artifact(artifact, job.chunking)
    with pytest.raises(IngestionError):
        await store.complete(job, chunks, artifact.model_copy(update={"source_sha256": "a" * 64}))
    assert await store.complete(job, chunks, artifact)
    async with store.transaction() as c:
        assert (await c.execute(select(s.chunks.c.id))).scalars().all() == before
    await store.reprocess(receipt.document_id)
    stale = await store.claim()
    assert stale
    await store.delete(receipt.document_id)
    assert not await store.complete(stale, chunks, artifact)
    assert len(await store.extraction_history(receipt.document_id)) == 1
    assert not await store.extraction_history(receipt.document_id, active_only=True)


async def test_extraction_publication_rolls_back_on_storage_failure(
    store: PostgreSQLDocumentStore,
) -> None:
    from typing import Any

    from atlas_contracts.extraction import ExtractionConfig
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    receipt = await store.submit(submission(b"A\r\nB"))
    assert await process_one(store)
    async with store.transaction() as c:
        original_ids = (await c.execute(select(s.chunks.c.id))).scalars().all()
    job_id = await store.reprocess(receipt.document_id, ExtractionConfig())
    injected = False

    def fail_once(
        conn: object, cursor: object, statement: str, params: Any, context: object, many: bool
    ) -> None:
        nonlocal injected
        if statement.startswith("INSERT INTO document_chunks") and not injected:
            injected = True
            raise OperationalError("redacted", {}, RuntimeError("private-storage-canary"))

    event.listen(store.engine.sync_engine, "before_cursor_execute", fail_once)
    try:
        assert await process_one(store)
    finally:
        event.remove(store.engine.sync_engine, "before_cursor_execute", fail_once)
    assert injected
    status = await store.status(job_id)
    assert status["status"] == "retry" and status["error_code"] == "storage_unavailable"
    assert "private-storage-canary" not in str(status)
    assert not await store.extraction_history(receipt.document_id)
    async with store.transaction() as c:
        assert (await c.execute(select(s.chunks.c.id))).scalars().all() == original_ids
    await expire(store, str(job_id))
    assert await process_one(store)
    assert len(await store.extraction_history(receipt.document_id, active_only=True)) == 1


async def test_non_destructive_upgrade_preserves_phase31_ids(
    store: PostgreSQLDocumentStore,
) -> None:
    from sqlalchemy import insert

    request = submission()
    document_id, job_id = str(request.descriptor.document_id), str(uuid4())
    chunks = segment(
        decode(request.content),
        request.descriptor.document_id,
        1,
        request.content_sha256,
        request.chunking,
    )
    async with store.engine.begin() as c:

        def downgrade(connection: Connection) -> None:
            config = configuration()
            config.attributes["connection"] = connection
            command.downgrade(config, "knowledge_0001")

        await c.run_sync(downgrade)
        await c.execute(
            insert(s.documents).values(
                id=document_id,
                collection="retail",
                source_uri=request.descriptor.source_uri,
                version=1,
                generation=1,
                deleted=False,
            )
        )
        await c.execute(
            insert(s.blobs).values(sha256=request.content_sha256, content=request.content)
        )
        await c.execute(
            insert(s.versions).values(
                document_id=document_id,
                version=1,
                content_sha256=request.content_sha256,
                revision_sha256=request.revision_sha256,
                descriptor=request.descriptor.model_dump(mode="json"),
                chunking=request.chunking.model_dump(mode="json"),
                ingested_at=func.now(),
            )
        )
        await c.execute(
            insert(s.jobs).values(
                id=job_id,
                document_id=document_id,
                version=1,
                generation=1,
                status="succeeded",
                attempts=1,
                created_at=func.now(),
                available_at=func.now(),
            )
        )
        await c.execute(insert(s.chunks), [chunk.model_dump(mode="json") for chunk in chunks])
        await c.run_sync(migrate)
    async with store.transaction() as c:
        assert (await c.execute(select(s.chunks.c.id))).scalars().all() == [
            str(chunk.id) for chunk in chunks
        ]
    assert (await store.submit(request)).deduplicated
    from atlas_contracts.extraction import ExtractionConfig

    await store.reprocess(request.descriptor.document_id, ExtractionConfig())
    assert await process_one(store)
    history = await store.extraction_history(request.descriptor.document_id)
    assert len(history) == 1
    with pytest.raises(IngestionError):
        async with store.transaction() as c:
            await c.execute(update(s.extractions).values(artifact_sha256="a" * 64))
    with pytest.raises(IngestionError):
        async with store.transaction() as c:
            from sqlalchemy import delete

            await c.execute(delete(s.extractions))
    assert (await store.extraction_history(request.descriptor.document_id))[0][
        "artifact_sha256"
    ] == history[0]["artifact_sha256"]
