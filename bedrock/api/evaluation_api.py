"""FastAPI router for stored PipelineRun benchmarking."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, FastAPI

from bedrock.contracts.base import BedrockModel
from bedrock.services.pipeline_run_evaluation_service import PipelineRunEvaluationService


router = APIRouter(prefix="/evaluation", tags=["evaluation"])
service = PipelineRunEvaluationService()


class RunFilter(BedrockModel):
    min_ROI: Optional[float] = None
    min_units: Optional[int] = None


class RunSelector(BedrockModel):
    run_ids: Optional[list[str]] = None
    filters: RunFilter = RunFilter()


class BenchmarkMetrics(BedrockModel):
    run_count: int
    avg_ROI: Optional[float] = None
    avg_projected_profit: Optional[float] = None
    avg_units: Optional[float] = None
    min_ROI: Optional[float] = None
    max_ROI: Optional[float] = None


class BenchmarkDelta(BedrockModel):
    run_count: int
    avg_ROI: Optional[float] = None
    avg_projected_profit: Optional[float] = None
    avg_units: Optional[float] = None
    min_ROI: Optional[float] = None
    max_ROI: Optional[float] = None


class EvaluationBenchmarkRequest(BedrockModel):
    candidate: RunSelector = RunSelector()
    baseline: Optional[RunSelector] = None


class EvaluationBenchmarkResponse(BedrockModel):
    candidate: BenchmarkMetrics
    baseline: Optional[BenchmarkMetrics] = None
    delta: Optional[BenchmarkDelta] = None


@router.post("/benchmark", response_model=EvaluationBenchmarkResponse)
def benchmark_runs(payload: EvaluationBenchmarkRequest) -> EvaluationBenchmarkResponse:
    candidate_selector = payload.candidate
    baseline_selector = payload.baseline

    if baseline_selector is None:
        candidate = service.benchmark(
            run_ids=candidate_selector.run_ids,
            min_roi=candidate_selector.filters.min_ROI,
            min_units=candidate_selector.filters.min_units,
        )
        return EvaluationBenchmarkResponse(candidate=BenchmarkMetrics.model_validate(candidate))

    compared = service.compare(
        candidate_run_ids=candidate_selector.run_ids,
        candidate_min_roi=candidate_selector.filters.min_ROI,
        candidate_min_units=candidate_selector.filters.min_units,
        baseline_run_ids=baseline_selector.run_ids,
        baseline_min_roi=baseline_selector.filters.min_ROI,
        baseline_min_units=baseline_selector.filters.min_units,
    )
    return EvaluationBenchmarkResponse(
        candidate=BenchmarkMetrics.model_validate(compared["candidate"]),
        baseline=BenchmarkMetrics.model_validate(compared["baseline"]),
        delta=BenchmarkDelta.model_validate(compared["delta"]),
    )


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app
