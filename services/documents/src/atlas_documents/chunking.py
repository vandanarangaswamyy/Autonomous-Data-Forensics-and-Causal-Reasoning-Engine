"""Deterministic bounded segmentation over exact decoded source, with zero offset drift."""

import re
from uuid import UUID, uuid5

from atlas_contracts.documents import Chunk, ChunkConfig, IngestionError


def decode(content: bytes) -> str:
    try:
        value = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IngestionError("invalid_encoding", "Source must contain UTF-8 text") from exc
    if "\x00" in value or not value.strip():
        raise IngestionError("invalid_content", "Source must contain nonempty text without NUL")
    return value


def segment(
    text: str, document_id: UUID, version: int, content_hash: str, config: ChunkConfig
) -> list[Chunk]:
    sections: list[tuple[int, int, list[str]]] = []
    start = 0
    hierarchy: list[str] = []
    if config.strategy == "markdown":
        in_fence = False
        fence = ""
        for match in re.finditer(r".*(?:\n|$)", text):
            line = match.group().rstrip("\r\n")
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if marker:
                candidate = marker.group(1)
                if not in_fence:
                    in_fence, fence = True, candidate
                elif candidate[0] == fence[0] and len(candidate) >= len(fence):
                    in_fence = False
                continue
            heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$", line)
            if heading and not in_fence:
                if match.start() > start:
                    sections.append((start, match.start(), list(hierarchy)))
                level, title = len(heading.group(1)), heading.group(2)
                hierarchy = hierarchy[: level - 1] + [title]
                start = match.start()
    sections.append((start, len(text), list(hierarchy)))
    result: list[Chunk] = []
    for left, right, path in sections:
        while left < right:
            end = min(left + config.max_chars, right)
            if end < right:
                # Prefer paragraphs, then lines, then whitespace. Never discard delimiters.
                for separator in ("\n\n", "\n", " "):
                    boundary = text.rfind(separator, left + config.max_chars // 2, end)
                    if boundary >= 0:
                        end = boundary + len(separator)
                        break
            identity = f"{version}:{config.digest}:{left}:{end}"
            result.append(
                Chunk(
                    id=uuid5(document_id, identity),
                    document_id=document_id,
                    version=version,
                    start=left,
                    end=end,
                    text=text[left:end],
                    section_path=path,
                    content_sha256=content_hash,
                    processor_sha256=config.digest,
                )
            )
            left = end
    return result
