"""Pipeline for parcel feasibility evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel

try:  # Prefer canonical package paths when imported as `bedrock.pipelines.*`.
    from bedrock.contracts.feasibility import FeasibilityResult, FeasibilityScenario
    from bedrock.contracts.layout import SubdivisionLayout
    from bedrock.contracts.market_data import MarketData
    from bedrock.contracts.parcel import Parcel
    from bedrock.contracts.scenario_evaluation import ScenarioEvaluation
    from bedrock.contracts.zoning import DevelopmentStandard, ZoningDistrict
    from bedrock.engines import parcel_engine, zoning_engine
    from bedrock.orchestration.pipeline_runner import PipelineRunner
    from bedrock.services.feasibility_service import FeasibilityService, evaluate_scenario
except ImportError:  # Compatibility mode for PYTHONPATH rooted at `bedrock/`.
    from contracts.feasibility import FeasibilityResult, FeasibilityScenario
    from contracts.layout import SubdivisionLayout
    from contracts.market_data import MarketData
    from contracts.parcel import Parcel
    from contracts.scenario_evaluation import ScenarioEvaluation
    from contracts.zoning import DevelopmentStandard, ZoningDistrict
    from engines import parcel_engine, zoning_engine
    from orchestration.pipeline_runner import PipelineRunner
    from services.feasibility_service import FeasibilityService, evaluate_scenario


@dataclass
class ParcelFeasibilityArtifacts:
    parcel: Parcel
    zoning: ZoningDistrict
    standards: List[DevelopmentStandard]
    scenario: FeasibilityScenario
    layout: SubdivisionLayout
    result: FeasibilityResult
    scenario_evaluation: Optional[ScenarioEvaluation] = None


class ParcelFeasibilityPipeline:
    """Coarse orchestration pipeline for parcel feasibility analysis."""

    name = "parcel_feasibility"
    required_engines = (parcel_engine, zoning_engine)
    feasibility_service = FeasibilityService()

    def run(
        self,
        parcel_id: str,
        runner: Optional["PipelineRunner"] = None,
        run_id: Optional[str] = None,
    ) -> ParcelFeasibilityArtifacts:
        parcel = self._run_stage(
            "parcel_lookup",
            "GIS_lot_layout_optimizer.get_parcel",
            lambda: parcel_engine.get_parcel(parcel_id),
            runner=runner,
            run_id=run_id,
        )
        zoning = self._run_stage(
            "zoning_retrieval",
            "zoning_data_scraper.get_zoning",
            lambda: zoning_engine.get_zoning(parcel),
            runner=runner,
            run_id=run_id,
        )
        standards = self._run_stage(
            "development_standards_resolution",
            "zoning_data_scraper.get_development_standards",
            lambda: zoning_engine.get_development_standards(parcel, zoning),
            runner=runner,
            run_id=run_id,
        )
        self._validate_development_standards(standards)
        scenario = self.create_feasibility_scenario(parcel, standards)
        layout = self._run_stage(
            "subdivision_layout_generation",
            "GIS_lot_layout_optimizer.generate_layout",
            lambda: parcel_engine.generate_layout(parcel, zoning, standards),
            runner=runner,
            run_id=run_id,
        )
        result = self._run_stage(
            "feasibility_scoring",
            "bedrock.services.feasibility_service.evaluate",
            lambda: self.score_layout(parcel, layout, standards, scenario),
            runner=runner,
            run_id=run_id,
        )
        scenario_evaluation = self._run_stage(
            "feasibility_ranking",
            "bedrock.services.feasibility_service.evaluate_scenario",
            lambda: evaluate_scenario(
                parcel,
                [layout],
                self.default_market_data(),
            ),
            runner=runner,
            run_id=run_id,
        )
        return ParcelFeasibilityArtifacts(
            parcel=parcel,
            zoning=zoning,
            standards=standards,
            scenario=scenario,
            layout=layout,
            result=result,
            scenario_evaluation=scenario_evaluation,
        )

    @staticmethod
    def _run_stage(stage: str, engine_called: str, fn, runner: Optional["PipelineRunner"], run_id: Optional[str]):
        started = time.perf_counter()
        try:
            result = fn()
            validation_result = "passed" if ParcelFeasibilityPipeline._is_valid_contract_output(result) else "unknown"
            stub_used = ParcelFeasibilityPipeline._uses_stub(result)
            if runner is not None and run_id is not None:
                runner.log_engine_interaction(
                    run_id,
                    stage,
                    engine_called,
                    time.perf_counter() - started,
                    status="success",
                    validation_result=validation_result,
                    stub_used=stub_used,
                )
            return result
        except Exception as exc:
            if runner is not None and run_id is not None:
                runner.log_engine_interaction(
                    run_id,
                    stage,
                    engine_called,
                    time.perf_counter() - started,
                    status="failure",
                    validation_result="failed",
                    stub_used=False,
                    error=str(exc),
                )
            raise

    @staticmethod
    def _is_valid_contract_output(result: object) -> bool:
        if isinstance(result, BaseModel):
            return True
        if isinstance(result, list):
            return all(isinstance(item, BaseModel) for item in result)
        return False

    @staticmethod
    def _uses_stub(result: object) -> bool:
        if isinstance(result, BaseModel):
            metadata = getattr(result, "metadata", None)
            return bool(metadata and metadata.source_run_id == "stub")
        if isinstance(result, list):
            return any(ParcelFeasibilityPipeline._uses_stub(item) for item in result)
        return False

    @staticmethod
    def _validate_development_standards(standards: List[DevelopmentStandard]) -> None:
        if not all(isinstance(item, DevelopmentStandard) for item in standards):
            raise TypeError("development_standards_resolution must return DevelopmentStandard contracts")

    @staticmethod
    def create_feasibility_scenario(
        parcel: Parcel, standards: List[DevelopmentStandard]
    ) -> FeasibilityScenario:
        density_limit = next(
            (
                int(float(item.value))
                for item in standards
                if item.standard_type.lower() in {"density", "max_units"}
            ),
            None,
        )
        requested_units = density_limit or max(int(parcel.area // 5000), 1)
        return FeasibilityScenario(
            scenario_id=str(uuid4()),
            parcel_id=parcel.parcel_id,
            requested_units=requested_units,
            assumptions={
                "scenario_source": "bedrock.pipeline.parcel_feasibility",
                "density_limit_detected": density_limit is not None,
            },
            constraints=[item.model_dump() for item in standards],
        )

    @staticmethod
    def score_layout(
        parcel: Parcel,
        layout: SubdivisionLayout,
        standards: List[DevelopmentStandard],
        scenario: FeasibilityScenario,
    ) -> FeasibilityResult:
        violations = []
        if layout.lot_count < scenario.requested_units:
            violations.append("requested_units_exceed_layout_capacity")

        if not standards:
            violations.append("missing_development_standards")

        evaluated = ParcelFeasibilityPipeline.feasibility_service.evaluate(
            parcel=parcel,
            layout=layout,
            market_data=ParcelFeasibilityPipeline.default_market_data(),
        )
        if not violations:
            return evaluated
        merged_violations = sorted(set(evaluated.constraint_violations).union(violations))
        return evaluated.model_copy(
            update={
                "constraint_violations": merged_violations,
                "status": "constrained",
                "confidence": min(0.85 if standards else 0.45, evaluated.confidence),
            }
        )

    @staticmethod
    def default_market_data() -> MarketData:
        return FeasibilityService.default_market_data()
