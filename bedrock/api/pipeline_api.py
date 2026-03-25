"""FastAPI router for PO-2 full pipeline execution."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import Field, model_validator

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.market_data import MarketData
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.pipeline_run import PipelineRun
from bedrock.contracts.validators import validate_pipeline_run_output
from bedrock.services.pipeline_run_store import PipelineRunStore
from bedrock.services.pipeline_service import PipelineExecutionResult, PipelineService, PipelineStageError
from bedrock.services.zoning_service import IncompleteZoningRulesError
from zoning_data_scraper.services.zoning_overlay import (
    AmbiguousJurisdictionMatchError,
    AmbiguousZoningMatchError,
)


router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class PipelineRunRequest(BedrockModel):
    parcel: Optional[Parcel] = None
    parcel_geometry: Optional[dict] = None
    parcel_id: Optional[str] = None
    jurisdiction: Optional[str] = None
    max_candidates: int = Field(default=50, ge=1, le=250)
    market_context: Optional[MarketData] = None

    @model_validator(mode="after")
    def _require_parcel_input(self) -> "PipelineRunRequest":
        if self.parcel is None and self.parcel_geometry is None:
            raise ValueError("Request must include either 'parcel' or 'parcel_geometry'")
        if self.parcel_geometry is not None and not (self.jurisdiction or "").strip():
            raise ValueError("Request must include 'jurisdiction' when 'parcel_geometry' is provided")
        return self


def _to_pipeline_run_response(
    execution_result: PipelineExecutionResult,
    *,
    service: PipelineService,
) -> PipelineRun:
    run_payload = service.run_store.load_run(execution_result.run_id)
    return validate_pipeline_run_output(PipelineRunStore._normalize_pipeline_run_payload(run_payload))


@router.post("/run", response_model=PipelineRun)
def run_pipeline(request: PipelineRunRequest) -> PipelineRun:
    service = PipelineService()
    try:
        execution_result = service.run(
            parcel=request.parcel,
            parcel_geometry=request.parcel_geometry,
            max_candidates=request.max_candidates,
            parcel_id=request.parcel_id,
            jurisdiction=request.jurisdiction,
            market_data=request.market_context,
        )
        return _to_pipeline_run_response(execution_result, service=service)
    except PipelineStageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_parcel_input", "message": str(exc)},
        ) from exc
    except (AmbiguousJurisdictionMatchError, AmbiguousZoningMatchError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "ambiguous_district_match", "message": str(exc)},
        ) from exc
    except IncompleteZoningRulesError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "incomplete_zoning_rules",
                "district": exc.district,
                "missing_fields": exc.missing_fields,
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "layout_solver_failure", "message": str(exc)},
        ) from exc


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app
