"""Bounded extraction; PDF is isolated from the application event loop and credentials."""

import asyncio
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4, uuid5

from atlas_contracts.documents import MAX_SOURCE_BYTES, Chunk, ChunkConfig, IngestionError
from atlas_contracts.extraction import (
    ExtractedPage,
    ExtractionArtifact,
    ExtractionConfig,
    normalize,
)

from atlas_documents.chunking import decode, segment

PDF_ERRORS = frozenset(
    {
        "source_too_large",
        "malformed_pdf",
        "encrypted_pdf",
        "page_limit",
        "ocr_required",
        "empty_pdf",
        "text_limit",
        "parser_resource_limit",
    }
)


async def _reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    cleanup = asyncio.create_task(process.wait())
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
    await cleanup


async def pdf_pages(content: bytes, config: ExtractionConfig) -> list[str]:
    executable = shutil.which("docker")
    if executable is None:
        raise IngestionError("parser_unavailable", "Build and provision the isolated PDF parser")
    name = "atlas-pdf-" + uuid4().hex
    env = {"HOME": str(Path.home()), "PATH": os.defpath}
    process = await asyncio.create_subprocess_exec(
        executable,
        "run",
        "--rm",
        "--pull",
        "never",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "32",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "1",
        "--init",
        "--log-driver",
        "none",
        "--interactive",
        "atlas-pdf-parser:6.21.4",
        str(config.max_pages),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )
    try:
        async with asyncio.timeout(config.timeout_seconds):
            assert process.stdin and process.stdout
            process.stdin.write(content)
            await process.stdin.drain()
            process.stdin.close()
            output = bytearray()
            while block := await process.stdout.read(65536):
                output.extend(block)
                if len(output) > 2_000_000:
                    raise IngestionError("text_limit", "PDF output exceeds extraction limits")
            code = await process.wait()
            if code in {125, 126, 127}:
                raise IngestionError("parser_unavailable", "Isolated PDF parser is unavailable")
            if code:
                raise IngestionError(
                    "parser_resource_limit", "PDF parser could not complete within resource limits"
                )
            result = json.loads(output)
            if result.get("error"):
                error = result["error"]
                safe = error if error in PDF_ERRORS else "malformed_pdf"
                raise IngestionError(safe, "PDF extraction could not produce supported text")
            if result.get("parser_revision") != config.pdf_parser_revision:
                raise IngestionError(
                    "parser_revision_mismatch", "Requested PDF parser revision is unavailable"
                )
            pages = result.get("pages")
            if (
                not isinstance(pages, list)
                or not pages
                or not all(isinstance(p, str) for p in pages)
            ):
                raise ValueError("Invalid child response")
            if len(pages) > config.max_pages:
                raise IngestionError("page_limit", "PDF exceeds configured page limit")
            return pages
    except TimeoutError:
        raise IngestionError(
            "parser_timeout", "PDF extraction exceeded its deadline", True
        ) from None
    except (ValueError, BrokenPipeError, ConnectionResetError):
        raise IngestionError("malformed_pdf", "PDF parser returned no supported text") from None
    finally:
        # Includes repeated cancellation: no child remains active when this call exits.
        await _reap(process)
        # Killing the attach client alone does not stop a Docker container.
        cleanup = asyncio.create_task(_remove_container(executable, name, env))
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        await cleanup


async def _remove_container(executable: str, name: str, env: dict[str, str]) -> None:
    async def command(*args: str) -> tuple[int | None, bytes]:
        child = await asyncio.create_subprocess_exec(
            executable,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        try:
            async with asyncio.timeout(5):
                output, _ = await child.communicate()
                return child.returncode, output
        finally:
            await _reap(child)

    await command("rm", "--force", name)
    code, output = await command(
        "ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.ID}}"
    )
    if code != 0 or output.strip():
        raise IngestionError("parser_cleanup_failed", "PDF parser cleanup could not be confirmed")


class SourceExtractor:
    async def extract(
        self,
        document_id: UUID,
        version: int,
        content: bytes,
        media_type: str,
        config: ExtractionConfig,
    ) -> ExtractionArtifact:
        if not content or len(content) > MAX_SOURCE_BYTES:
            raise IngestionError("source_too_large", "Source exceeds supported extraction bounds")
        parser: Literal["pypdf", "utf8"]
        revision: str
        if media_type == "application/pdf":
            pages = await pdf_pages(content, config)
            parser, revision = "pypdf", config.pdf_parser_revision
        elif media_type in {"text/plain", "text/markdown", "application/sql"}:
            pages = [decode(content)]
            parser, revision = "utf8", "utf8-v1"
        else:
            raise IngestionError("unsupported_format", "Source format is unsupported")
        text = "\f".join(pages)
        if not text.strip() or "\x00" in text:
            raise IngestionError("invalid_content", "Extraction produced no supported text")
        if len(text) > config.max_chars:
            raise IngestionError("text_limit", "Extracted text exceeds configured limit")
        boundaries = []
        start = 0
        for number, page in enumerate(pages, 1):
            boundaries.append(ExtractedPage(number=number, start=start, end=start + len(page)))
            start += len(page) + 1
        digest = hashlib.sha256(content).hexdigest()
        try:
            mapped = normalize(text, config.normalization)
        except ValueError:
            raise IngestionError(
                "normalization_limit", "Text mapping exceeds supported complexity"
            ) from None
        return ExtractionArtifact(
            id=uuid5(document_id, f"{version}:{digest}:{config.digest}"),
            document_id=document_id,
            version=version,
            source_sha256=digest,
            config=config,
            parser=parser,
            parser_revision=revision,
            extracted_text=text,
            pages=boundaries,
            normalized=mapped,
            processed_at=datetime.now(UTC),
        )


def segment_artifact(artifact: ExtractionArtifact, config: ChunkConfig) -> list[Chunk]:
    if artifact.parser == "utf8":
        return segment(
            artifact.extracted_text,
            artifact.document_id,
            artifact.version,
            artifact.source_sha256,
            config,
        )
    chunks: list[Chunk] = []
    for index, page in enumerate(artifact.pages):
        end = artifact.pages[index + 1].start if index + 1 < len(artifact.pages) else page.end
        for chunk in segment(
            artifact.extracted_text[page.start : end],
            artifact.document_id,
            artifact.version,
            artifact.source_sha256,
            config,
        ):
            start, stop = chunk.start + page.start, chunk.end + page.start
            chunks.append(
                chunk.model_copy(
                    update={
                        "id": uuid5(
                            artifact.document_id,
                            f"{artifact.version}:{config.digest}:{start}:{stop}",
                        ),
                        "start": start,
                        "end": stop,
                        "section_path": [f"page:{page.number}"],
                    }
                )
            )
    return chunks
