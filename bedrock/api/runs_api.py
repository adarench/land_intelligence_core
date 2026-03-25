"""FastAPI router for persisted pipeline runs."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.pipeline_run import PipelineRun
from bedrock.contracts.validators import validate_service_output
from bedrock.services.pipeline_run_store import PipelineRunStore


router = APIRouter(prefix="/runs", tags=["runs"])
store = PipelineRunStore()


class PipelineRunSummary(BedrockModel):
    run_id: str
    timestamp: str
    parcel_id: Optional[str] = None
    units: Optional[int] = None
    projected_profit: Optional[float] = None
    ROI: Optional[float] = None


@router.get("", response_model=list[PipelineRunSummary])
def list_runs(
    sort: Literal["ROI", "projected_profit", "units", "timestamp"] = "timestamp",
    order: Literal["asc", "desc"] = "desc",
    min_ROI: Optional[float] = None,
    max_ROI: Optional[float] = None,
    min_units: Optional[int] = None,
    max_units: Optional[int] = None,
    limit: Optional[int] = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
) -> list[PipelineRunSummary]:
    return [
        PipelineRunSummary.model_validate(item)
        for item in store.list_runs(
            sort=sort,
            order=order,
            min_roi=min_ROI,
            max_roi=max_ROI,
            min_units=min_units,
            max_units=max_units,
            limit=limit,
            offset=offset,
        )
    ]


@router.get("/{run_id}", response_model=PipelineRun)
def get_run(run_id: str) -> PipelineRun:
    try:
        return validate_service_output("bedrock.api.runs_api.get_run", store.load_run(run_id))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "run_not_found", "message": f"PipelineRun '{run_id}' was not found"},
        ) from exc


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app
