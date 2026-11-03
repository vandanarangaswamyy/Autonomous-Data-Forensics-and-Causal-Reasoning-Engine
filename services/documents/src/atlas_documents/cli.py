"""Local operator interface. Never mount source/owner settings into investigation agents."""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

from atlas_contracts.documents import (
    MAX_SOURCE_BYTES,
    ChunkConfig,
    DocumentKind,
    IngestionError,
    SourceDescriptor,
    SourceSubmission,
)
from atlas_database.documents import PostgreSQLDocumentStore
from pydantic import ValidationError

from atlas_documents.settings import DocumentSettings
from atlas_documents.worker import process_one


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="ATLAS durable knowledge source ingestion")
    sub = result.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("submit")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--collection", required=True)
    ingest.add_argument("--source-uri", required=True)
    ingest.add_argument("--source-version", required=True)
    ingest.add_argument("--observed-at", required=True, type=datetime.fromisoformat)
    ingest.add_argument("--kind", required=True, choices=[kind.value for kind in DocumentKind])
    ingest.add_argument("--media-type", default="text/plain")
    ingest.add_argument("--strategy", choices=["recursive", "markdown"], default="recursive")
    ingest.add_argument("--max-chars", type=int, default=1000)
    worker = sub.add_parser("work")
    worker.add_argument("--max-jobs", type=int, default=100)
    for name in ("delete", "reprocess", "status"):
        sub.add_parser(name).add_argument("id", type=UUID)
    return result


async def execute(args: argparse.Namespace) -> object:
    engine = DocumentSettings().engine()
    try:
        store = PostgreSQLDocumentStore(engine)
        if args.command == "submit":
            # Explicit local files only: no crawler, remote fetch, directory traversal or symlinks.
            if args.path.is_symlink() or not args.path.is_file():
                raise IngestionError("invalid_source", "Provide a regular local source file")
            with args.path.open("rb") as handle:
                content = handle.read(MAX_SOURCE_BYTES + 1)
            submission = SourceSubmission(
                descriptor=SourceDescriptor(
                    collection=args.collection,
                    source_uri=args.source_uri,
                    source_version=args.source_version,
                    observed_at=args.observed_at,
                    kind=args.kind,
                    media_type=args.media_type,
                ),
                content=content,
                chunking=ChunkConfig(strategy=args.strategy, max_chars=args.max_chars),
            )
            return (await store.submit(submission)).model_dump(mode="json")
        if args.command == "work":
            if not 1 <= args.max_jobs <= 10000:
                raise IngestionError("invalid_input", "Worker max-jobs must be between 1 and 10000")
            count = 0
            while count < args.max_jobs and await process_one(store):
                count += 1
            return {"claimed_jobs": count}
        if args.command == "delete":
            return {"deleted": await store.delete(args.id)}
        if args.command == "reprocess":
            return {"job_id": str(await store.reprocess(args.id))}
        return await store.status(args.id)
    finally:
        await engine.dispose()


def main() -> None:
    args = parser().parse_args()
    try:
        result = asyncio.run(execute(args))
    except IngestionError as exc:
        print(
            json.dumps({"code": exc.code, "message": exc.message, "retryable": exc.retryable}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except (ValidationError, ValueError, OSError):
        print(
            json.dumps({"code": "invalid_input", "message": "Source or configuration is invalid"}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except Exception:
        print(
            json.dumps({"code": "internal_error", "message": "Ingestion command failed"}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(json.dumps(result))
