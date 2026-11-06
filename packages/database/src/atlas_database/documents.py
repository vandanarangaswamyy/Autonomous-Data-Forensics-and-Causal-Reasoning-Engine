"""PostgreSQL durable ingestion queue with document locks and fenced worker leases."""

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from atlas_contracts.documents import (
    Chunk,
    ChunkConfig,
    IngestionError,
    IngestionReceipt,
    Job,
    SourceDescriptor,
    SourceSubmission,
)
from atlas_contracts.extraction import ExtractionArtifact, ExtractionConfig
from pydantic import ValidationError
from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from atlas_database import documents_schema as s


class PostgreSQLDocumentStore:
    def __init__(self, engine: AsyncEngine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Document ingestion requires PostgreSQL")
        self.engine = engine

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        try:
            async with self.engine.begin() as connection:
                await connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
                await connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
                yield connection
        except SQLAlchemyError as exc:
            raise IngestionError(
                "storage_unavailable", "Ingestion storage operation failed", True
            ) from exc

    async def submit(self, request: SourceSubmission) -> IngestionReceipt:
        descriptor = request.descriptor
        document_id = str(descriptor.document_id)
        async with self.transaction() as c:
            await c.execute(
                pg_insert(s.documents)
                .values(
                    id=document_id,
                    collection=descriptor.collection,
                    source_uri=descriptor.source_uri,
                    version=0,
                    generation=0,
                    deleted=False,
                )
                .on_conflict_do_nothing()
            )
            document = (
                (
                    await c.execute(
                        select(s.documents).where(s.documents.c.id == document_id).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if document["version"] and not document["deleted"]:
                previous = (
                    (
                        await c.execute(
                            select(s.versions).where(
                                s.versions.c.document_id == document_id,
                                s.versions.c.version == document["version"],
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                if previous["revision_sha256"] == request.revision_sha256:
                    job_id = (
                        await c.execute(
                            select(s.jobs.c.id).where(
                                s.jobs.c.document_id == document_id,
                                s.jobs.c.generation == document["generation"],
                            )
                        )
                    ).scalar_one()
                    return IngestionReceipt(
                        document_id=descriptor.document_id,
                        version=document["version"],
                        job_id=UUID(job_id),
                        deduplicated=True,
                    )
            version, generation = document["version"] + 1, document["generation"] + 1
            await c.execute(
                pg_insert(s.blobs)
                .values(sha256=request.content_sha256, content=request.content)
                .on_conflict_do_nothing()
            )
            await c.execute(
                insert(s.versions).values(
                    document_id=document_id,
                    version=version,
                    content_sha256=request.content_sha256,
                    revision_sha256=request.revision_sha256,
                    descriptor=descriptor.model_dump(mode="json"),
                    chunking=request.chunking.model_dump(mode="json"),
                    ingested_at=func.now(),
                )
            )
            await c.execute(
                update(s.documents)
                .where(s.documents.c.id == document_id)
                .values(version=version, generation=generation, deleted=False)
            )
            job_id = await self._enqueue(
                c,
                document_id,
                version,
                generation,
                ExtractionConfig() if descriptor.media_type == "application/pdf" else None,
            )
            return IngestionReceipt(
                document_id=descriptor.document_id,
                version=version,
                job_id=job_id,
                deduplicated=False,
            )

    async def _enqueue(
        self,
        c: AsyncConnection,
        document_id: str,
        version: int,
        generation: int,
        extraction_config: ExtractionConfig | None = None,
    ) -> UUID:
        await c.execute(
            update(s.jobs)
            .where(
                s.jobs.c.document_id == document_id,
                s.jobs.c.status.in_(["queued", "retry", "running"]),
            )
            .values(status="superseded", token=None, lease_until=None)
        )
        job_id = uuid4()
        await c.execute(
            insert(s.jobs).values(
                id=str(job_id),
                document_id=document_id,
                version=version,
                generation=generation,
                status="queued",
                attempts=0,
                created_at=func.now(),
                available_at=func.now(),
                extraction_config=extraction_config.model_dump(mode="json")
                if extraction_config
                else None,
            )
        )
        return job_id

    async def claim(self, lease_seconds: int = 60) -> Job | None:
        # Expired jobs at the attempt cap must not hide ready work behind them.
        for _ in range(100):
            result = await self._claim(lease_seconds)
            if result is not False:
                return result
        raise IngestionError("queue_busy", "Queue recovery batch limit reached", True)

    async def _claim(self, lease_seconds: int) -> Job | None | Literal[False]:
        if not 5 <= lease_seconds <= 300:
            raise ValueError("Lease must be between 5 and 300 seconds")
        async with self.transaction() as c:
            ready = or_(
                and_(s.jobs.c.status.in_(["queued", "retry"]), s.jobs.c.available_at <= func.now()),
                and_(s.jobs.c.status == "running", s.jobs.c.lease_until <= func.now()),
            )
            # Every mutator locks documents before jobs. Skip locked supports parallel workers
            # without reclaiming a live lease, or deadlocking submit/delete against claim.
            row = (
                (
                    await c.execute(
                        select(s.jobs)
                        .join(
                            s.documents,
                            and_(
                                s.documents.c.id == s.jobs.c.document_id,
                                s.documents.c.generation == s.jobs.c.generation,
                            ),
                        )
                        .where(ready, s.documents.c.deleted.is_(False))
                        .order_by(s.jobs.c.created_at, s.jobs.c.id)
                        .limit(1)
                        .with_for_update(of=s.documents, skip_locked=True)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            if row["attempts"] >= 3:
                await c.execute(
                    update(s.jobs)
                    .where(s.jobs.c.id == row["id"])
                    .values(
                        status="failed",
                        error_code="attempts_exhausted",
                        token=None,
                        lease_until=None,
                    )
                )
                return False
            token = uuid4()
            await c.execute(
                update(s.jobs)
                .where(s.jobs.c.id == row["id"])
                .values(
                    status="running",
                    token=str(token),
                    attempts=row["attempts"] + 1,
                    lease_until=func.now() + timedelta(seconds=lease_seconds),
                    error_code=None,
                )
            )
            version = (
                (
                    await c.execute(
                        select(s.versions, s.blobs.c.content)
                        .join(s.blobs, s.blobs.c.sha256 == s.versions.c.content_sha256)
                        .where(
                            s.versions.c.document_id == row["document_id"],
                            s.versions.c.version == row["version"],
                        )
                    )
                )
                .mappings()
                .one()
            )
            return Job(
                id=UUID(row["id"]),
                document_id=UUID(row["document_id"]),
                version=row["version"],
                generation=row["generation"],
                token=token,
                attempts=row["attempts"] + 1,
                descriptor=SourceDescriptor.model_validate(version["descriptor"]),
                content=bytes(version["content"]),
                chunking=ChunkConfig.model_validate(version["chunking"]),
                extraction_config=ExtractionConfig.model_validate(row["extraction_config"])
                if row["extraction_config"]
                else None,
            )

    async def _owned(self, c: AsyncConnection, job: Job) -> bool:
        document = (
            (
                await c.execute(
                    select(s.documents)
                    .where(s.documents.c.id == str(job.document_id))
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if document is None or document["deleted"] or document["generation"] != job.generation:
            return False
        return (
            await c.execute(
                select(s.jobs.c.id).where(
                    s.jobs.c.id == str(job.id),
                    s.jobs.c.token == str(job.token),
                    s.jobs.c.document_id == str(job.document_id),
                    s.jobs.c.version == job.version,
                    s.jobs.c.status == "running",
                    s.jobs.c.lease_until > func.now(),
                )
            )
        ).first() is not None

    async def complete(
        self, job: Job, chunks: list[Chunk], artifact: ExtractionArtifact | None = None
    ) -> bool:
        async with self.transaction() as c:
            if not await self._owned(c, job):
                return False
            version = (
                (
                    await c.execute(
                        select(s.versions).where(
                            s.versions.c.document_id == str(job.document_id),
                            s.versions.c.version == job.version,
                        )
                    )
                )
                .mappings()
                .one()
            )
            content_hash = hashlib.sha256(job.content).hexdigest()
            if (
                content_hash != version["content_sha256"]
                or job.chunking.model_dump(mode="json") != version["chunking"]
                or job.descriptor.model_dump(mode="json") != version["descriptor"]
            ):
                raise IngestionError(
                    "invalid_chunks", "Processor source differs from stored version"
                )
            stored_config = (
                await c.execute(
                    select(s.jobs.c.extraction_config).where(s.jobs.c.id == str(job.id))
                )
            ).scalar_one()
            if stored_config != (
                job.extraction_config.model_dump(mode="json") if job.extraction_config else None
            ):
                raise IngestionError(
                    "invalid_extraction", "Extraction request configuration changed"
                )
            if job.extraction_config is not None:
                if artifact is None:
                    raise IngestionError(
                        "invalid_extraction", "Configured extraction artifact is required"
                    )
                # model_copy bypasses Pydantic validation; validate a serialized boundary again.
                try:
                    artifact = ExtractionArtifact.model_validate(artifact.model_dump())
                except ValidationError:
                    raise IngestionError(
                        "invalid_extraction", "Extraction artifact validation failed"
                    ) from None
                if (
                    artifact.document_id != job.document_id
                    or artifact.version != job.version
                    or artifact.source_sha256 != content_hash
                    or artifact.config != job.extraction_config
                    or (artifact.parser == "pypdf")
                    != (job.descriptor.media_type == "application/pdf")
                ):
                    raise IngestionError(
                        "invalid_extraction", "Extraction source provenance disagrees"
                    )
                original = artifact.extracted_text
                if artifact.parser == "utf8" and original != job.content.decode("utf-8"):
                    raise IngestionError(
                        "invalid_extraction", "Text extraction differs from source bytes"
                    )
            else:
                if artifact is not None or job.descriptor.media_type == "application/pdf":
                    raise IngestionError(
                        "invalid_extraction", "Source requires explicit extraction configuration"
                    )
                original = job.content.decode("utf-8")
            position = 0
            for chunk in chunks:
                if artifact is not None and artifact.parser == "pypdf":
                    page = next(
                        (p for p in artifact.pages if p.start <= chunk.start <= p.end), None
                    )
                    if page is None or chunk.end > page.end + (
                        1 if page.number < len(artifact.pages) else 0
                    ):
                        raise IngestionError("invalid_chunks", "PDF chunk crosses page boundaries")
                identity = f"{job.version}:{job.chunking.digest}:{chunk.start}:{chunk.end}"
                if (
                    chunk.document_id != job.document_id
                    or chunk.version != job.version
                    or chunk.start != position
                    or chunk.end > len(original)
                    or chunk.end - chunk.start > job.chunking.max_chars
                    or chunk.text != original[chunk.start : chunk.end]
                    or chunk.content_sha256 != content_hash
                    or chunk.processor_sha256 != job.chunking.digest
                    or chunk.id != uuid5(job.document_id, identity)
                ):
                    raise IngestionError(
                        "invalid_chunks", "Processor output violates source provenance"
                    )
                position = chunk.end
            if not chunks or position != len(original):
                raise IngestionError("invalid_chunks", "Processor output omits source content")
            if artifact is not None:
                encoded = artifact.model_dump(mode="json", exclude={"processed_at"})
                fingerprint = hashlib.sha256(
                    json.dumps(encoded, sort_keys=True).encode()
                ).hexdigest()
                existing = (
                    await c.execute(
                        select(s.extractions.c.artifact_sha256).where(
                            s.extractions.c.id == str(artifact.id)
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None and existing != fingerprint:
                    raise IngestionError(
                        "invalid_extraction", "Existing extraction history differs"
                    )
                if existing is None:
                    await c.execute(
                        insert(s.extractions).values(
                            id=str(artifact.id),
                            document_id=str(job.document_id),
                            version=job.version,
                            source_sha256=content_hash,
                            config_sha256=artifact.config.digest,
                            artifact_sha256=fingerprint,
                            processed_at=artifact.processed_at,
                            artifact=artifact.model_dump(mode="json"),
                            chunk_manifest=[chunk.model_dump(mode="json") for chunk in chunks],
                        )
                    )
            await c.execute(
                delete(s.chunks).where(
                    s.chunks.c.document_id == str(job.document_id),
                    s.chunks.c.version == job.version,
                )
            )
            if chunks:
                await c.execute(
                    insert(s.chunks),
                    [
                        {
                            **chunk.model_dump(mode="json"),
                            "extraction_id": str(artifact.id) if artifact else None,
                        }
                        for chunk in chunks
                    ],
                )
            await c.execute(
                update(s.jobs)
                .where(s.jobs.c.id == str(job.id))
                .values(status="succeeded", token=None, lease_until=None, error_code=None)
            )
            return True

    async def fail(self, job: Job, code: str, retryable: bool) -> None:
        codes = {
            "invalid_encoding",
            "invalid_content",
            "invalid_chunks",
            "processing_failed",
            "storage_unavailable",
            "invalid_extraction",
            "source_too_large",
            "malformed_pdf",
            "encrypted_pdf",
            "page_limit",
            "ocr_required",
            "empty_pdf",
            "text_limit",
            "parser_resource_limit",
            "parser_timeout",
            "parser_revision_mismatch",
            "unsupported_format",
            "normalization_limit",
            "parser_unavailable",
            "parser_cleanup_failed",
        }
        safe_code = code if code in codes else "processing_failed"
        async with self.transaction() as c:
            if not await self._owned(c, job):
                return
            retry = retryable and job.attempts < 3
            await c.execute(
                update(s.jobs)
                .where(s.jobs.c.id == str(job.id))
                .values(
                    status="retry" if retry else "failed",
                    error_code=safe_code,
                    available_at=func.now() + timedelta(seconds=min(60, 2**job.attempts)),
                    token=None,
                    lease_until=None,
                )
            )

    async def delete(self, document_id: UUID) -> bool:
        async with self.transaction() as c:
            row = (
                (
                    await c.execute(
                        select(s.documents)
                        .where(s.documents.c.id == str(document_id))
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["deleted"]:
                return False
            await c.execute(
                update(s.documents)
                .where(s.documents.c.id == str(document_id))
                .values(deleted=True, generation=row["generation"] + 1)
            )
            await c.execute(
                update(s.jobs)
                .where(
                    s.jobs.c.document_id == str(document_id),
                    s.jobs.c.status.in_(["queued", "running", "retry"]),
                )
                .values(status="superseded", token=None, lease_until=None)
            )
            await c.execute(delete(s.chunks).where(s.chunks.c.document_id == str(document_id)))
            return True

    async def reprocess(
        self, document_id: UUID, extraction_config: ExtractionConfig | None = None
    ) -> UUID:
        async with self.transaction() as c:
            row = (
                (
                    await c.execute(
                        select(s.documents)
                        .where(s.documents.c.id == str(document_id))
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["deleted"]:
                raise IngestionError("not_found", "Active document was not found")
            generation = row["generation"] + 1
            await c.execute(
                update(s.documents)
                .where(s.documents.c.id == str(document_id))
                .values(generation=generation)
            )
            if extraction_config is None:
                previous = (
                    await c.execute(
                        select(s.jobs.c.extraction_config).where(
                            s.jobs.c.document_id == str(document_id),
                            s.jobs.c.generation == row["generation"],
                        )
                    )
                ).scalar_one_or_none()
                extraction_config = ExtractionConfig.model_validate(previous) if previous else None
            return await self._enqueue(
                c, str(document_id), row["version"], generation, extraction_config
            )

    async def status(self, job_id: UUID) -> dict[str, Any]:
        async with self.transaction() as c:
            row = await c.execute(select(s.jobs).where(s.jobs.c.id == str(job_id)))
            mapping = row.mappings().first()
            if mapping is None:
                raise IngestionError("not_found", "Ingestion job was not found")
            return {
                key: mapping[key]
                for key in (
                    "id",
                    "document_id",
                    "version",
                    "generation",
                    "status",
                    "attempts",
                    "error_code",
                    "extraction_config",
                )
            }

    async def extraction_history(
        self, document_id: UUID, active_only: bool = False
    ) -> list[dict[str, Any]]:
        async with self.transaction() as c:
            query = select(s.extractions).where(s.extractions.c.document_id == str(document_id))
            if active_only:
                query = (
                    query.join(s.documents, s.documents.c.id == s.extractions.c.document_id)
                    .join(
                        s.jobs,
                        and_(
                            s.jobs.c.document_id == s.documents.c.id,
                            s.jobs.c.generation == s.documents.c.generation,
                        ),
                    )
                    .where(
                        s.documents.c.deleted.is_(False),
                        s.documents.c.version == s.extractions.c.version,
                        s.jobs.c.status == "succeeded",
                        s.extractions.c.id.in_(
                            select(s.chunks.c.extraction_id).where(
                                s.chunks.c.document_id == str(document_id)
                            )
                        ),
                    )
                )
            rows = (
                await c.execute(query.order_by(s.extractions.c.processed_at).limit(11))
            ).mappings()
            items = [dict(row) for row in rows]
            if len(items) > 10 or len(json.dumps(items, default=str)) > 2_000_000:
                raise IngestionError(
                    "result_too_large", "Extraction inspection exceeds its safe bound"
                )
            return items
