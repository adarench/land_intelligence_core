"""Parcel ingestion service."""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

try:  # Prefer canonical package paths when Bedrock is imported as a package.
    from bedrock.contracts.parcel import Parcel
    from bedrock.contracts.validators import validate_contract
    from bedrock.contracts.validators import validate_parcel_output
    from bedrock.services.jurisdiction_resolver import is_known_jurisdiction
    from bedrock.services.jurisdiction_resolver import resolve_jurisdiction
    from bedrock.services.parcel_store import ParcelStore
    from bedrock.utils.geometry_normalization import (
        compute_area_sqft,
        compute_bounding_box,
        compute_centroid,
        normalize_polygon_geometry,
    )
except ImportError:  # Compatibility mode for runtime surfaces that bootstrap `bedrock/` as PYTHONPATH root.
    from contracts.parcel import Parcel
    from contracts.validators import validate_contract
    from contracts.validators import validate_parcel_output
    from services.jurisdiction_resolver import is_known_jurisdiction
    from services.jurisdiction_resolver import resolve_jurisdiction
    from services.parcel_store import ParcelStore
    from utils.geometry_normalization import (
        compute_area_sqft,
        compute_bounding_box,
        compute_centroid,
        normalize_polygon_geometry,
    )


class ParcelService:
    """Normalizes inbound parcel geometries into the shared Parcel contract."""

    source_engine = "bedrock.parcel_service"

    def __init__(self, store: Optional[ParcelStore] = None) -> None:
        self.store = store or ParcelStore()

    def load_parcel(
        self,
        geometry: dict[str, Any],
        parcel_id: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        crs: Optional[str] = None,
        zoning_district: Optional[str] = None,
    ) -> Parcel:
        normalized_parcel = self._build_normalized_parcel(
            geometry=geometry,
            parcel_id=parcel_id,
            jurisdiction=jurisdiction,
            crs=crs,
            zoning_district=zoning_district,
        )
        if parcel_id is not None:
            existing = self.store.get_parcel(parcel_id)
            if existing is not None:
                try:
                    existing = self.normalize_parcel_contract(existing)
                except ValueError:
                    return self.store.replace_parcel(normalized_parcel)
                if self._equivalent_except_zoning(existing, normalized_parcel):
                    if existing.zoning_district != normalized_parcel.zoning_district:
                        return self.store.replace_parcel(normalized_parcel)
                    return existing
                return self.store.replace_parcel(normalized_parcel)

        return self.store.save_parcel(normalized_parcel)

    def normalize_parcel_contract(self, parcel: Parcel | dict[str, Any]) -> Parcel:
        contract = validate_contract("Parcel", parcel)
        return self._build_normalized_parcel(
            geometry=contract.geometry,
            parcel_id=contract.parcel_id,
            jurisdiction=contract.jurisdiction,
            crs=contract.crs,
            zoning_district=contract.zoning_district,
            land_use=contract.land_use,
            slope_percent=contract.slope_percent,
            flood_zone=contract.flood_zone,
            utilities=list(contract.utilities),
            access_points=list(contract.access_points),
            topography=dict(contract.topography),
            existing_structures=[dict(item) for item in contract.existing_structures],
        )

    def get_parcel(self, parcel_id: str) -> Optional[Parcel]:
        stored = self.store.get_parcel(parcel_id)
        if stored is None:
            return None
        try:
            normalized = self.normalize_parcel_contract(stored)
        except ValueError:
            return None
        if not self._equivalent(stored, normalized):
            return self.store.replace_parcel(normalized)
        return stored

    def parcel_exists(self, parcel_id: str) -> bool:
        return self.store.parcel_exists(parcel_id)

    def _build_normalized_parcel(
        self,
        *,
        geometry: dict[str, Any],
        parcel_id: Optional[str],
        jurisdiction: Optional[str],
        crs: Optional[str],
        zoning_district: Optional[str],
        land_use: Optional[str] = None,
        slope_percent: Optional[float] = None,
        flood_zone: Optional[str] = None,
        utilities: Optional[list[str]] = None,
        access_points: Optional[list[dict[str, Any]]] = None,
        topography: Optional[dict[str, Any]] = None,
        existing_structures: Optional[list[dict[str, Any]]] = None,
    ) -> Parcel:
        normalized_geometry, polygon, normalized_crs = normalize_polygon_geometry(geometry, input_crs=crs)
        centroid = compute_centroid(polygon)
        bounding_box = compute_bounding_box(polygon)
        resolved_jurisdiction = self._resolve_jurisdiction(jurisdiction, centroid)
        return Parcel(
            parcel_id=parcel_id or f"parcel-{uuid4()}",
            geometry=normalized_geometry,
            jurisdiction=resolved_jurisdiction,
            crs=normalized_crs,
            area_sqft=compute_area_sqft(polygon, crs=normalized_crs),
            centroid=centroid,
            bounding_box=bounding_box,
            land_use=land_use,
            slope_percent=slope_percent,
            flood_zone=flood_zone,
            zoning_district=zoning_district,
            utilities=list(utilities or []),
            access_points=list(access_points or []),
            topography=dict(topography or {}),
            existing_structures=[dict(item) for item in (existing_structures or [])],
            metadata={
                "source_engine": self.source_engine,
                "source_run_id": None,
            },
        )

    @staticmethod
    def _resolve_jurisdiction(jurisdiction: Optional[str], centroid: list[float]) -> str:
        if jurisdiction is not None:
            normalized = str(jurisdiction).strip()
            if not normalized:
                raise ValueError("jurisdiction is required")
            return normalized
        resolved = resolve_jurisdiction(centroid)
        if resolved is None:
            raise ValueError("jurisdiction is required and could not be resolved deterministically")
        return resolved

    @staticmethod
    def _equivalent(left: Parcel, right: Parcel) -> bool:
        return (
            left.parcel_id == right.parcel_id
            and left.geometry == right.geometry
            and float(left.area_sqft) == float(right.area_sqft)
            and list(left.centroid or []) == list(right.centroid or [])
            and list(left.bounding_box or []) == list(right.bounding_box or [])
            and left.jurisdiction == right.jurisdiction
            and left.zoning_district == right.zoning_district
        )

    @classmethod
    def _equivalent_except_zoning(cls, left: Parcel, right: Parcel) -> bool:
        return cls._equivalent(
            left.model_copy(update={"zoning_district": right.zoning_district}),
            right,
        )
