"""FastAPI router for deterministic feasibility evaluation."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import AliasChoices, Field

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.validators import (
    validate_feasibility_result_output,
    validate_layout_result_output,
    validate_parcel_output,
)
from bedrock.services.feasibility_service import FeasibilityService

router = APIRouter(prefix="/feasibility", tags=["feasibility"])
service = FeasibilityService()


class MarketDataInput(BedrockModel):
    estimated_home_price: Optional[float] = Field(default=None, ge=0)
    construction_cost_per_home: Optional[float] = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("construction_cost_per_home", "cost_per_home"),
    )
    road_cost_per_ft: Optional[float] = Field(default=None, ge=0)
    land_price: Optional[float] = Field(default=None, ge=0)
    soft_cost_factor: Optional[float] = Field(default=None, ge=0)


class FeasibilityEvaluateRequest(BedrockModel):
    parcel: Parcel
    layout: SubdivisionLayout
    market_data: Optional[MarketDataInput] = Field(default=None, alias="market_context")


@router.post("/evaluate", response_model=FeasibilityResult)
async def evaluate_feasibility(request: FeasibilityEvaluateRequest) -> FeasibilityResult:
    try:
        parcel = validate_parcel_output(request.parcel)
        layout = validate_layout_result_output(request.layout)
        if layout.parcel_id != parcel.parcel_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "contract_mismatch",
                    "message": "LayoutResult.parcel_id must match Parcel.parcel_id",
                },
            )

        market_data = (
            None
            if request.market_data is None
            else service.market_data_from_overrides(
                estimated_home_price=request.market_data.estimated_home_price,
                construction_cost_per_home=request.market_data.construction_cost_per_home,
                road_cost_per_ft=request.market_data.road_cost_per_ft,
                land_price=request.market_data.land_price,
                soft_cost_factor=request.market_data.soft_cost_factor,
            )
        )
        return validate_feasibility_result_output(
            service.evaluate(
                parcel=parcel,
                layout=layout,
                market_data=market_data,
            )
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_feasibility_input", "message": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "feasibility_evaluation_failure", "message": str(exc)},
        ) from exc


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app
