import json
from pathlib import Path
from uuid import uuid4

import pytest
from atlas_contracts.documents import IngestionError
from atlas_contracts.extraction import ExtractionConfig, normalize
from atlas_core.extraction import DocumentExtractor
from atlas_documents.extraction import SourceExtractor
from pydantic import ValidationError

LABELS = json.loads(Path("evaluation/nlp/source-v1/labels.json").read_text())


@pytest.mark.parametrize("case", LABELS["normalization"])
def test_manually_labeled_normalization(case: dict[str, str | int]) -> None:
    result = normalize(str(case["source"]), "line-endings-v1")
    assert result.text == case["normalized"]
    assert result.source_span(int(case["start"]), int(case["end"])) == (
        case["source_start"],
        case["source_end"],
    )


def test_identity_mapping_and_span_bounds() -> None:
    text = "\ufeffA\r\nB\u00a0é"
    result = normalize(text, "identity-v1")
    assert result.text == text and result.source_span(0, len(text)) == (0, len(text))
    for start, end in [(-1, 1), (0, 0), (0, len(text) + 1)]:
        with pytest.raises(ValueError):
            result.source_span(start, end)
    with pytest.raises(ValueError):
        normalize(text, "unknown-model")
    with pytest.raises(ValueError):
        normalize("a\r\n" * 3000, "line-endings-v1")


async def test_real_utf8_extractor_contract_and_tamper_validation() -> None:
    extractor: DocumentExtractor = SourceExtractor()
    document_id = uuid4()
    artifact = await extractor.extract(document_id, 1, b"A\r\nB", "text/plain", ExtractionConfig())
    assert artifact.document_id == document_id and artifact.parser_revision == "utf8-v1"
    assert artifact.normalized.text == "A\nB" and artifact.extracted_text == "A\r\nB"
    assert artifact.pages[0].number == 1
    from atlas_contracts.extraction import ExtractionArtifact

    changes: tuple[dict[str, object], ...] = (
        {"source_sha256": "a" * 64},
        {"pages": []},
        {"parser_revision": "fictional"},
        {"normalized": {"text": "fake", "mapping": []}},
    )
    for change in changes:
        with pytest.raises(ValidationError):
            ExtractionArtifact.model_validate({**artifact.model_dump(), **change})


@pytest.mark.parametrize(
    "content,media,code",
    [
        (b"valid", "application/zip", "unsupported_format"),
        (b"x" * 1_000_001, "text/plain", "source_too_large"),
        (b"bad\x00text", "text/plain", "invalid_content"),
        (b"\xff", "text/plain", "invalid_encoding"),
    ],
)
async def test_source_extraction_limits(content: bytes, media: str, code: str) -> None:
    with pytest.raises(IngestionError) as error:
        await SourceExtractor().extract(uuid4(), 1, content, media, ExtractionConfig())
    assert error.value.code == code


async def test_configured_text_limits_and_rule_revision() -> None:
    extractor = SourceExtractor()
    with pytest.raises(IngestionError) as error:
        await extractor.extract(
            uuid4(), 1, b"too long", "text/plain", ExtractionConfig(max_chars=2)
        )
    assert error.value.code == "text_limit"
    document_id = uuid4()
    first = await extractor.extract(document_id, 1, b"A\r\nB", "text/plain", ExtractionConfig())
    second = await extractor.extract(
        document_id, 1, b"A\r\nB", "text/plain", ExtractionConfig(normalization="identity-v1")
    )
    assert first.id != second.id and first.extracted_text == second.extracted_text
    assert first.normalized.text != second.normalized.text
