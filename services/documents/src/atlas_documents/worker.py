import asyncio
import hashlib

from atlas_contracts.documents import IngestionError
from atlas_core.documents import DocumentStore

from atlas_documents.chunking import decode, segment
from atlas_documents.extraction import SourceExtractor, segment_artifact


async def process_one(store: DocumentStore) -> bool:
    """One durable unit. Cancellation leaves a leased job for bounded crash recovery."""
    job = await store.claim()
    if job is None:
        return False
    try:
        if job.extraction_config is not None:
            artifact = await SourceExtractor().extract(
                job.document_id,
                job.version,
                job.content,
                job.descriptor.media_type,
                job.extraction_config,
            )
            chunks = await asyncio.to_thread(segment_artifact, artifact, job.chunking)
            await store.complete(job, chunks, artifact)
            return True
        text = decode(job.content)
        chunks = await asyncio.to_thread(
            segment,
            text,
            job.document_id,
            job.version,
            hashlib.sha256(job.content).hexdigest(),
            job.chunking,
        )
        await store.complete(job, chunks)
    except IngestionError as exc:
        await store.fail(job, exc.code, exc.retryable)
    except asyncio.CancelledError:
        raise  # No acknowledgement: a future worker reclaims the lease with a new token.
    except Exception:
        await store.fail(job, "processing_failed", False)
    return True
