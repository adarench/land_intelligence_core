"""Canonical zoning API backed by real overlay datasets."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.validators import validate_parcel_output, validate_zoning_rules_for_layout
from bedrock.services.parcel_service import ParcelService
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.zoning_service import (
    AmbiguousJurisdictionMatchError,
    AmbiguousZoningMatchError,
    IncompleteZoningRulesError,
    InvalidZoningRulesError,
    NoJurisdictionMatchError,
    NoZoningMatchError,
    ZoningService,
)


router = APIRouter(prefix="/zoning", tags=["zoning"])


class ZoningLookupRequest(BedrockModel):
    parcel: Parcel


@router.post("/lookup", response_model=ZoningRules)
def lookup_zoning(request: ZoningLookupRequest) -> ZoningRules:
    try:
        parcel = validate_parcel_output(ParcelService().normalize_parcel_contract(request.parcel))
        return validate_zoning_rules_for_layout(ZoningService().lookup(parcel).rules)
    except (NoJurisdictionMatchError, NoZoningMatchError) as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "no_district_match", "message": str(exc)},
        ) from exc
    except (AmbiguousJurisdictionMatchError, AmbiguousZoningMatchError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "ambiguous_district_match", "message": str(exc)},
        ) from exc
    except IncompleteZoningRulesError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "error": "incomplete_zoning_rules",
                "district": exc.district,
                "missing_fields": exc.missing_fields,
                "usability": exc.usability,
                "available_fields": exc.available_fields,
                "reason_codes": exc.reason_codes,
                "synthetic_fallback_used": exc.synthetic_fallback_used,
            },
        )
    except InvalidZoningRulesError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_zoning_rules",
                "district": exc.district,
                "violations": exc.violations,
            },
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_zoning_rules", "message": str(exc)},
        ) from exc


def create_app() -> FastAPI:
    app = FastAPI(title="Bedrock Zoning API", version="0.1.0")
    app.include_router(router)
    return app
