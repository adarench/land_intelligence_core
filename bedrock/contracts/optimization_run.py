"""Canonical optimization run contract for multi-layout parcel evaluation."""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import Field, model_validator

from .base import BedrockModel
from .feasibility_result import FeasibilityResult
from .layout_candidate_batch import LayoutSearchPlan
from .layout_result import LayoutResult
from .scenario_evaluation import ScenarioEvaluation
from .zoning_rules import ZoningRules


class OptimizationObjective(BedrockModel):
    """Configurable scoring weights for candidate ranking."""

    roi_weight: float = Field(default=0.45, ge=0, le=1)
    profit_weight: float = Field(default=0.25, ge=0, le=1)
    risk_weight: float = Field(default=0.20, ge=0, le=1)
    confidence_weight: float = Field(default=0.10, ge=0, le=1)
    min_roi: Optional[float] = None
    max_risk: Optional[float] = Field(default=None, ge=0, le=1)
    improvement_epsilon: float = Field(default=0.01, ge=0)
    target_score: float = Field(default=0.5)

    @model_validator(mode="after")
    def _validate_weights(self) -> "OptimizationObjective":
        total = self.roi_weight + self.profit_weight + self.risk_weight + self.confidence_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError("OptimizationObjective weights must sum to 1.0")
        return self


class OptimizationCandidate(BedrockModel):
    """One layout + feasibility evaluation inside an optimization run."""

    layout_result: LayoutResult
    feasibility_result: FeasibilityResult
    strategy_parameters: LayoutSearchPlan
    objective_score: float
    optimization_rank: int = Field(ge=1)


class CandidateScoreStats(BedrockModel):
    count: int = Field(ge=0)
    min_score: Optional[float] = None
    p25_score: Optional[float] = None
    median_score: Optional[float] = None
    p75_score: Optional[float] = None
    max_score: Optional[float] = None


class OptimizationIteration(BedrockModel):
    iteration_index: int = Field(ge=1)
    search_plans: List[LayoutSearchPlan] = Field(default_factory=list)
    candidate_count: int = Field(ge=0)
    best_layout_id: Optional[str] = None
    best_objective_score: Optional[float] = None
    best_roi: Optional[float] = None
    best_projected_profit: Optional[float] = None
    improvement_from_prior: Optional[float] = None
    score_distribution: CandidateScoreStats = Field(default_factory=lambda: CandidateScoreStats(count=0))


class ConvergenceMetrics(BedrockModel):
    iteration_count: int = Field(ge=0)
    plateau_reached: bool = False
    stopped_reason: Optional[str] = None
    improvement_curve: List[float] = Field(default_factory=list)
    candidate_score_distribution: CandidateScoreStats = Field(default_factory=lambda: CandidateScoreStats(count=0))


class SensitivityBreakpoint(BedrockModel):
    variable: str
    current_value: Optional[float] = None
    break_even_value: Optional[float] = None
    margin_value: Optional[float] = None
    margin_percent: Optional[float] = None
    explanation: str


class CandidateSensitivity(BedrockModel):
    layout_id: str
    status: Literal["best_candidate", "near_feasible", "failing_candidate"]
    primary_failure_reason: str
    margin_to_feasibility: float
    make_it_work_statement: str
    breakpoints: List[SensitivityBreakpoint] = Field(default_factory=list)


class EconomicScenario(BedrockModel):
    scenario_name: str
    scenario_type: Literal["land_price_sweep", "density_curve", "rezoning"]
    status: Literal["evaluated", "not_applicable"]
    best_layout_id: Optional[str] = None
    best_roi: Optional[float] = None
    best_projected_profit: Optional[float] = None
    delta_roi: Optional[float] = None
    delta_profit: Optional[float] = None
    recommended_max_offer_price: Optional[float] = None
    curve: List[dict[str, Any]] = Field(default_factory=list)
    explanation: Optional[str] = None


class OptimizationDecision(BedrockModel):
    """Decision-oriented summary derived from the best candidate."""

    recommendation: Literal["acquire", "renegotiate_price", "pursue_rezoning", "abandon"]
    action: Optional[Literal["acquire", "renegotiate_price", "pursue_rezoning", "abandon"]] = None
    best_layout_id: Optional[str] = None
    expected_roi_base: Optional[float] = None
    expected_roi_best_case: Optional[float] = None
    expected_roi_worst_case: Optional[float] = None
    sensitivity: List[str] = Field(default_factory=list)
    key_risks: List[str] = Field(default_factory=list)
    breakpoints: List[SensitivityBreakpoint] = Field(default_factory=list)
    target_price: Optional[float] = None
    reason: Optional[str] = None
    alternative: Optional[str] = None
    rationale: Optional[str] = None

    @model_validator(mode="after")
    def _sync_action(self) -> "OptimizationDecision":
        if self.action is None:
            object.__setattr__(self, "action", self.recommendation)
        return self


class OptimizationRun(BedrockModel):
    """Persisted multi-scenario optimization artifact."""

    schema_name: str = Field(default="OptimizationRun", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    optimization_run_id: str
    parcel_id: str
    zoning_result: ZoningRules
    layout_candidates: List[OptimizationCandidate] = Field(default_factory=list)
    best_candidate: Optional[OptimizationCandidate] = None
    ranking_metrics: dict[str, Any] = Field(default_factory=dict)
    objective: OptimizationObjective
    scenario_evaluation: Optional[ScenarioEvaluation] = None
    iterations: List[OptimizationIteration] = Field(default_factory=list)
    convergence_metrics: Optional[ConvergenceMetrics] = None
    sensitivity_analysis: List[CandidateSensitivity] = Field(default_factory=list)
    economic_scenarios: List[EconomicScenario] = Field(default_factory=list)
    decision: Optional[OptimizationDecision] = None
    selected_pipeline_run_id: Optional[str] = None
    timestamp: str
    git_commit: Optional[str] = None
    input_hash: Optional[str] = None
    stage_runtimes: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_best_candidate(self) -> "OptimizationRun":
        if self.best_candidate is not None and not self.layout_candidates:
            raise ValueError("OptimizationRun.best_candidate requires layout_candidates")
        return self
