"""FastAPI router for ExperimentRun creation and retrieval."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import Field

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.experiment_run import ExperimentRun
from bedrock.contracts.validators import validate_service_output
from bedrock.services.experiment_run_service import ExperimentRunService


router = APIRouter(prefix="/experiments", tags=["experiments"])
service = ExperimentRunService()


class ExperimentCreateRequest(BedrockModel):
    run_ids: list[str]
    config: dict[str, Any] = Field(default_factory=dict)


@router.post("/create", response_model=ExperimentRun)
def create_experiment(payload: ExperimentCreateRequest) -> ExperimentRun:
    try:
        result = service.create(run_ids=payload.run_ids, config=payload.config)
        return validate_service_output("bedrock.api.experiments_api.create_experiment", result)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_experiment_input", "message": str(exc)},
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "pipeline_run_not_found", "message": str(exc)},
        ) from exc


@router.get("/{experiment_id}", response_model=ExperimentRun)
def get_experiment(experiment_id: str) -> ExperimentRun:
    try:
        payload = service.get(experiment_id)
        return validate_service_output("bedrock.api.experiments_api.get_experiment", payload)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "experiment_not_found", "message": f"ExperimentRun '{experiment_id}' was not found"},
        ) from exc


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app
