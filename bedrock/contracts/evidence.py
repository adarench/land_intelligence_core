"""Evidence traceability contracts."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from .base import BedrockModel, EngineMetadata


class Evidence(BedrockModel):
    evidence_id: str
    source_type: str
    document_id: str
    section: Optional[str] = None
    page: Optional[int] = Field(default=None, ge=1)
    text: str
    confidence: float = Field(ge=0, le=1)
    metadata: Optional[EngineMetadata] = None
