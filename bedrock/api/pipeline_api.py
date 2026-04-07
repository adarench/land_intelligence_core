"""FastAPI router for PO-2 full pipeline execution."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import Field, model_validator

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.market_data import MarketData
from bedrock.contracts.optimization_run import OptimizationObjective, OptimizationRun
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.pipeline_run import PipelineRun
from bedrock.contracts.validators import validate_optimization_run_output, validate_pipeline_run_output
from bedrock.services.pipeline_run_store import PipelineRunStore
from bedrock.services.flood_enrichment_service import FloodEnrichmentService
from bedrock.services.pipeline_service import PipelineExecutionResult, PipelineService, PipelineStageError
from bedrock.services.slope_enrichment_service import SlopeEnrichmentService
from bedrock.services.zoning_service import IncompleteZoningRulesError

_flood_service = FloodEnrichmentService()
_slope_service = SlopeEnrichmentService()
from zoning_data_scraper.services.zoning_overlay import (
    AmbiguousJurisdictionMatchError,
    AmbiguousZoningMatchError,
    diagnose_zoning_resolution,
)


router = APIRouter(prefix="/pipeline", tags=["pipeline"])


def _resolve_land_basis(
    market_context: Optional[MarketData],
    land_basis_mode: str,
    land_basis_value: Optional[float],
) -> Optional[MarketData]:
    """Apply land basis mode to market data before passing to pipeline."""
    if land_basis_mode == "excluded":
        if market_context is None:
            return MarketData(
                estimated_home_price=480000.0,
                construction_cost_per_home=260000.0,
                road_cost_per_ft=300.0,
                land_price=0.0,
            )
        return MarketData(
            estimated_home_price=market_context.estimated_home_price,
            construction_cost_per_home=market_context.construction_cost_per_home,
            road_cost_per_ft=market_context.road_cost_per_ft,
            land_price=0.0,
            soft_cost_factor=market_context.soft_cost_factor,
        )
    if land_basis_mode == "user":
        if land_basis_value is None:
            raise ValueError("land_basis_value is required when land_basis_mode is 'user'")
        if market_context is None:
            return MarketData(
                estimated_home_price=480000.0,
                construction_cost_per_home=260000.0,
                road_cost_per_ft=300.0,
                land_price=land_basis_value,
            )
        return MarketData(
            estimated_home_price=market_context.estimated_home_price,
            construction_cost_per_home=market_context.construction_cost_per_home,
            road_cost_per_ft=market_context.road_cost_per_ft,
            land_price=land_basis_value,
            soft_cost_factor=market_context.soft_cost_factor,
        )
    # "proxy" — let MarketIntelligenceService compute land_price
    return market_context


class PipelineRunRequest(BedrockModel):
    parcel: Optional[Parcel] = None
    parcel_geometry: Optional[dict] = None
    parcel_id: Optional[str] = None
    jurisdiction: Optional[str] = None
    max_candidates: int = Field(default=50, ge=1, le=250)
    market_context: Optional[MarketData] = None
    include_carry_cost: bool = False
    land_basis_mode: Literal["excluded", "proxy", "user"] = "proxy"
    land_basis_value: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_parcel_input(self) -> "PipelineRunRequest":
        if self.parcel is None and self.parcel_geometry is None:
            raise ValueError("Request must include either 'parcel' or 'parcel_geometry'")
        if self.parcel_geometry is not None and not (self.jurisdiction or "").strip():
            raise ValueError("Request must include 'jurisdiction' when 'parcel_geometry' is provided")
        return self


class PipelineOptimizeRequest(BedrockModel):
    parcel: Optional[Parcel] = None
    parcel_geometry: Optional[dict] = None
    parcel_id: Optional[str] = None
    jurisdiction: Optional[str] = None
    max_candidates: int = Field(default=48, ge=1, le=48)
    market_context: Optional[MarketData] = None
    objective: OptimizationObjective = Field(default_factory=OptimizationObjective)
    max_rounds: int = Field(default=3, ge=1, le=4)
    include_carry_cost: bool = False
    land_basis_mode: Literal["excluded", "proxy", "user"] = "proxy"
    land_basis_value: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_parcel_input(self) -> "PipelineOptimizeRequest":
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
    service = PipelineService(flood_enrichment_service=_flood_service, slope_enrichment_service=_slope_service)
    service._include_carry_cost = request.include_carry_cost
    market_data = _resolve_land_basis(request.market_context, request.land_basis_mode, request.land_basis_value)
    service._land_basis_mode = request.land_basis_mode
    try:
        execution_result = service.run(
            parcel=request.parcel,
            parcel_geometry=request.parcel_geometry,
            max_candidates=request.max_candidates,
            parcel_id=request.parcel_id,
            jurisdiction=request.jurisdiction,
            market_data=market_data,
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


@router.post("/optimize", response_model=OptimizationRun)
def optimize_pipeline(request: PipelineOptimizeRequest) -> OptimizationRun:
    service = PipelineService(flood_enrichment_service=_flood_service, slope_enrichment_service=_slope_service)
    service._include_carry_cost = request.include_carry_cost
    market_data = _resolve_land_basis(request.market_context, request.land_basis_mode, request.land_basis_value)
    service._land_basis_mode = request.land_basis_mode
    try:
        result = service.optimize(
            parcel=request.parcel,
            parcel_geometry=request.parcel_geometry,
            max_candidates=request.max_candidates,
            parcel_id=request.parcel_id,
            jurisdiction=request.jurisdiction,
            market_data=market_data,
            objective=request.objective,
            max_rounds=request.max_rounds,
        )
        return validate_optimization_run_output(result)
    except PipelineStageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_optimization_input", "message": str(exc)},
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
            detail={"error": "optimization_runtime_error", "message": str(exc)},
        ) from exc


class BatchOptimizeRequest(BedrockModel):
    parcel_ids: list[str] = Field(min_length=1, max_length=50)
    max_candidates: int = Field(default=12, ge=1, le=48)
    market_context: Optional[MarketData] = None
    objective: OptimizationObjective = Field(default_factory=OptimizationObjective)
    max_rounds: int = Field(default=2, ge=1, le=4)


class BatchOptimizeResultItem(BedrockModel):
    parcel_id: str
    optimization_run_id: Optional[str] = None
    recommendation: Optional[str] = None
    roi: Optional[float] = None
    projected_profit: Optional[float] = None
    units: Optional[int] = None
    status: str = "completed"
    error: Optional[str] = None


class BatchOptimizeResponse(BedrockModel):
    results: list[BatchOptimizeResultItem]
    total: int
    succeeded: int
    failed: int


@router.post("/optimize/batch", response_model=BatchOptimizeResponse)
def optimize_batch(request: BatchOptimizeRequest) -> BatchOptimizeResponse:
    service = PipelineService(flood_enrichment_service=_flood_service, slope_enrichment_service=_slope_service)
    results: list[BatchOptimizeResultItem] = []

    for parcel_id in request.parcel_ids:
        try:
            parcel = service.parcel_service.get_parcel(parcel_id)
            if parcel is None:
                results.append(BatchOptimizeResultItem(
                    parcel_id=parcel_id,
                    status="failed",
                    error=f"Parcel '{parcel_id}' not found",
                ))
                continue

            optimization_run = service.optimize(
                parcel=parcel,
                max_candidates=request.max_candidates,
                market_data=request.market_context,
                objective=request.objective,
                max_rounds=request.max_rounds,
            )
            decision = optimization_run.decision
            best = optimization_run.best_candidate
            fr = best.feasibility_result if best else None

            results.append(BatchOptimizeResultItem(
                parcel_id=parcel_id,
                optimization_run_id=optimization_run.optimization_run_id,
                recommendation=decision.recommendation if decision else None,
                roi=fr.ROI_base or fr.ROI if fr else None,
                projected_profit=fr.projected_profit if fr else None,
                units=fr.units if fr else None,
                status="completed",
            ))
        except Exception as exc:
            results.append(BatchOptimizeResultItem(
                parcel_id=parcel_id,
                status="failed",
                error=str(exc)[:200],
            ))

    succeeded = sum(1 for r in results if r.status == "completed")
    return BatchOptimizeResponse(
        results=results,
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )


class InferenceRequest(BedrockModel):
    parcel: Optional[Parcel] = None
    parcel_id: Optional[str] = None

    @model_validator(mode="after")
    def _require_input(self) -> "InferenceRequest":
        if self.parcel is None and not self.parcel_id:
            raise ValueError("Request must include either 'parcel' or 'parcel_id'")
        return self


@router.post("/infer")
def infer_feasibility(request: InferenceRequest) -> dict:
    """Run LLM-powered feasibility inference for parcels without overlay zoning."""
    from bedrock.services.inference_service import run_inference

    service = PipelineService()
    if request.parcel:
        parcel = request.parcel
    else:
        parcel = service.parcel_service.get_parcel(request.parcel_id)
        if parcel is None:
            raise HTTPException(status_code=404, detail={"error": "parcel_not_found"})

    # Get zoning hint from fallback
    zoning_hint = None
    try:
        from bedrock.services.zoning_service import ZoningService
        zs = ZoningService()
        result = zs.lookup(parcel)
        zoning_hint = {
            "district": result.rules.district,
            "max_units_per_acre": result.rules.max_units_per_acre,
            "min_lot_size_sqft": result.rules.min_lot_size_sqft,
        }
    except Exception:
        pass

    return run_inference(parcel, zoning_hint=zoning_hint)


@router.post("/infer/stream")
async def infer_feasibility_stream(request: InferenceRequest):
    """Stream progressive inference updates then final result."""
    from fastapi.responses import StreamingResponse
    from bedrock.services.inference_service import run_inference_streaming
    import json as _json

    service = PipelineService()
    if request.parcel:
        parcel = request.parcel
    else:
        parcel = service.parcel_service.get_parcel(request.parcel_id)
        if parcel is None:
            raise HTTPException(status_code=404, detail={"error": "parcel_not_found"})

    zoning_hint = None
    try:
        from bedrock.services.zoning_service import ZoningService
        zs = ZoningService()
        result = zs.lookup(parcel)
        zoning_hint = {
            "district": result.rules.district,
            "max_units_per_acre": result.rules.max_units_per_acre,
            "min_lot_size_sqft": result.rules.min_lot_size_sqft,
        }
    except Exception:
        pass

    def generate():
        for event in run_inference_streaming(parcel, zoning_hint=zoning_hint):
            yield _json.dumps(event, default=str) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.get("/zoning-debug/{parcel_id}")
def zoning_debug(parcel_id: str):
    """Return structured diagnostics for zoning resolution on a parcel."""
    from shapely.geometry import shape as shapely_shape

    service = PipelineService()
    parcel = service.parcel_service.get_parcel(parcel_id)
    if parcel is None:
        raise HTTPException(status_code=404, detail=f"Parcel '{parcel_id}' not found")

    try:
        parcel_geometry = shapely_shape(parcel.geometry)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_geometry", "message": f"Cannot parse parcel geometry: {exc}"},
        ) from exc

    diag = diagnose_zoning_resolution(
        parcel_geometry,
        parcel_jurisdiction=parcel.jurisdiction or "",
    )

    diag["parcel_id"] = parcel_id
    diag["parcel_jurisdiction"] = parcel.jurisdiction
    diag["parcel_zoning_district"] = parcel.zoning_district

    return diag


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app
