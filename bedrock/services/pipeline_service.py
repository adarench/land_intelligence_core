"""Pipeline orchestration for parcel -> zoning -> layout -> feasibility execution."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from shapely.geometry import shape

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.base import EngineMetadata
from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout_candidate_batch import LayoutCandidateBatch, LayoutSearchPlan
from bedrock.contracts.layout_result import LayoutResult
from bedrock.contracts.market_data import MarketData
from bedrock.contracts.near_feasible_result import NearFeasibleResult
from bedrock.contracts.optimization_run import (
    CandidateScoreStats,
    CandidateSensitivity,
    ConvergenceMetrics,
    EconomicScenario,
    SensitivityBreakpoint,
    OptimizationCandidate,
    OptimizationDecision,
    OptimizationIteration,
    OptimizationObjective,
    OptimizationRun,
)
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.pipeline_execution_result import PipelineExecutionResult
from bedrock.contracts.scenario_evaluation import ScenarioEvaluation
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.contracts.validators import (
    validate_feasibility_pipeline_contracts,
    validate_optimization_run_output,
    validate_feasibility_result_output,
    validate_layout_result_output,
    validate_parcel_output,
    validate_zoning_rules_for_layout,
)
from bedrock.services.feasibility_service import FeasibilityService, evaluate_near_feasible_upside
from bedrock.services.layout_service import (
    LayoutSearchError,
    _near_feasible_result,
    search_layout_candidates_debug,
    search_subdivision_layout,
)
from bedrock.services.parcel_service import ParcelService
from bedrock.services.pipeline_run_store import PipelineRunStore
from bedrock.services.zoning_rule_normalizer import normalize_rules as normalize_zoning_rules
from bedrock.services.zoning_service import IncompleteZoningRulesError, InvalidZoningRulesError, ZoningService
from zoning_data_scraper.services.zoning_overlay import (
    AmbiguousJurisdictionMatchError,
    AmbiguousZoningMatchError,
    NoJurisdictionMatchError,
    NoZoningMatchError,
)

logger = logging.getLogger(__name__)

NON_BUILDABLE_DISTRICTS_ENABLED = True
NON_BUILDABLE_DISTRICTS = (
    {"jurisdiction": "provo", "district": "RC", "reason": "historical_constraint"},
    {"jurisdiction": "murray", "district": "M-G", "reason": "non_residential"},
)


class PipelineStageError(RuntimeError):
    """Structured stage failure surfaced by pipeline orchestration."""

    def __init__(
        self,
        *,
        stage: str,
        error: str,
        message: str,
        status_code: int,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.error = error
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "error": self.error,
            "stage": self.stage,
            "message": self.message,
            **self.details,
        }


class PipelineRunLog(BedrockModel):
    run_id: str
    status: str = "completed"
    parcel_id: str
    zoning_district: str
    zoning_source: str
    zoning_geometry_match_success: bool
    zoning_bypassed: bool = False
    bypass_reason: Optional[str] = None
    layout_units: Optional[int] = None
    layout_score: Optional[float] = None
    feasibility_roi: Optional[float] = None
    timestamp: datetime


class PipelineRunRecord(BedrockModel):
    run_id: str
    timestamp: datetime
    status: str = "completed"
    parcel: Parcel
    zoning: ZoningRules
    layout: Optional[LayoutResult] = None
    feasibility: Optional[FeasibilityResult] = None
    near_feasible_result: Optional[NearFeasibleResult] = None
    inferred_analysis: Optional[dict] = None
    git_commit: Optional[str] = None
    input_hash: Optional[str] = None
    stage_runtimes: dict[str, float] = {}
    zoning_bypassed: bool = False
    bypass_reason: Optional[str] = None


class ZoningStageResult(BedrockModel):
    rules: ZoningRules
    status: str = "completed"
    bypass_reason: Optional[str] = None

    @property
    def is_bypassed(self) -> bool:
        return self.status != "completed"


def _classify_zoning_bypass(zoning_rules) -> str:
    """Return a structured failure category for why zoning was bypassed."""
    meta = zoning_rules.metadata
    if meta is None:
        return "NO_METADATA"
    source = str(meta.source_run_id or "unknown")
    source_type = str(meta.source_type or "unknown")
    legal = bool(meta.legal_reliability) if meta.legal_reliability is not None else False

    # Check for match_classification from the new overlap-based system
    match_class = getattr(meta, "match_classification", None)
    if match_class == "LOW_CONFIDENCE":
        return "LOW_INTERSECTION_AREA"
    if match_class == "INFERRED":
        return "CENTROID_FALLBACK"

    if source in ("jurisdiction_fallback", "safe_minimum_viable"):
        return "NO_INTERSECTION"
    if "precomputed_district_index" in source:
        return "LOW_INTERSECTION_AREA"
    if source_type != "real_lookup":
        return "NO_INTERSECTION"
    if not legal:
        return "LOW_LEGAL_RELIABILITY"
    return "UNKNOWN_BYPASS"


class PipelineService:
    """Compose the canonical PO-2 execution chain."""

    def __init__(
        self,
        dataset_root: Optional[Path] = None,
        run_store: Optional[PipelineRunStore] = None,
    ) -> None:
        self.parcel_service = ParcelService()
        self.zoning_service = ZoningService(dataset_root=dataset_root)
        self.feasibility_service = FeasibilityService()
        self.run_store = run_store or PipelineRunStore()

    def run(
        self,
        *,
        parcel: Optional[Parcel] = None,
        parcel_geometry: Optional[dict] = None,
        max_candidates: int = 50,
        parcel_id: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        market_data: Optional[MarketData] = None,
    ) -> PipelineExecutionResult:
        stage_runtimes: dict[str, float] = {}
        input_hash = self._build_input_hash(
            parcel=parcel,
            parcel_geometry=parcel_geometry,
            max_candidates=max_candidates,
            parcel_id=parcel_id,
            jurisdiction=jurisdiction,
            market_data=market_data,
        )

        stage_started = time.perf_counter()
        parcel_contract = self._load_parcel_stage(
            parcel=parcel,
            parcel_geometry=parcel_geometry,
            parcel_id=parcel_id,
            jurisdiction=jurisdiction,
        )
        stage_runtimes["parcel.load"] = round(time.perf_counter() - stage_started, 6)

        stage_started = time.perf_counter()
        zoning_stage = self._lookup_zoning_stage(parcel_contract)
        zoning_rules = zoning_stage.rules
        stage_runtimes["zoning.lookup"] = round(time.perf_counter() - stage_started, 6)

        if not parcel_contract.zoning_district and zoning_rules.district:
            parcel_contract.zoning_district = zoning_rules.district

        status = zoning_stage.status
        zoning_bypassed = zoning_stage.is_bypassed
        bypass_reason = zoning_stage.bypass_reason
        layout_result: Optional[LayoutResult] = None
        feasibility_result: Optional[FeasibilityResult] = None
        near_feasible_result: Optional[NearFeasibleResult] = None
        inferred_analysis: Optional[dict] = None
        if zoning_bypassed:
            near_feasible_result = self._build_bypassed_near_feasible_result(
                parcel_contract,
                zoning_rules,
                status=status,
                bypass_reason=bypass_reason,
            )
            status = "near_feasible"
            try:
                from bedrock.services.inference_service import run_inference
                zoning_hint = {
                    "district": zoning_rules.district,
                    "max_units_per_acre": zoning_rules.max_units_per_acre,
                    "min_lot_size_sqft": zoning_rules.min_lot_size_sqft,
                }
                inferred_analysis = run_inference(parcel_contract, zoning_hint=zoning_hint)
                stage_runtimes["inference.analyze"] = 0.0
            except Exception:
                pass
        else:
            stage_started = time.perf_counter()
            try:
                layout_result = self._search_layout_stage(
                    parcel_contract,
                    zoning_rules,
                    max_candidates=max_candidates,
                )
                stage_runtimes["layout.search"] = round(time.perf_counter() - stage_started, 6)

                stage_started = time.perf_counter()
                feasibility_result = self._evaluate_feasibility_stage(
                    parcel_contract,
                    zoning_rules,
                    layout_result,
                    market_data=market_data,
                )
                stage_runtimes["feasibility.evaluate"] = round(time.perf_counter() - stage_started, 6)
            except PipelineStageError as exc:
                stage_runtimes["layout.search"] = round(time.perf_counter() - stage_started, 6)
                near_feasible_payload = self._build_near_feasible_result(
                    parcel_contract,
                    zoning_rules,
                    exc,
                )
                if near_feasible_payload is None:
                    raise
                near_feasible_result = near_feasible_payload
                status = "near_feasible"
                if inferred_analysis is None:
                    try:
                        from bedrock.services.inference_service import run_inference
                        zoning_hint = {
                            "district": zoning_rules.district,
                            "max_units_per_acre": zoning_rules.max_units_per_acre,
                            "min_lot_size_sqft": zoning_rules.min_lot_size_sqft,
                        }
                        inferred_analysis = run_inference(parcel_contract, zoning_hint=zoning_hint)
                    except Exception:
                        pass
        run_id = str(uuid4())
        timestamp = datetime.now(timezone.utc)
        run_record = PipelineRunRecord(
            run_id=run_id,
            timestamp=timestamp,
            status=status,
            parcel=parcel_contract,
            zoning=zoning_rules,
            layout=layout_result,
            feasibility=feasibility_result,
            near_feasible_result=near_feasible_result,
            inferred_analysis=inferred_analysis,
            git_commit=self._resolve_git_commit(),
            input_hash=input_hash,
            stage_runtimes=stage_runtimes,
            zoning_bypassed=zoning_bypassed,
            bypass_reason=bypass_reason,
        )
        self.run_store.save_run(run_id, run_record)

        run_log = PipelineRunLog(
            run_id=run_id,
            status=status,
            parcel_id=parcel_contract.parcel_id,
            zoning_district=zoning_rules.district,
            zoning_source=str(zoning_rules.metadata.source_run_id if zoning_rules.metadata is not None else "unknown"),
            zoning_geometry_match_success=True,
            zoning_bypassed=zoning_bypassed,
            bypass_reason=bypass_reason,
            layout_units=layout_result.units if layout_result is not None else None,
            layout_score=layout_result.score if layout_result is not None else None,
            feasibility_roi=feasibility_result.ROI if feasibility_result is not None else None,
            timestamp=timestamp,
        )
        self.run_store.save(run_log)
        return PipelineExecutionResult(
            run_id=run_id,
            status=status,
            feasibility_result=feasibility_result,
            near_feasible_result=near_feasible_result,
        )

    def optimize(
        self,
        *,
        parcel: Optional[Parcel] = None,
        parcel_geometry: Optional[dict] = None,
        max_candidates: int = 48,
        parcel_id: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        market_data: Optional[MarketData] = None,
        objective: Optional[OptimizationObjective] = None,
        max_rounds: int = 3,
    ) -> OptimizationRun:
        stage_runtimes: dict[str, float] = {}
        objective = objective or OptimizationObjective()
        input_hash = self._build_input_hash(
            parcel=parcel,
            parcel_geometry=parcel_geometry,
            max_candidates=max_candidates,
            parcel_id=parcel_id,
            jurisdiction=jurisdiction,
            market_data=market_data,
        )

        stage_started = time.perf_counter()
        parcel_contract = self._load_parcel_stage(
            parcel=parcel,
            parcel_geometry=parcel_geometry,
            parcel_id=parcel_id,
            jurisdiction=jurisdiction,
        )
        stage_runtimes["parcel.load"] = round(time.perf_counter() - stage_started, 6)

        stage_started = time.perf_counter()
        zoning_degraded = False
        zoning_degraded_reason: Optional[str] = None
        try:
            zoning_stage = self._lookup_zoning_stage(parcel_contract)
        except PipelineStageError as exc:
            zoning_degraded = True
            zoning_degraded_reason = exc.message
            zoning_stage = self._build_unsupported_zoning_stage(parcel_contract, reason=exc.error)
        zoning_rules = zoning_stage.rules
        stage_runtimes["zoning.lookup"] = round(time.perf_counter() - stage_started, 6)

        if not parcel_contract.zoning_district and zoning_rules.district:
            parcel_contract.zoning_district = zoning_rules.district

        if zoning_stage.is_bypassed or zoning_degraded:
            bypass_label = zoning_degraded_reason or zoning_stage.bypass_reason or "zoning_insufficient"
            fallback_result = self.run(
                parcel=parcel_contract,
                max_candidates=max_candidates,
                market_data=market_data,
            )
            timestamp = datetime.now(timezone.utc)
            optimization_run_id = str(uuid4())
            record = OptimizationRun(
                optimization_run_id=optimization_run_id,
                parcel_id=parcel_contract.parcel_id,
                zoning_result=zoning_rules,
                layout_candidates=[],
                best_candidate=None,
                ranking_metrics={"candidate_count": 0},
                objective=objective,
                iterations=[],
                convergence_metrics=ConvergenceMetrics(iteration_count=0, plateau_reached=False, stopped_reason="zoning_degraded"),
                sensitivity_analysis=[],
                economic_scenarios=[],
                decision=OptimizationDecision(
                    recommendation="abandon",
                    action="abandon",
                    sensitivity=["zoning_data_insufficient"],
                    key_risks=["zoning_data_insufficient"],
                    reason=f"Zoning data insufficient for optimization ({bypass_label}). Basic pipeline ran instead.",
                    rationale="No optimization was attempted because zoning data does not meet decision-grade requirements. The basic pipeline produced a near-feasible or fallback result.",
                ),
                selected_pipeline_run_id=fallback_result.run_id,
                timestamp=timestamp.isoformat(),
                git_commit=self._resolve_git_commit(),
                input_hash=input_hash,
                stage_runtimes=stage_runtimes,
            )
            self.run_store.save_optimization_run(optimization_run_id, record)
            return validate_optimization_run_output(record)

        stage_started = time.perf_counter()
        optimization = self._optimize_layout_scenarios(
            parcel_contract,
            zoning_rules,
            market_data=market_data,
            max_candidates=max_candidates,
            objective=objective,
            max_rounds=max_rounds,
        )
        stage_runtimes.update(optimization["stage_runtimes"])
        stage_runtimes["optimization.total"] = round(time.perf_counter() - stage_started, 6)

        timestamp = datetime.now(timezone.utc)
        best_candidate = optimization["best_candidate"]
        selected_pipeline_run_id = None
        if best_candidate is not None:
            selected_pipeline_run_id = self._persist_best_optimization_candidate(
                parcel_contract,
                zoning_rules,
                best_candidate,
                timestamp=timestamp,
                input_hash=input_hash,
            )
        else:
            fallback_result = self.run(
                parcel=parcel_contract,
                max_candidates=max_candidates,
                market_data=market_data,
            )
            selected_pipeline_run_id = fallback_result.run_id

        optimization_run_id = str(uuid4())
        record = OptimizationRun(
            optimization_run_id=optimization_run_id,
            parcel_id=parcel_contract.parcel_id,
            zoning_result=zoning_rules,
            layout_candidates=optimization["ranked_candidates"],
            best_candidate=best_candidate,
            ranking_metrics=optimization["ranking_metrics"],
            objective=objective,
            scenario_evaluation=optimization["scenario_evaluation"],
            iterations=optimization["iterations"],
            convergence_metrics=optimization["convergence_metrics"],
            sensitivity_analysis=optimization["sensitivity_analysis"],
            economic_scenarios=optimization["economic_scenarios"],
            decision=self._build_optimization_decision(
                best_candidate=best_candidate,
                zoning_rules=zoning_rules,
                ranking_metrics=optimization["ranking_metrics"],
                sensitivity_analysis=optimization["sensitivity_analysis"],
                economic_scenarios=optimization["economic_scenarios"],
            ),
            selected_pipeline_run_id=selected_pipeline_run_id,
            timestamp=timestamp.isoformat(),
            git_commit=self._resolve_git_commit(),
            input_hash=input_hash,
            stage_runtimes=stage_runtimes,
        )
        self.run_store.save_optimization_run(optimization_run_id, record)
        return validate_optimization_run_output(record)

    def _optimize_layout_scenarios(
        self,
        parcel: Parcel,
        zoning_rules: ZoningRules,
        *,
        market_data: Optional[MarketData],
        max_candidates: int,
        objective: OptimizationObjective,
        max_rounds: int,
    ) -> dict[str, object]:
        ranked_candidates: list[OptimizationCandidate] = []
        seen_layout_ids: set[str] = set()
        round_plans = self._initial_search_plans(max_candidates=max_candidates)
        round_index = 0
        best_score: float | None = None
        stagnant_rounds = 0
        stage_runtimes: dict[str, float] = {}
        iterations: list[OptimizationIteration] = []
        stopped_reason = "max_rounds_reached"

        while round_plans and round_index < max_rounds:
            guided_plans: list[LayoutSearchPlan] = []
            iteration_candidates: list[OptimizationCandidate] = []
            executed_plans = list(round_plans)
            for plan in round_plans:
                started = time.perf_counter()
                try:
                    batch = search_layout_candidates_debug(parcel, zoning_rules, search_plan=plan)
                except LayoutSearchError as exc:
                    guidance = _near_feasible_result(
                        parcel,
                        zoning_rules,
                        exc,
                        attempted_profiles=[plan.label],
                        attempted_repairs=[],
                    )
                    stage_runtimes[f"layout.search.{plan.label}"] = round(time.perf_counter() - started, 6)
                    if guidance is not None:
                        guided_plans.extend(self._plans_from_near_feasible_guidance(guidance, plan, max_candidates=max_candidates))
                    continue

                stage_runtimes[f"layout.search.{plan.label}"] = round(time.perf_counter() - started, 6)
                if not batch.layouts:
                    continue

                started = time.perf_counter()
                opt_zoning_metadata = None
                if getattr(zoning_rules, "metadata", None) is not None:
                    opt_zoning_metadata = {
                        "source_type": zoning_rules.metadata.source_type,
                        "legal_reliability": zoning_rules.metadata.legal_reliability,
                    }
                feasible_results = self.feasibility_service.evaluate_layouts(
                    parcel,
                    batch.layouts,
                    market_data=market_data,
                    zoning_metadata=opt_zoning_metadata,
                )
                stage_runtimes[f"feasibility.evaluate.{plan.label}"] = round(time.perf_counter() - started, 6)
                layout_index = {layout.layout_id: layout for layout in batch.layouts}

                for feasibility in feasible_results:
                    if feasibility.layout_id not in layout_index:
                        continue
                    if not self._passes_objective_filters(feasibility, objective):
                        continue
                    if feasibility.layout_id in seen_layout_ids:
                        continue
                    seen_layout_ids.add(feasibility.layout_id)
                    candidate = OptimizationCandidate(
                        layout_result=layout_index[feasibility.layout_id],
                        feasibility_result=feasibility,
                        strategy_parameters=plan,
                        objective_score=self._objective_score(feasibility, objective),
                        optimization_rank=1,
                    )
                    ranked_candidates.append(candidate)
                    iteration_candidates.append(candidate)

            ranked_candidates = self._rank_optimization_candidates(ranked_candidates)
            ranked_iteration_candidates = self._rank_optimization_candidates(iteration_candidates)
            current_best = ranked_candidates[0].objective_score if ranked_candidates else None
            previous_best = best_score
            if best_score is None or (current_best is not None and current_best > best_score + objective.improvement_epsilon):
                best_score = current_best
                stagnant_rounds = 0
            else:
                stagnant_rounds += 1

            iterations.append(
                OptimizationIteration(
                    iteration_index=round_index + 1,
                    search_plans=executed_plans,
                    candidate_count=len(ranked_iteration_candidates),
                    best_layout_id=ranked_iteration_candidates[0].layout_result.layout_id if ranked_iteration_candidates else None,
                    best_objective_score=ranked_iteration_candidates[0].objective_score if ranked_iteration_candidates else None,
                    best_roi=ranked_iteration_candidates[0].feasibility_result.ROI if ranked_iteration_candidates else None,
                    best_projected_profit=(
                        ranked_iteration_candidates[0].feasibility_result.projected_profit
                        if ranked_iteration_candidates
                        else None
                    ),
                    improvement_from_prior=(
                        None
                        if current_best is None or previous_best is None
                        else round(current_best - previous_best, 6)
                    ),
                    score_distribution=self._score_distribution(ranked_iteration_candidates),
                )
            )

            if best_score is not None and best_score >= objective.target_score:
                stopped_reason = "target_score_reached"
                break
            if stagnant_rounds >= 2:
                stopped_reason = "plateau_reached"
                break

            if ranked_candidates:
                round_plans = self._refine_search_plans(
                    ranked_candidates[:2],
                    max_candidates=max_candidates,
                )
            else:
                round_plans = guided_plans[:4]
                if not round_plans:
                    stopped_reason = "no_guided_plans"
                    break
            round_index += 1

        scenario_evaluation = ScenarioEvaluation(
            parcel_id=parcel.parcel_id,
            layout_count=len(ranked_candidates),
            best_layout_id=ranked_candidates[0].layout_result.layout_id if ranked_candidates else None,
            best_roi=ranked_candidates[0].feasibility_result.ROI if ranked_candidates else None,
            best_profit=ranked_candidates[0].feasibility_result.projected_profit if ranked_candidates else None,
            best_units=ranked_candidates[0].feasibility_result.units if ranked_candidates else None,
            layouts_ranked=[candidate.feasibility_result for candidate in ranked_candidates],
        )
        convergence_metrics = ConvergenceMetrics(
            iteration_count=len(iterations),
            plateau_reached=stopped_reason == "plateau_reached",
            stopped_reason=stopped_reason,
            improvement_curve=[
                float(iteration.best_objective_score or 0.0)
                for iteration in iterations
            ],
            candidate_score_distribution=self._score_distribution(ranked_candidates),
        )
        sensitivity_analysis = self._build_sensitivity_analysis(
            ranked_candidates=ranked_candidates,
            market_data=market_data,
        )
        economic_scenarios = self._build_economic_scenarios(
            parcel=parcel,
            zoning_rules=zoning_rules,
            ranked_candidates=ranked_candidates,
            market_data=market_data,
            max_candidates=max_candidates,
        )
        return {
            "ranked_candidates": ranked_candidates,
            "best_candidate": ranked_candidates[0] if ranked_candidates else None,
            "scenario_evaluation": scenario_evaluation,
            "ranking_metrics": self._ranking_metrics(ranked_candidates, len(iterations)),
            "iterations": iterations,
            "convergence_metrics": convergence_metrics,
            "sensitivity_analysis": sensitivity_analysis,
            "economic_scenarios": economic_scenarios,
            "stage_runtimes": stage_runtimes,
        }

    @staticmethod
    def _initial_search_plans(*, max_candidates: int) -> list[LayoutSearchPlan]:
        base_budget = min(24, max_candidates)
        return [
            LayoutSearchPlan(
                label="broad_sampling",
                strategies=["grid", "spine-road", "cul-de-sac", "loop_custom", "t_junction"],
                max_candidates=base_budget,
                max_layouts=3,
            ),
            LayoutSearchPlan(
                label="compact_yield",
                strategies=["grid", "spine-road", "herringbone", "loop_custom"],
                max_candidates=base_budget,
                max_layouts=3,
                density_factor=1.0,
                lot_depth_factor=0.92,
                frontage_hint_factor=0.95,
                road_width_factor=0.95,
            ),
            LayoutSearchPlan(
                label="hybrid_spacing",
                strategies=["loop_custom", "spine-road", "grid", "t_junction"],
                max_candidates=base_budget,
                max_layouts=3,
                density_factor=0.9,
                lot_depth_factor=1.1,
                frontage_hint_factor=0.92,
                road_width_factor=0.9,
            ),
        ]

    @staticmethod
    def _refine_search_plans(
        seeds: list[OptimizationCandidate],
        *,
        max_candidates: int,
    ) -> list[LayoutSearchPlan]:
        plans: list[LayoutSearchPlan] = []
        for index, seed in enumerate(seeds[:2], start=1):
            base = seed.strategy_parameters
            feasibility = seed.feasibility_result
            risk_adjust = 0.05 if feasibility.risk_score > 0.4 else 0.0
            density_adjust = 0.08 if (feasibility.ROI or -1.0) < 0 else 0.04
            if "high_road_length_per_unit" in feasibility.key_risk_factors:
                preferred_strategies = [strategy for strategy in base.strategies if strategy in {"grid", "spine-road", "loop_custom"}]
                preferred_strategies = preferred_strategies or base.strategies
            else:
                preferred_strategies = base.strategies
            plans.append(
                LayoutSearchPlan(
                    label=f"refine_{index}_dense",
                    strategies=preferred_strategies,
                    max_candidates=min(36, max_candidates),
                    max_layouts=3,
                    density_factor=min(1.0, base.density_factor + density_adjust),
                    lot_depth_factor=min(1.25, base.lot_depth_factor + 0.08),
                    frontage_hint_factor=max(0.85, base.frontage_hint_factor - 0.05),
                    road_width_factor=max(0.85, base.road_width_factor - 0.05),
                    runtime_budget_factor=min(1.2, base.runtime_budget_factor + 0.05),
                )
            )
            plans.append(
                LayoutSearchPlan(
                    label=f"refine_{index}_conservative",
                    strategies=preferred_strategies,
                    max_candidates=min(36, max_candidates),
                    max_layouts=3,
                    density_factor=max(0.7, base.density_factor - (0.08 + risk_adjust)),
                    lot_depth_factor=max(0.9, base.lot_depth_factor - (0.05 if feasibility.projected_profit >= 0 else 0.02)),
                    frontage_hint_factor=min(1.1, base.frontage_hint_factor + (0.05 if feasibility.projected_profit >= 0 else 0.02)),
                    road_width_factor=min(1.1, base.road_width_factor + (0.05 if feasibility.risk_score > 0.4 else 0.02)),
                    runtime_budget_factor=min(1.2, base.runtime_budget_factor + 0.05),
                )
            )
            plans.append(
                LayoutSearchPlan(
                    label=f"refine_{index}_mutated",
                    strategies=list(reversed(preferred_strategies)),
                    max_candidates=min(30, max_candidates),
                    max_layouts=2,
                    density_factor=min(1.0, max(0.7, base.density_factor + (0.06 if feasibility.projected_profit < 0 else -0.03))),
                    lot_depth_factor=min(1.3, max(0.9, base.lot_depth_factor + (0.1 if "small_area_per_unit" in feasibility.key_risk_factors else -0.03))),
                    frontage_hint_factor=max(0.85, min(1.1, base.frontage_hint_factor + (0.05 if "negative_or_unknown_base_roi" in feasibility.key_risk_factors else -0.03))),
                    road_width_factor=max(0.85, min(1.1, base.road_width_factor + (-0.04 if "high_road_length_per_unit" in feasibility.key_risk_factors else 0.03))),
                    runtime_budget_factor=min(1.25, base.runtime_budget_factor + 0.08),
                )
            )
        return plans[:6]

    @staticmethod
    def _plans_from_near_feasible_guidance(
        guidance: dict,
        base_plan: LayoutSearchPlan,
        *,
        max_candidates: int,
    ) -> list[LayoutSearchPlan]:
        reason = str(guidance.get("reason_category") or "").upper()
        if reason == "FRONTAGE_FAIL":
            return [
                LayoutSearchPlan(
                    label=f"{base_plan.label}_frontage_relief",
                    strategies=["spine-road", "loop_custom", "grid"],
                    max_candidates=min(36, max_candidates),
                    max_layouts=3,
                    density_factor=base_plan.density_factor,
                    lot_depth_factor=min(1.2, base_plan.lot_depth_factor + 0.1),
                    frontage_hint_factor=max(0.85, base_plan.frontage_hint_factor - 0.05),
                    road_width_factor=max(0.88, base_plan.road_width_factor - 0.05),
                    runtime_budget_factor=min(1.2, base_plan.runtime_budget_factor + 0.05),
                )
            ]
        if reason == "SOLVER_FAIL":
            return [
                LayoutSearchPlan(
                    label=f"{base_plan.label}_solver_retry",
                    strategies=base_plan.strategies or ["grid", "spine-road", "loop_custom", "t_junction"],
                    max_candidates=min(48, max_candidates + 12),
                    max_layouts=3,
                    density_factor=base_plan.density_factor,
                    lot_depth_factor=base_plan.lot_depth_factor,
                    frontage_hint_factor=base_plan.frontage_hint_factor,
                    road_width_factor=base_plan.road_width_factor,
                    runtime_budget_factor=min(1.25, base_plan.runtime_budget_factor + 0.1),
                )
            ]
        return []

    @staticmethod
    def _passes_objective_filters(
        feasibility: FeasibilityResult,
        objective: OptimizationObjective,
    ) -> bool:
        if objective.min_roi is not None and (feasibility.ROI is None or feasibility.ROI < objective.min_roi):
            return False
        if objective.max_risk is not None and feasibility.risk_score > objective.max_risk:
            return False
        return True

    @staticmethod
    def _normalize_roi(value: Optional[float]) -> float:
        roi = 0.0 if value is None else float(value)
        return max(-0.5, min(1.5, roi)) / 1.5

    @staticmethod
    def _normalize_profit(value: float) -> float:
        if value <= 0.0:
            return 0.0
        return min(1.0, math.log1p(value) / math.log1p(10_000_000.0))

    @classmethod
    def _objective_score(cls, feasibility: FeasibilityResult, objective: OptimizationObjective) -> float:
        score = (
            objective.roi_weight * cls._normalize_roi(feasibility.ROI)
            + objective.profit_weight * cls._normalize_profit(feasibility.projected_profit)
            - objective.risk_weight * float(feasibility.risk_score)
            + objective.confidence_weight * float(feasibility.confidence)
        )
        return round(score, 6)

    @classmethod
    def _rank_optimization_candidates(
        cls,
        candidates: list[OptimizationCandidate],
    ) -> list[OptimizationCandidate]:
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                candidate.objective_score,
                float("-inf") if candidate.feasibility_result.ROI is None else candidate.feasibility_result.ROI,
                candidate.feasibility_result.projected_profit,
                -candidate.feasibility_result.risk_score,
                candidate.feasibility_result.confidence,
                candidate.layout_result.layout_id,
            ),
            reverse=True,
        )
        return [
            candidate.model_copy(update={"optimization_rank": index})
            for index, candidate in enumerate(ranked, start=1)
        ]

    @classmethod
    def _ranking_metrics(
        cls,
        ranked_candidates: list[OptimizationCandidate],
        rounds_executed: int,
    ) -> dict[str, object]:
        profitable = [candidate for candidate in ranked_candidates if (candidate.feasibility_result.ROI or 0.0) > 0.0]
        return {
            "rounds_executed": rounds_executed,
            "candidate_count": len(ranked_candidates),
            "profitable_candidate_count": len(profitable),
            "best_objective_score": ranked_candidates[0].objective_score if ranked_candidates else None,
            "best_roi": ranked_candidates[0].feasibility_result.ROI if ranked_candidates else None,
            "best_profit": ranked_candidates[0].feasibility_result.projected_profit if ranked_candidates else None,
            "best_confidence": ranked_candidates[0].feasibility_result.confidence if ranked_candidates else None,
        }

    @staticmethod
    def _percentile(sorted_values: list[float], fraction: float) -> Optional[float]:
        if not sorted_values:
            return None
        index = int(round((len(sorted_values) - 1) * fraction))
        return sorted_values[max(0, min(index, len(sorted_values) - 1))]

    @classmethod
    def _score_distribution(cls, candidates: list[OptimizationCandidate]) -> CandidateScoreStats:
        scores = sorted(float(candidate.objective_score) for candidate in candidates)
        if not scores:
            return CandidateScoreStats(count=0)
        return CandidateScoreStats(
            count=len(scores),
            min_score=scores[0],
            p25_score=cls._percentile(scores, 0.25),
            median_score=cls._percentile(scores, 0.5),
            p75_score=cls._percentile(scores, 0.75),
            max_score=scores[-1],
        )

    @classmethod
    def _breakpoint_margin_percent(cls, current_value: Optional[float], margin_value: Optional[float]) -> Optional[float]:
        if current_value is None or margin_value is None or abs(current_value) < 1e-9:
            return None
        return round(margin_value / abs(current_value), 6)

    @classmethod
    def _candidate_breakpoints(
        cls,
        candidate: OptimizationCandidate,
        market_data: Optional[MarketData],
    ) -> list[SensitivityBreakpoint]:
        feasibility = candidate.feasibility_result
        effective_market = market_data or FeasibilityService.default_market_data()
        breakpoints: list[SensitivityBreakpoint] = []
        current_land_price = float(effective_market.land_price or 0.0)
        land_break_even = current_land_price + float(feasibility.projected_profit)
        breakpoints.append(
            SensitivityBreakpoint(
                variable="land_price",
                current_value=current_land_price,
                break_even_value=round(land_break_even, 2),
                margin_value=round(float(feasibility.projected_profit), 2),
                margin_percent=cls._breakpoint_margin_percent(current_land_price, float(feasibility.projected_profit)),
                explanation=(
                    "This deal breaks when acquisition cost absorbs projected profit."
                    if feasibility.projected_profit >= 0
                    else "This deal becomes viable if acquisition cost falls by the profit deficit."
                ),
            )
        )
        units = max(int(feasibility.units), 1)
        construction_delta = float(feasibility.projected_profit) / units
        breakpoints.append(
            SensitivityBreakpoint(
                variable="construction_cost_per_home",
                current_value=float(effective_market.construction_cost_per_home),
                break_even_value=round(float(effective_market.construction_cost_per_home) + construction_delta, 2),
                margin_value=round(construction_delta, 2),
                margin_percent=cls._breakpoint_margin_percent(float(effective_market.construction_cost_per_home), construction_delta),
                explanation="Per-home construction cost can move by this amount before ROI crosses zero.",
            )
        )
        home_size = max(float(feasibility.estimated_home_size_sqft or 1.0), 1.0)
        price_delta_psf = float(feasibility.projected_profit) / max(units * home_size, 1.0)
        breakpoints.append(
            SensitivityBreakpoint(
                variable="price_per_sqft",
                current_value=float(feasibility.price_per_sqft),
                break_even_value=round(float(feasibility.price_per_sqft) - price_delta_psf, 4),
                margin_value=round(price_delta_psf, 4),
                margin_percent=cls._breakpoint_margin_percent(float(feasibility.price_per_sqft), price_delta_psf),
                explanation="Sale price per square foot must stay above this threshold for feasibility.",
            )
        )
        unit_margin = float(feasibility.revenue_per_unit) - float(feasibility.cost_per_unit)
        if unit_margin == 0.0:
            density_break_even = None
            density_margin = None
        elif feasibility.projected_profit >= 0:
            density_break_even = max(1.0, float(feasibility.units) - (float(feasibility.projected_profit) / unit_margin))
            density_margin = density_break_even - float(feasibility.units)
        else:
            density_break_even = float(feasibility.units) + abs(float(feasibility.projected_profit) / unit_margin)
            density_margin = density_break_even - float(feasibility.units)
        breakpoints.append(
            SensitivityBreakpoint(
                variable="density_units",
                current_value=float(feasibility.units),
                break_even_value=None if density_break_even is None else round(density_break_even, 2),
                margin_value=None if density_margin is None else round(density_margin, 2),
                margin_percent=cls._breakpoint_margin_percent(float(feasibility.units), density_margin),
                explanation="Additional or fewer units required before projected profit crosses zero.",
            )
        )
        return breakpoints

    @classmethod
    def _build_sensitivity_analysis(
        cls,
        *,
        ranked_candidates: list[OptimizationCandidate],
        market_data: Optional[MarketData],
    ) -> list[CandidateSensitivity]:
        if not ranked_candidates:
            return []
        analyses: list[CandidateSensitivity] = []
        best = ranked_candidates[0]
        focus: list[tuple[str, OptimizationCandidate]] = [("best_candidate", best)]
        failing = next((candidate for candidate in ranked_candidates if (candidate.feasibility_result.ROI or 0.0) < 0.0), None)
        if failing is not None and failing.layout_result.layout_id != best.layout_result.layout_id:
            focus.append(("failing_candidate", failing))
        for status, candidate in focus:
            feasibility = candidate.feasibility_result
            breakpoints = cls._candidate_breakpoints(candidate, market_data)
            if feasibility.projected_profit >= 0:
                primary_reason = "Deal is currently feasible but constrained by market and density breakpoints."
                make_it_work = "Maintain pricing discipline and protect density yield to preserve current profitability."
            else:
                if breakpoints:
                    tightest = min(
                        [bp for bp in breakpoints if bp.margin_value is not None],
                        key=lambda bp: abs(float(bp.margin_value)),
                        default=breakpoints[0],
                    )
                    primary_reason = f"This deal fails because {tightest.variable} is outside break-even bounds."
                    if tightest.margin_percent is not None:
                        make_it_work = (
                            f"It becomes viable if {tightest.variable} changes by "
                            f"{round(abs(float(tightest.margin_percent)) * 100.0, 2)}%."
                        )
                    else:
                        make_it_work = (
                            f"It becomes viable if {tightest.variable} moves to "
                            f"{tightest.break_even_value}."
                        )
                else:
                    primary_reason = "This deal fails because projected profit is negative."
                    make_it_work = "It becomes viable when economics cross the break-even threshold."
            analyses.append(
                CandidateSensitivity(
                    layout_id=candidate.layout_result.layout_id,
                    status=status,
                    primary_failure_reason=primary_reason,
                    margin_to_feasibility=round(float(feasibility.projected_profit), 2),
                    make_it_work_statement=make_it_work,
                    breakpoints=breakpoints,
                )
            )
        return analyses

    def _build_economic_scenarios(
        self,
        *,
        parcel: Parcel,
        zoning_rules: ZoningRules,
        ranked_candidates: list[OptimizationCandidate],
        market_data: Optional[MarketData],
        max_candidates: int,
    ) -> list[EconomicScenario]:
        if not ranked_candidates:
            return []
        best = ranked_candidates[0]
        feasibility = best.feasibility_result
        effective_market = market_data or FeasibilityService.default_market_data()
        land_price_now = float(effective_market.land_price or 0.0)
        max_offer = round(land_price_now + float(feasibility.projected_profit), 2)
        sweep_points: list[dict[str, float | int | str | None]] = []
        for share in (0.0, 0.5, 1.0, 1.25):
            offer = land_price_now + (float(feasibility.projected_profit) * share)
            profit = float(feasibility.projected_profit) - (offer - land_price_now)
            roi = None if feasibility.projected_cost == 0 else profit / float(feasibility.projected_cost)
            sweep_points.append(
                {
                    "offer_price": round(offer, 2),
                    "projected_profit": round(profit, 2),
                    "ROI": None if roi is None else round(roi, 6),
                }
            )
        density_curve = [
            {
                "layout_id": candidate.layout_result.layout_id,
                "units": candidate.feasibility_result.units,
                "ROI": candidate.feasibility_result.ROI,
                "projected_profit": candidate.feasibility_result.projected_profit,
            }
            for candidate in sorted(
                ranked_candidates,
                key=lambda item: (item.feasibility_result.units, item.objective_score),
            )
        ]
        rezoning_scenario = self._simulate_rezoning_scenario(
            parcel=parcel,
            zoning_rules=zoning_rules,
            baseline_candidate=best,
            market_data=market_data,
            max_candidates=max_candidates,
        )
        return [
            EconomicScenario(
                scenario_name="land_price_sweep",
                scenario_type="land_price_sweep",
                status="evaluated",
                best_layout_id=best.layout_result.layout_id,
                best_roi=feasibility.ROI,
                best_projected_profit=feasibility.projected_profit,
                recommended_max_offer_price=max_offer,
                curve=sweep_points,
                explanation="Recommended max offer equals current land price plus projected profit at break-even.",
            ),
            EconomicScenario(
                scenario_name="density_curve",
                scenario_type="density_curve",
                status="evaluated",
                best_layout_id=best.layout_result.layout_id,
                best_roi=feasibility.ROI,
                best_projected_profit=feasibility.projected_profit,
                curve=density_curve,
                explanation="Density curve compares ranked candidate economics across achieved unit counts.",
            ),
            rezoning_scenario,
        ]

    def _simulate_rezoning_scenario(
        self,
        *,
        parcel: Parcel,
        zoning_rules: ZoningRules,
        baseline_candidate: OptimizationCandidate,
        market_data: Optional[MarketData],
        max_candidates: int,
    ) -> EconomicScenario:
        rezoned_max_units_per_acre = float(zoning_rules.max_units_per_acre or 0.0) * 1.15
        if rezoned_max_units_per_acre <= float(zoning_rules.max_units_per_acre or 0.0):
            return EconomicScenario(
                scenario_name="rezoning_uplift",
                scenario_type="rezoning",
                status="not_applicable",
                explanation="No density headroom available for rezoning simulation.",
            )
        rezoned_rules = zoning_rules.model_copy(update={"max_units_per_acre": rezoned_max_units_per_acre})
        try:
            batch = search_layout_candidates_debug(
                parcel,
                rezoned_rules,
                search_plan=LayoutSearchPlan(
                    label="rezoning_uplift",
                    strategies=baseline_candidate.strategy_parameters.strategies,
                    max_candidates=min(24, max_candidates),
                    max_layouts=2,
                    density_factor=1.0,
                    lot_depth_factor=baseline_candidate.strategy_parameters.lot_depth_factor,
                    frontage_hint_factor=baseline_candidate.strategy_parameters.frontage_hint_factor,
                    road_width_factor=baseline_candidate.strategy_parameters.road_width_factor,
                    runtime_budget_factor=1.1,
                ),
            )
        except LayoutSearchError:
            return EconomicScenario(
                scenario_name="rezoning_uplift",
                scenario_type="rezoning",
                status="not_applicable",
                explanation="Rezoning scenario did not produce valid layouts.",
            )
        if not batch.layouts:
            return EconomicScenario(
                scenario_name="rezoning_uplift",
                scenario_type="rezoning",
                status="not_applicable",
                explanation="Rezoning scenario did not produce valid layouts.",
            )
        rezoning_zoning_metadata = None
        if getattr(zoning_rules, "metadata", None) is not None:
            rezoning_zoning_metadata = {
                "source_type": zoning_rules.metadata.source_type,
                "legal_reliability": zoning_rules.metadata.legal_reliability,
            }
        evaluated = self.feasibility_service.evaluate_layouts(parcel, batch.layouts, market_data=market_data, zoning_metadata=rezoning_zoning_metadata)
        best = evaluated[0]
        baseline = baseline_candidate.feasibility_result
        return EconomicScenario(
            scenario_name="rezoning_uplift",
            scenario_type="rezoning",
            status="evaluated",
            best_layout_id=best.layout_id,
            best_roi=best.ROI,
            best_projected_profit=best.projected_profit,
            delta_roi=None if best.ROI is None or baseline.ROI is None else round(best.ROI - baseline.ROI, 6),
            delta_profit=round(best.projected_profit - baseline.projected_profit, 2),
            curve=[
                {
                    "baseline_units": baseline.units,
                    "rezoned_units": best.units,
                    "baseline_profit": baseline.projected_profit,
                    "rezoned_profit": best.projected_profit,
                }
            ],
            explanation="Rezoning scenario simulates a 15% increase in max_units_per_acre and reruns candidate search.",
        )

    def _persist_best_optimization_candidate(
        self,
        parcel: Parcel,
        zoning_rules: ZoningRules,
        candidate: OptimizationCandidate,
        *,
        timestamp: datetime,
        input_hash: str,
    ) -> str:
        run_id = str(uuid4())
        run_record = PipelineRunRecord(
            run_id=run_id,
            timestamp=timestamp,
            status="completed",
            parcel=parcel,
            zoning=zoning_rules,
            layout=candidate.layout_result,
            feasibility=candidate.feasibility_result,
            near_feasible_result=None,
            git_commit=self._resolve_git_commit(),
            input_hash=input_hash,
            stage_runtimes={},
            zoning_bypassed=False,
            bypass_reason=None,
        )
        self.run_store.save_run(run_id, run_record)
        return run_id

    @staticmethod
    def _build_optimization_decision(
        *,
        best_candidate: Optional[OptimizationCandidate],
        zoning_rules: ZoningRules,
        ranking_metrics: dict[str, object],
        sensitivity_analysis: list[CandidateSensitivity],
        economic_scenarios: list[EconomicScenario],
    ) -> Optional[OptimizationDecision]:
        if best_candidate is None:
            return OptimizationDecision(
                recommendation="abandon",
                action="abandon",
                sensitivity=["no_viable_candidate_found"],
                key_risks=["no_candidate"],
                rationale="Optimization did not produce any valid financially ranked candidates.",
                reason="No valid candidate layouts survived optimization.",
            )
        feasibility = best_candidate.feasibility_result
        roi = feasibility.ROI or 0.0
        worst_case = feasibility.ROI_worst_case
        sensitivity = list(feasibility.key_risk_factors)
        breakpoint_set = sensitivity_analysis[0].breakpoints if sensitivity_analysis else []
        if worst_case is not None and worst_case < 0:
            sensitivity.append("downside_case_breaks_profitability")
        if feasibility.break_even_price is not None:
            sensitivity.append(f"break_even_price={round(feasibility.break_even_price, 2)}")
        land_scenario = next((scenario for scenario in economic_scenarios if scenario.scenario_type == "land_price_sweep"), None)
        rezoning_scenario = next((scenario for scenario in economic_scenarios if scenario.scenario_type == "rezoning"), None)
        if roi >= 0.15 and feasibility.confidence >= 0.65 and feasibility.risk_score <= 0.45:
            recommendation = "acquire"
            reason = "Base case ROI, downside range, and confidence clear the acquisition threshold."
            alternative = None
        elif roi >= 0.0:
            recommendation = "renegotiate_price"
            reason = "Deal is positive but margin is thin; renegotiating land basis preserves feasibility."
            alternative = (
                f"increase density to {rezoning_scenario.curve[0]['rezoned_units']} units for more upside"
                if rezoning_scenario is not None and rezoning_scenario.status == "evaluated" and rezoning_scenario.curve
                else None
            )
        elif best_candidate.strategy_parameters.density_factor >= 0.95:
            recommendation = "pursue_rezoning"
            reason = "Current zoning-constrained density leaves the deal negative while higher density improves economics."
            alternative = (
                f"max offer price should not exceed {land_scenario.recommended_max_offer_price}"
                if land_scenario is not None and land_scenario.recommended_max_offer_price is not None
                else None
            )
        else:
            recommendation = "abandon"
            reason = "Even the best candidate remains negative after bounded optimization."
            alternative = (
                sensitivity_analysis[1].make_it_work_statement
                if len(sensitivity_analysis) > 1
                else None
            )
        return OptimizationDecision(
            recommendation=recommendation,
            action=recommendation,
            best_layout_id=best_candidate.layout_result.layout_id,
            expected_roi_base=feasibility.ROI_base if feasibility.ROI_base is not None else feasibility.ROI,
            expected_roi_best_case=feasibility.ROI_best_case,
            expected_roi_worst_case=feasibility.ROI_worst_case,
            sensitivity=sensitivity,
            key_risks=list(feasibility.key_risk_factors),
            breakpoints=breakpoint_set,
            target_price=land_scenario.recommended_max_offer_price if land_scenario is not None else None,
            reason=reason,
            alternative=alternative,
            rationale=(
                f"Selected layout {best_candidate.layout_result.layout_id} with ROI "
                f"{round(roi, 4)} and projected profit {round(feasibility.projected_profit, 2)} "
                f"from {ranking_metrics.get('candidate_count', 0)} ranked candidates."
            ),
        )

    @staticmethod
    def _build_input_hash(
        *,
        parcel: Optional[Parcel],
        parcel_geometry: Optional[dict],
        max_candidates: int,
        parcel_id: Optional[str],
        jurisdiction: Optional[str],
        market_data: Optional[MarketData],
    ) -> str:
        payload = {
            "parcel": parcel.model_dump(mode="json") if parcel is not None else None,
            "parcel_geometry": parcel_geometry,
            "max_candidates": max_candidates,
            "parcel_id": parcel_id,
            "jurisdiction": jurisdiction,
            "market_data": market_data.model_dump(mode="json") if market_data is not None else None,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _resolve_git_commit() -> Optional[str]:
        try:
            workspace_root = Path(__file__).resolve().parents[2]
            return (
                subprocess.check_output(
                    ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
                or None
            )
        except Exception:
            return None

    def _load_parcel_stage(
        self,
        *,
        parcel: Optional[Parcel],
        parcel_geometry: Optional[dict],
        parcel_id: Optional[str],
        jurisdiction: Optional[str],
    ) -> Parcel:
        try:
            if parcel is not None:
                return validate_parcel_output(self.parcel_service.normalize_parcel_contract(parcel))
            if parcel_geometry is None:
                raise ValueError("pipeline input must include either 'parcel' or 'parcel_geometry'")
            if not (jurisdiction or "").strip():
                raise ValueError("pipeline parcel_geometry input requires jurisdiction")
            loaded = self.parcel_service.load_parcel(
                geometry=parcel_geometry,
                parcel_id=parcel_id,
                jurisdiction=jurisdiction,
            )
            return validate_parcel_output(loaded)
        except ValueError as exc:
            raise PipelineStageError(
                stage="parcel.load",
                error="invalid_parcel_input",
                message=str(exc),
                status_code=400,
            ) from exc

    def _lookup_zoning_stage(self, parcel: Parcel) -> ZoningStageResult:
        try:
            bypassed = self._lookup_non_buildable_zoning_stage(parcel)
            if bypassed is not None:
                return bypassed
            zoning = self.zoning_service.lookup(parcel)
            zoning_rules = validate_zoning_rules_for_layout(zoning.rules)
            try:
                self._validate_real_overlay_zoning_source(parcel, zoning_rules)
            except PipelineStageError as zoning_exc:
                bypass_reason = _classify_zoning_bypass(zoning_rules)
                logger.warning(
                    "zoning_bypass_classified",
                    extra={
                        "parcel_id": parcel.parcel_id,
                        "jurisdiction": parcel.jurisdiction,
                        "district": zoning_rules.district,
                        "bypass_reason": bypass_reason,
                        "source_type": str(getattr(zoning_rules.metadata, "source_type", None)),
                        "legal_reliability": str(getattr(zoning_rules.metadata, "legal_reliability", None)),
                        "source_run_id": str(getattr(zoning_rules.metadata, "source_run_id", None)),
                    },
                )
                return ZoningStageResult(
                    rules=zoning_rules,
                    status="exploratory_zoning",
                    bypass_reason=bypass_reason,
                )
            return ZoningStageResult(rules=zoning_rules)
        except NoJurisdictionMatchError as exc:
            hinted = self._lookup_non_buildable_from_parcel_hint(parcel)
            if hinted is not None:
                return hinted
            return self._build_unsupported_zoning_stage(parcel, reason="NO_JURISDICTION_MATCH")
        except NoZoningMatchError as exc:
            hinted = self._lookup_non_buildable_from_parcel_hint(parcel)
            if hinted is not None:
                return hinted
            return self._build_unsupported_zoning_stage(parcel, reason="NO_INTERSECTION")
        except (AmbiguousJurisdictionMatchError, AmbiguousZoningMatchError) as exc:
            raise PipelineStageError(
                stage="zoning.lookup",
                error="ambiguous_district_match",
                message=str(exc),
                status_code=409,
            ) from exc
        except IncompleteZoningRulesError as exc:
            hinted = self._lookup_non_buildable_from_parcel_hint(parcel)
            if hinted is not None:
                return hinted
            raise PipelineStageError(
                stage="zoning.lookup",
                error="incomplete_zoning_rules",
                message="Zoning rules are incomplete for layout execution",
                status_code=422,
                details={"district": exc.district, "missing_fields": exc.missing_fields},
            ) from exc
        except InvalidZoningRulesError as exc:
            hinted = self._lookup_non_buildable_from_parcel_hint(parcel)
            if hinted is not None:
                return hinted
            raise PipelineStageError(
                stage="zoning.lookup",
                error="invalid_zoning_rules",
                message="Zoning rules are invalid for layout execution",
                status_code=422,
                details={"district": exc.district, "violations": exc.violations},
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "ZoningRules is incomplete for layout compatibility:" in message:
                hinted = self._lookup_non_buildable_from_parcel_hint(parcel)
                if hinted is not None:
                    return hinted
                trailing = message.split(":", 1)[1] if ":" in message else ""
                missing_fields = [field.strip() for field in trailing.split(",") if field.strip()]
                raise PipelineStageError(
                    stage="zoning.lookup",
                    error="incomplete_zoning_rules",
                    message="Zoning rules are incomplete for layout execution",
                    status_code=422,
                    details={"district": parcel.jurisdiction or "unknown", "missing_fields": missing_fields},
                ) from exc
            raise PipelineStageError(
                stage="zoning.lookup",
                error="invalid_zoning_rules",
                message="Zoning rules are invalid for layout execution",
                status_code=422,
                details={"district": parcel.jurisdiction or "unknown", "violations": [message]},
            ) from exc

    def _lookup_non_buildable_from_parcel_hint(self, parcel: Parcel) -> Optional[ZoningStageResult]:
        district = (parcel.zoning_district or "").strip()
        if not district:
            return None

        bypass_reason = self._match_non_buildable_district(
            jurisdiction=parcel.jurisdiction,
            district=district,
        )
        if bypass_reason is None:
            return None

        zoning_rules = ZoningRules(
            parcel_id=parcel.parcel_id,
            jurisdiction=parcel.jurisdiction,
            district=district,
            metadata=EngineMetadata(
                source_engine="bedrock.pipeline_service",
                source_run_id="parcel_non_buildable_hint",
            ),
        )
        logger.info(
            "pipeline_non_buildable_parcel_hint_bypassed",
            extra={
                "parcel_id": parcel.parcel_id,
                "jurisdiction": parcel.jurisdiction,
                "district": district,
                "bypass_reason": bypass_reason,
            },
        )
        return ZoningStageResult(rules=zoning_rules, status="non_buildable", bypass_reason=bypass_reason)

    def _lookup_non_buildable_zoning_stage(self, parcel: Parcel) -> Optional[ZoningStageResult]:
        if not NON_BUILDABLE_DISTRICTS_ENABLED:
            return None

        parcel_geometry = shape(parcel.geometry)
        try:
            raw_rules = self.zoning_service._resolve_raw_rules(parcel, parcel_geometry)
        except IncompleteZoningRulesError:
            raw_rules = self.zoning_service._build_jurisdiction_fallback_raw(parcel)
        except (NoJurisdictionMatchError, NoZoningMatchError):
            return None
        normalized_raw = self.zoning_service._normalize_raw_input(parcel_geometry, raw_rules)
        bypass_reason = self._match_non_buildable_district(
            jurisdiction=normalized_raw.get("jurisdiction"),
            district=normalized_raw.get("district"),
        )
        if bypass_reason is None:
            return None

        zoning_rules = normalize_zoning_rules(
            normalized_raw,
            parcel=parcel,
            jurisdiction=normalized_raw["jurisdiction"],
            district=normalized_raw["district"],
        )
        try:
            self._validate_real_overlay_zoning_source(parcel, zoning_rules)
        except PipelineStageError:
            pass
        logger.info(
            "pipeline_non_buildable_zoning_bypassed",
            extra={
                "parcel_id": parcel.parcel_id,
                "jurisdiction": zoning_rules.jurisdiction,
                "district": zoning_rules.district,
                "zoning_source": zoning_rules.metadata.source_run_id if zoning_rules.metadata else "unknown",
                "bypass_reason": bypass_reason,
            },
        )
        return ZoningStageResult(rules=zoning_rules, status="non_buildable", bypass_reason=bypass_reason)

    def _build_unsupported_zoning_stage(self, parcel: Parcel, *, reason: str) -> ZoningStageResult:
        district = (parcel.zoning_district or "").strip() or "UNSUPPORTED"
        zoning_rules = ZoningRules(
            parcel_id=parcel.parcel_id,
            jurisdiction=parcel.jurisdiction,
            district=district,
            metadata=EngineMetadata(
                source_engine="bedrock.pipeline_service",
                source_run_id="unsupported_jurisdiction",
            ),
        )
        logger.info(
            "pipeline_unsupported_zoning_bypassed",
            extra={
                "parcel_id": parcel.parcel_id,
                "jurisdiction": parcel.jurisdiction,
                "district": district,
                "bypass_reason": reason,
            },
        )
        return ZoningStageResult(rules=zoning_rules, status="unsupported", bypass_reason=reason)

    @staticmethod
    def _match_non_buildable_district(*, jurisdiction: Optional[str], district: Optional[str]) -> Optional[str]:
        normalized_jurisdiction = (jurisdiction or "").strip().lower()
        normalized_district = (district or "").strip().upper()
        for candidate in NON_BUILDABLE_DISTRICTS:
            if normalized_jurisdiction == candidate["jurisdiction"] and normalized_district == candidate["district"].upper():
                return str(candidate["reason"])
        return None

    @staticmethod
    def _validate_real_overlay_zoning_source(parcel: Parcel, zoning_rules: ZoningRules) -> None:
        source = str(zoning_rules.metadata.source_run_id if zoning_rules.metadata is not None else "unknown")
        source_type = str(zoning_rules.metadata.source_type if zoning_rules.metadata is not None and zoning_rules.metadata.source_type else "unknown")
        legal_reliability = bool(
            zoning_rules.metadata.legal_reliability
            if zoning_rules.metadata is not None and zoning_rules.metadata.legal_reliability is not None
            else False
        )
        invalid_sources = {
            "jurisdiction_fallback",
            "safe_minimum_viable",
            "precomputed_district_index",
            "unknown",
        }
        invalid_source_type = source_type not in {"real_lookup"}
        invalid_legal_reliability = not legal_reliability
        if source in invalid_sources or "precomputed_district_index" in source or invalid_source_type or invalid_legal_reliability:
            message = (
                "Pipeline zoning is exploratory only until parcel zoning uses a real_lookup source"
                if source_type == "inferred"
                else "Pipeline zoning must use real overlay-backed district resolution with legally reliable development standards"
            )
            logger.error(
                "pipeline_zoning_source_rejected",
                extra={
                    "parcel_id": parcel.parcel_id,
                    "jurisdiction": parcel.jurisdiction,
                    "district": zoning_rules.district,
                    "zoning_source": source,
                    "source_type": source_type,
                    "legal_reliability": legal_reliability,
                },
            )
            raise PipelineStageError(
                stage="zoning.lookup",
                error="invalid_zoning_source",
                message=message,
                status_code=422,
                details={
                    "district": zoning_rules.district,
                    "zoning_source": source,
                    "source_type": source_type,
                    "legal_reliability": legal_reliability,
                    "geometry_match_success": False,
                },
            )

        logger.info(
            "pipeline_zoning_source_validated",
            extra={
                "parcel_id": parcel.parcel_id,
                "jurisdiction": parcel.jurisdiction,
                "district": zoning_rules.district,
                "zoning_source": source,
                "source_type": source_type,
                "legal_reliability": legal_reliability,
                "geometry_match_success": True,
            },
        )

    def _search_layout_stage(self, parcel: Parcel, zoning_rules, *, max_candidates: int):
        try:
            layout = search_subdivision_layout(parcel, zoning_rules, max_candidates=max_candidates)
            return validate_layout_result_output(layout)
        except LayoutSearchError as exc:
            raise PipelineStageError(
                stage="layout.search",
                error=exc.code,
                message=exc.message,
                status_code=422,
                details=dict(exc.details),
            ) from exc
        except RuntimeError as exc:
            raise PipelineStageError(
                stage="layout.search",
                error="layout_solver_failure",
                message=str(exc),
                status_code=500,
            ) from exc

    @staticmethod
    def _build_near_feasible_result(
        parcel: Parcel,
        zoning_rules: ZoningRules,
        exc: PipelineStageError,
    ) -> Optional[NearFeasibleResult]:
        if exc.stage != "layout.search":
            return None
        details = dict(exc.details or {})
        attempted_profiles = details.get("attempted_profiles") or []
        attempted_repairs = details.get("attempted_repairs") or []
        layout_error = LayoutSearchError(exc.error, exc.message, details=details)
        payload = _near_feasible_result(
            parcel,
            zoning_rules,
            layout_error,
            attempted_profiles=list(attempted_profiles),
            attempted_repairs=list(attempted_repairs),
        )
        if payload is None:
            return None
        payload["financial_upside"] = evaluate_near_feasible_upside(
            parcel=parcel,
            near_feasible_result=payload,
        )
        return NearFeasibleResult.model_validate(payload)

    @staticmethod
    def _build_bypassed_near_feasible_result(
        parcel: Parcel,
        zoning_rules: ZoningRules,
        *,
        status: str,
        bypass_reason: Optional[str],
    ) -> NearFeasibleResult:
        limiting_constraints = {
            "district": zoning_rules.district,
            "jurisdiction": zoning_rules.jurisdiction,
            "bypass_reason": bypass_reason,
            "status": status,
        }
        required_relaxation = {
            "zoning_resolution": {
                "required": True,
                "reason": bypass_reason,
            }
        }
        return NearFeasibleResult(
            reason_category="ZONING_CONSTRAINT_FAIL",
            limiting_constraints=limiting_constraints,
            required_relaxation=required_relaxation,
            best_attempt_summary={"status": status, "bypass_reason": bypass_reason},
            financial_upside=evaluate_near_feasible_upside(
                parcel=parcel,
                near_feasible_result={
                    "limiting_constraints": {"max_units": 1, **limiting_constraints},
                    "best_attempt_summary": {"lot_count": 1, "total_road_ft": 0.0},
                },
            ),
            attempted_strategies=[],
            attempted_repairs=[],
        )

    def _evaluate_feasibility_stage(
        self,
        parcel: Parcel,
        zoning_rules,
        layout_result,
        *,
        market_data: Optional[MarketData],
    ) -> FeasibilityResult:
        try:
            zoning_metadata = None
            if getattr(zoning_rules, "metadata", None) is not None:
                zoning_metadata = {
                    "source_type": zoning_rules.metadata.source_type,
                    "legal_reliability": zoning_rules.metadata.legal_reliability,
                }
            feasibility = self.feasibility_service.evaluate(
                parcel=parcel,
                layout=layout_result,
                market_data=market_data,
                zoning_metadata=zoning_metadata,
            )
            feasibility = validate_feasibility_result_output(feasibility)
            validate_feasibility_pipeline_contracts(
                parcel=parcel,
                zoning_rules=zoning_rules,
                layout_result=layout_result,
                feasibility_result=feasibility,
            )
            return feasibility
        except ValueError as exc:
            raise PipelineStageError(
                stage="feasibility.evaluate",
                error="invalid_feasibility_output",
                message=str(exc),
                status_code=500,
            ) from exc
        except RuntimeError as exc:
            raise PipelineStageError(
                stage="feasibility.evaluate",
                error="feasibility_evaluation_failure",
                message=str(exc),
                status_code=500,
            ) from exc
