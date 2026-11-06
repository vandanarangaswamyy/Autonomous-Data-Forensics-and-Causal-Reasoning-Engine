from typing import Protocol
from uuid import UUID

from atlas_contracts.extraction import ExtractionArtifact, ExtractionConfig


class DocumentExtractor(Protocol):
    async def extract(
        self,
        document_id: UUID,
        version: int,
        content: bytes,
        media_type: str,
        config: ExtractionConfig,
    ) -> ExtractionArtifact: ...
