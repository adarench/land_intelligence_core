"""Canonical Bedrock layout API."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field
from pydantic import ValidationError

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.layout_candidate_batch import LayoutCandidateBatch, LayoutSearchPlan
from bedrock.contracts.layout_result import LayoutResult
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.validators import (
    validate_layout_candidate_batch_output,
    build_layout_result,
    validate_layout_result_output,
    validate_parcel_output,
)
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.layout_export_service import LayoutExportError, export_layout_artifact
from bedrock.services.layout_service import (
    LayoutSearchError,
    search_layout,
    search_layout_candidates_debug,
)

app = FastAPI(title="Bedrock Layout API", version="0.1.0")


class LayoutSearchRequest(BedrockModel):
    parcel: Parcel
    zoning: ZoningRules
    max_candidates: int = Field(default=50, ge=1, le=250)


class LayoutCandidatesRequest(BedrockModel):
    parcel: Parcel
    zoning: ZoningRules
    label: str = "default"
    max_candidates: int = Field(default=24, ge=1, le=48)
    max_layouts: int = Field(default=5, ge=1, le=10)
    strategies: list[str] = Field(default_factory=list)
    density_factor: float = Field(default=1.0, ge=0.25, le=1.0)
    lot_depth_factor: float = Field(default=1.0, ge=0.8, le=1.5)
    frontage_hint_factor: float = Field(default=1.0, ge=0.85, le=1.2)
    road_width_factor: float = Field(default=1.0, ge=0.8, le=1.2)
    runtime_budget_factor: float = Field(default=1.0, ge=0.8, le=1.5)

    def to_search_plan(self) -> LayoutSearchPlan:
        return LayoutSearchPlan(
            label=self.label,
            strategies=self.strategies,
            max_candidates=self.max_candidates,
            max_layouts=self.max_layouts,
            density_factor=self.density_factor,
            lot_depth_factor=self.lot_depth_factor,
            frontage_hint_factor=self.frontage_hint_factor,
            road_width_factor=self.road_width_factor,
            runtime_budget_factor=self.runtime_budget_factor,
        )


class LayoutExportRequest(BedrockModel):
    parcel: Parcel
    layout: LayoutResult
    format: Literal["dxf", "step", "geojson"] = "dxf"
    zoning: Optional[ZoningRules] = None


@app.post("/layout/search", response_model=LayoutResult)
def layout_search(request: LayoutSearchRequest) -> LayoutResult:
    try:
        parcel = validate_parcel_output(request.parcel)
        zoning = request.zoning
        if zoning.parcel_id != parcel.parcel_id:
            raise ValueError(
                "Contract mismatch: ZoningRules.parcel_id must match Parcel.parcel_id"
            )
        return build_layout_result(
            parcel.parcel_id,
            validate_layout_result_output(
                search_layout(parcel, zoning, max_candidates=request.max_candidates)
            ),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_layout_input", "message": str(exc)}) from exc
    except LayoutSearchError as exc:
        status_code = 400 if exc.code == "non_usable_zoning" else 422
        raise HTTPException(
            status_code=status_code,
            detail={"error": exc.code, "message": exc.message},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_layout_input", "message": str(exc)}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail={"error": "layout_runtime_error", "message": str(exc)}) from exc


@app.post("/layout/candidates", response_model=LayoutCandidateBatch)
def layout_candidates(request: LayoutCandidatesRequest) -> LayoutCandidateBatch:
    try:
        parcel = validate_parcel_output(request.parcel)
        zoning = request.zoning
        if zoning.parcel_id != parcel.parcel_id:
            raise ValueError(
                "Contract mismatch: ZoningRules.parcel_id must match Parcel.parcel_id"
            )
        batch = search_layout_candidates_debug(
            parcel,
            zoning,
            search_plan=request.to_search_plan(),
        )
        return validate_layout_candidate_batch_output(batch)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_layout_input", "message": str(exc)}) from exc
    except LayoutSearchError as exc:
        status_code = 400 if exc.code == "non_usable_zoning" else 422
        raise HTTPException(
            status_code=status_code,
            detail={"error": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_layout_input", "message": str(exc)}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail={"error": "layout_runtime_error", "message": str(exc)}) from exc


@app.post("/layout/export")
def layout_export(request: dict[str, Any]) -> FileResponse:
    try:
        payload = LayoutExportRequest.model_validate(request)
        parcel = validate_parcel_output(payload.parcel)
        layout = validate_layout_result_output(payload.layout)
        if layout.parcel_id != parcel.parcel_id:
            raise ValueError(
                "Contract mismatch: LayoutResult.parcel_id must match Parcel.parcel_id"
            )
        if payload.zoning is not None and payload.zoning.parcel_id != parcel.parcel_id:
            raise ValueError(
                "Contract mismatch: ZoningRules.parcel_id must match Parcel.parcel_id"
            )
        artifact = export_layout_artifact(
            parcel,
            layout,
            export_format=payload.format,
            zoning=payload.zoning,
        )
        return FileResponse(
            path=str(artifact.path),
            media_type=artifact.media_type,
            filename=artifact.filename,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_layout_export_input", "message": str(exc)}) from exc
    except LayoutExportError as exc:
        raise HTTPException(status_code=422, detail={"error": exc.code, "message": exc.message}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_layout_export_input", "message": str(exc)}) from exc
