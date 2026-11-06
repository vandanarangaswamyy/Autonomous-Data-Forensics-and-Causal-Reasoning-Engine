"""Typed source-extraction artifacts; no inferred entities, confidence or PDF coordinates."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from atlas_contracts.documents import IngestionModel
from atlas_contracts.extraction_config import ExtractionConfig as ExtractionConfig


class OffsetRun(IngestionModel):
    normalized_start: int = Field(ge=0)
    normalized_end: int = Field(gt=0)
    extracted_start: int = Field(ge=0)
    extracted_end: int = Field(gt=0)


class NormalizedText(IngestionModel):
    text: str
    mapping: list[OffsetRun]

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end <= len(self.text):
            raise ValueError("Normalized span is outside the text")
        first = next(
            run for run in self.mapping if run.normalized_start <= start < run.normalized_end
        )
        last = next(run for run in self.mapping if run.normalized_start < end <= run.normalized_end)
        left = first.extracted_start + start - first.normalized_start
        right = last.extracted_end - (last.normalized_end - end)
        return left, right


def normalize(text: str, revision: str) -> NormalizedText:
    """Conservative normalization; compact exact source mapping, including CRLF expansion."""
    if revision not in {"identity-v1", "line-endings-v1"}:
        raise ValueError("Unsupported normalizer revision")
    output: list[str] = []
    runs: list[OffsetRun] = []
    source = 0
    while source < len(text):
        width = 2 if revision == "line-endings-v1" and text[source : source + 2] == "\r\n" else 1
        char = text[source]
        if revision == "line-endings-v1":
            char = {"\r": "\n", "\u00a0": " "}.get(char, char)
        position = len(output)
        output.append(char)
        if (
            width == 1
            and runs
            and (
                runs[-1].normalized_end - runs[-1].normalized_start
                == runs[-1].extracted_end - runs[-1].extracted_start
            )
        ):
            previous = runs.pop()
            runs.append(
                OffsetRun(
                    normalized_start=previous.normalized_start,
                    normalized_end=position + 1,
                    extracted_start=previous.extracted_start,
                    extracted_end=source + 1,
                )
            )
        else:
            runs.append(
                OffsetRun(
                    normalized_start=position,
                    normalized_end=position + 1,
                    extracted_start=source,
                    extracted_end=source + width,
                )
            )
        if len(runs) > 4096:
            raise ValueError("Normalization mapping exceeds safe complexity")
        source += width
    return NormalizedText(text="".join(output), mapping=runs)


class ExtractedPage(IngestionModel):
    number: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class ExtractionArtifact(IngestionModel):
    id: UUID
    document_id: UUID
    version: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: ExtractionConfig
    parser: Literal["utf8", "pypdf"]
    parser_revision: str
    extracted_text: str = Field(min_length=1, max_length=200000)
    pages: list[ExtractedPage] = Field(min_length=1, max_length=100)
    normalized: NormalizedText
    processed_at: datetime
    # Coordinates/heading/table structure are unavailable; extracted layout is best effort.
    coordinate_system: Literal["extracted-unicode-codepoints"] = "extracted-unicode-codepoints"

    @model_validator(mode="after")
    def provenance(self) -> Self:
        identity = f"{self.version}:{self.source_sha256}:{self.config.digest}"
        if self.id != uuid5(self.document_id, identity):
            raise ValueError("Extraction identity disagrees with source/configuration")
        if self.processed_at.tzinfo is None or self.processed_at.utcoffset() is None:
            raise ValueError("Processing timestamp must be aware")
        if self.normalized != normalize(self.extracted_text, self.config.normalization):
            raise ValueError("Normalization provenance differs from source text")
        position = 0
        for index, page in enumerate(self.pages):
            if page.number != index + 1 or page.start != position or page.end < page.start:
                raise ValueError("Page boundaries are invalid")
            if page.end > len(self.extracted_text):
                raise ValueError("Page extends beyond text")
            position = page.end
            if index < len(self.pages) - 1:
                if self.extracted_text[position : position + 1] != "\f":
                    raise ValueError("Page separator is missing")
                position += 1
        if position != len(self.extracted_text) or len(self.pages) > self.config.max_pages:
            raise ValueError("Pages do not cover source text")
        if len(self.extracted_text) > self.config.max_chars or "\x00" in self.extracted_text:
            raise ValueError("Extracted text exceeds policy")
        if self.parser_revision != (
            self.config.pdf_parser_revision if self.parser == "pypdf" else "utf8-v1"
        ):
            raise ValueError("Parser revision disagrees with requested configuration")
        return self
