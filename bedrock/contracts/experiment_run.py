"""Canonical support contract for experiment grouping over pipeline runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import Field

from .base import BedrockModel


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperimentRun(BedrockModel):
    """Authoritative experiment record used by experiment APIs."""

    schema_name: str = Field(default="ExperimentRun", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    experiment_id: str
    run_ids: list[str]
    config: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_now_iso)
