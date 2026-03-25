"""Canonical scenario evaluation contract for ranked layout feasibility."""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field

from .base import BedrockModel
from .feasibility_result import FeasibilityResult


class ScenarioEvaluation(BedrockModel):
    """Ranked feasibility summary across one or more layout candidates."""

    schema_name: str = Field(default="ScenarioEvaluation", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    parcel_id: str
    layout_count: int = Field(ge=0)
    best_layout_id: Optional[str] = None
    best_roi: Optional[float] = None
    best_profit: Optional[float] = None
    best_units: Optional[int] = Field(default=None, ge=0)
    layouts_ranked: List[FeasibilityResult] = Field(default_factory=list)
