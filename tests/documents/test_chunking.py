from datetime import UTC, datetime
from uuid import uuid4

import pytest
from atlas_contracts.documents import (
    ChunkConfig,
    DocumentKind,
    IngestionError,
    SourceDescriptor,
    SourceSubmission,
)
from atlas_documents.chunking import decode, segment
from pydantic import ValidationError


def submission(
    content: bytes = b"orders schema", uri: str = "atlas://docs/orders"
) -> SourceSubmission:
    return SourceSubmission(
        descriptor=SourceDescriptor(
            collection="retail",
            source_uri=uri,
            source_version="v1",
            kind=DocumentKind.TECHNICAL,
            media_type="text/plain",
            observed_at=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        content=content,
    )


def test_identity_hash_and_provenance_contract() -> None:
    first, same = submission(), submission()
    assert first.descriptor.document_id == same.descriptor.document_id
    assert first.content_sha256 == same.content_sha256
    assert first.revision_sha256 == same.revision_sha256
    assert submission(b"new").descriptor.document_id == first.descriptor.document_id
    assert submission(b"new").content_sha256 != first.content_sha256
    assert (
        submission(uri="atlas://docs/payments").descriptor.document_id
        != first.descriptor.document_id
    )
    changed_collection = first.descriptor.model_copy(update={"collection": "other"})
    assert changed_collection.document_id != first.descriptor.document_id
    assert "orders schema" not in repr(first)


@pytest.mark.parametrize(
    "uri",
    [
        "file:///etc/passwd",
        "https://user:secret@host/path",
        "https://host/path?token=secret",
        "https://host/path#secret",
        " x",
    ],
)
def test_reject_sensitive_or_ambiguous_locator(uri: str) -> None:
    with pytest.raises(ValidationError):
        submission(uri=uri)


@pytest.mark.parametrize(
    "data,code",
    [(b"\xff", "invalid_encoding"), (b"  ", "invalid_content"), (b"abc\x00def", "invalid_content")],
)
def test_decode_errors_are_structured(data: bytes, code: str) -> None:
    with pytest.raises(IngestionError) as error:
        decode(data)
    assert error.value.code == code


@pytest.mark.parametrize("strategy", ["recursive", "markdown"])
def test_chunk_coverage_stability_and_offsets(strategy: str) -> None:
    raw = (
        "# Guide\r\n\nÜber payments.\n" + "large line " * 100 + "\n## Schema\npublic.orders\n"
    ).encode()
    request = submission(raw)
    config = ChunkConfig(strategy=strategy, max_chars=64)
    text = decode(raw)
    chunks = segment(text, request.descriptor.document_id, 1, request.content_sha256, config)
    assert len(chunks) > 2
    assert chunks == segment(
        text, request.descriptor.document_id, 1, request.content_sha256, config
    )
    assert "".join(c.text for c in chunks) == text
    assert all(c.text == text[c.start : c.end] and len(c.text) <= 64 for c in chunks)
    assert all(c.content_sha256 == request.content_sha256 for c in chunks)
    assert all(c.processor_sha256 == config.digest for c in chunks)
    assert len({c.id for c in chunks}) == len(chunks)
    different = segment(text, request.descriptor.document_id, 2, request.content_sha256, config)
    assert not {c.id for c in chunks} & {c.id for c in different}


def test_markdown_hierarchy_ignores_fenced_headings() -> None:
    text = "# A\nintro\n```sql\n## Not a section\n```\n## B\nbody\n# C\nend"
    chunks = segment(text, uuid4(), 1, "a" * 64, ChunkConfig(strategy="markdown"))
    assert [c.section_path for c in chunks] == [["A"], ["A", "B"], ["C"]]
    assert "".join(c.text for c in chunks) == text


def test_source_limits_and_unknown_media_fail_closed() -> None:
    with pytest.raises(ValidationError):
        submission(b"x" * 1_000_001)
    with pytest.raises(ValidationError):
        SourceDescriptor.model_validate(
            {**submission().descriptor.model_dump(), "media_type": "application/zip"}
        )
    with pytest.raises(ValidationError):
        SourceDescriptor.model_validate(
            {**submission().descriptor.model_dump(), "observed_at": datetime(2025, 1, 1)}
        )
