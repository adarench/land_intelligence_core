"""FastAPI router for persisted optimization runs."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.optimization_run import OptimizationRun
from bedrock.contracts.validators import validate_optimization_run_output
from bedrock.services.pipeline_run_store import PipelineRunStore


router = APIRouter(prefix="/optimization/runs", tags=["optimization"])
store = PipelineRunStore()


class OptimizationRunSummary(BedrockModel):
    optimization_run_id: str
    timestamp: str
    parcel_id: Optional[str] = None
    candidate_count: int = 0
    best_layout_id: Optional[str] = None
    best_roi: Optional[float] = None
    best_projected_profit: Optional[float] = None
    selected_pipeline_run_id: Optional[str] = None


@router.get("", response_model=list[OptimizationRunSummary])
def list_optimization_runs(
    limit: Optional[int] = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
) -> list[OptimizationRunSummary]:
    return [
        OptimizationRunSummary.model_validate(item)
        for item in store.list_optimization_runs(limit=limit, offset=offset)
    ]


@router.get("/{optimization_run_id}", response_model=OptimizationRun)
def get_optimization_run(optimization_run_id: str) -> OptimizationRun:
    try:
        return validate_optimization_run_output(store.load_optimization_run(optimization_run_id))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "optimization_run_not_found",
                "message": f"OptimizationRun '{optimization_run_id}' was not found",
            },
        ) from exc


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app
