"""Canonical batch contract for multi-candidate layout search."""

from __future__ import annotations

from typing import Any, List

from pydantic import Field

from .base import BedrockModel
from .layout_result import LayoutResult


class LayoutSearchPlan(BedrockModel):
    """Deterministic parameterization for one candidate search batch."""

    label: str
    strategies: List[str] = Field(default_factory=list)
    max_candidates: int = Field(default=24, ge=1, le=48)
    max_layouts: int = Field(default=3, ge=1, le=10)
    density_factor: float = Field(default=1.0, ge=0.25, le=1.0)
    lot_depth_factor: float = Field(default=1.0, ge=0.8, le=1.5)
    frontage_hint_factor: float = Field(default=1.0, ge=0.85, le=1.2)
    road_width_factor: float = Field(default=1.0, ge=0.8, le=1.2)
    runtime_budget_factor: float = Field(default=1.0, ge=0.8, le=1.5)

    def search_overrides(self) -> dict[str, float]:
        return {
            "density_factor": self.density_factor,
            "lot_depth_factor": self.lot_depth_factor,
            "frontage_hint_factor": self.frontage_hint_factor,
            "road_width_factor": self.road_width_factor,
            "runtime_budget_factor": self.runtime_budget_factor,
        }


class LayoutCandidateBatch(BedrockModel):
    """Validated layout candidates produced by a single search plan."""

    schema_name: str = Field(default="LayoutCandidateBatch", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    parcel_id: str
    search_plan: LayoutSearchPlan
    candidate_count_generated: int = Field(default=0, ge=0)
    candidate_count_valid: int = Field(default=0, ge=0)
    layouts: List[LayoutResult] = Field(default_factory=list)
    search_debug: dict[str, Any] = Field(default_factory=dict)
