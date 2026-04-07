"""Base types used across Bedrock contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BedrockModel(BaseModel):
    """Base model with strict validation and future-proof defaults."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )


class EngineMetadata(BedrockModel):
    """Optional metadata describing how a contract was produced."""

    source_engine: Optional[str] = None
    source_run_id: Optional[str] = None
    source_type: Optional[str] = None
    rule_completeness: Optional[float] = Field(default=None, ge=0, le=1)
    legal_reliability: Optional[bool] = None
    match_classification: Optional[str] = None
    overlap_ratio: Optional[float] = Field(default=None, ge=0)
    selection_method: Optional[str] = None
    observed_at: datetime = Field(default_factory=utc_now)


Geometry = Dict[str, Any]
