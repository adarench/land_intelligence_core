"""Parcel ingestion API."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

try:  # Prefer canonical package paths when imported as `bedrock.api.*`.
    from bedrock.contracts.parcel import Parcel
    from bedrock.contracts.validators import validate_parcel_output
    from bedrock.services.parcel_service import ParcelService
except ImportError:  # Compatibility mode for top-level `api.*` imports.
    from contracts.parcel import Parcel
    from contracts.validators import validate_parcel_output
    from services.parcel_service import ParcelService


class ParcelLoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parcel_id: Optional[str] = None
    geometry: dict[str, Any]
    jurisdiction: str
    crs: Optional[str] = None
    zoning_district: Optional[str] = None


def create_router(service: ParcelService) -> APIRouter:
    router = APIRouter(prefix="/parcel", tags=["parcel"])

    @router.post("/load", response_model=Parcel)
    def load_parcel(request: ParcelLoadRequest) -> Parcel:
        try:
            return validate_parcel_output(
                service.load_parcel(
                    geometry=request.geometry,
                    parcel_id=request.parcel_id,
                    jurisdiction=request.jurisdiction,
                    crs=request.crs,
                    zoning_district=request.zoning_district,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/{parcel_id}", response_model=Parcel)
    def get_parcel(parcel_id: str) -> Parcel:
        parcel = service.get_parcel(parcel_id)
        if parcel is None:
            raise HTTPException(status_code=404, detail="Parcel not found")
        return validate_parcel_output(parcel)

    return router


def create_app(service: Optional[ParcelService] = None) -> FastAPI:
    app = FastAPI(title="Bedrock Parcel API", version="0.1.0")
    app.include_router(create_router(service or ParcelService()))
    return app


app = create_app()
