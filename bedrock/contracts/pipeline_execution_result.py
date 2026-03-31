"""Governed internal support contract for pipeline service execution results."""

from __future__ import annotations

from typing import Optional

from pydantic import AliasChoices, Field

from .base import BedrockModel
from .feasibility_result import FeasibilityResult
from .near_feasible_result import NearFeasibleResult


class PipelineExecutionResult(BedrockModel):
    """Internal orchestration result object; not a public API payload."""

    schema_name: str = Field(default="PipelineExecutionResult", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    run_id: str
    status: str
    feasibility_result: Optional[FeasibilityResult] = Field(
        default=None,
        validation_alias=AliasChoices("feasibility_result", "feasibility"),
    )
    near_feasible_result: Optional[NearFeasibleResult] = None

    @property
    def feasibility(self) -> FeasibilityResult:
        """Compatibility alias for older internal callers."""

        return self.feasibility_result
