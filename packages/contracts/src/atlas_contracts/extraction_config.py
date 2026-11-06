import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    implementation: Literal["source-extraction-v1"] = "source-extraction-v1"
    pdf_parser_revision: Literal["6.21.4"] = "6.21.4"
    normalization: Literal["identity-v1", "line-endings-v1"] = "line-endings-v1"
    max_pages: int = Field(default=50, ge=1, le=100)
    max_chars: int = Field(default=200000, ge=1, le=200000)
    timeout_seconds: float = Field(default=10, ge=0.05, le=15)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
