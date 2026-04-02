"""Canonical pipeline execution output contract."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from .base import BedrockModel
from .feasibility_result import FeasibilityResult
from .layout_result import LayoutResult
from .near_feasible_result import NearFeasibleResult
from .zoning_rules import ZoningRules


class PipelineRun(BedrockModel):
    """Authoritative output contract for POST /pipeline/run."""

    schema_name: str = Field(default="PipelineRun", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    run_id: str
    status: str = "completed"
    parcel_id: str
    zoning_result: ZoningRules
    layout_result: Optional[LayoutResult] = None
    feasibility_result: Optional[FeasibilityResult] = None
    near_feasible_result: Optional[NearFeasibleResult] = None
    inferred_analysis: Optional[dict] = None
    timestamp: str
    git_commit: Optional[str] = None
    input_hash: Optional[str] = None
    stage_runtimes: dict[str, float] = Field(default_factory=dict)
    zoning_bypassed: bool = False
    bypass_reason: Optional[str] = None
