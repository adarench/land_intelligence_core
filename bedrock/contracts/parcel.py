"""Canonical parcel contract used across the feasibility pipeline."""

from __future__ import annotations

from typing import Dict, List, Optional, Union

from pydantic import AliasChoices, Field, field_validator, model_validator
from shapely.geometry import shape

from .base import BedrockModel, EngineMetadata, Geometry

SUPPORTED_PARCEL_CRS = {"EPSG:4326", "BEDROCK:LOCAL_FEET"}


class Parcel(BedrockModel):
    """Authoritative parcel handoff object for cross-service communication."""

    schema_name: str = Field(default="Parcel", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    parcel_id: str
    geometry: Geometry
    jurisdiction: str
    crs: str = Field(default="EPSG:4326")
    area_sqft: float = Field(gt=0, validation_alias=AliasChoices("area_sqft", "area"))
    centroid: Optional[List[float]] = None
    bounding_box: Optional[List[float]] = None
    land_use: Optional[str] = None
    slope_percent: Optional[float] = Field(default=None, ge=0)
    flood_zone: Optional[str] = None
    zoning_district: Optional[str] = None
    utilities: List[str] = Field(default_factory=list)
    access_points: List[Geometry] = Field(default_factory=list)
    topography: Dict[str, Optional[Union[float, str, int, float]]] = Field(default_factory=dict)
    existing_structures: List[Dict[str, object]] = Field(default_factory=list)
    metadata: Optional[EngineMetadata] = None

    @field_validator("geometry")
    @classmethod
    def _validate_geometry(cls, value: Geometry) -> Geometry:
        geometry_type = value.get("type")
        if geometry_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Parcel.geometry must be a GeoJSON Polygon or MultiPolygon")
        coordinates = value.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("Parcel.geometry must contain coordinates")
        return value

    @field_validator("crs")
    @classmethod
    def _validate_crs(cls, value: str) -> str:
        normalized = str(value).strip()
        if normalized not in SUPPORTED_PARCEL_CRS:
            raise ValueError(
                "Parcel.crs must be one of: " + ", ".join(sorted(SUPPORTED_PARCEL_CRS))
            )
        return normalized

    @field_validator("centroid")
    @classmethod
    def _validate_centroid(cls, value: Optional[List[float]]) -> Optional[List[float]]:
        if value is not None and len(value) != 2:
            raise ValueError("Parcel.centroid must contain [x, y]")
        return value

    @field_validator("bounding_box")
    @classmethod
    def _validate_bounding_box(cls, value: Optional[List[float]]) -> Optional[List[float]]:
        if value is not None and len(value) != 4:
            raise ValueError("Parcel.bounding_box must contain [min_x, min_y, max_x, max_y]")
        return value

    @model_validator(mode="before")
    @classmethod
    def _synchronize_topography(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        geometry = normalized.get("geometry")
        if isinstance(geometry, dict):
            try:
                surface = shape(geometry)
            except Exception:
                surface = None
            if surface is not None and not surface.is_empty:
                if not normalized.get("centroid"):
                    centroid = surface.centroid
                    normalized["centroid"] = [round(float(centroid.x), 6), round(float(centroid.y), 6)]
                if not normalized.get("bounding_box"):
                    min_x, min_y, max_x, max_y = surface.bounds
                    normalized["bounding_box"] = [
                        round(float(min_x), 6),
                        round(float(min_y), 6),
                        round(float(max_x), 6),
                        round(float(max_y), 6),
                    ]
                if not normalized.get("crs"):
                    xs: list[float] = []
                    ys: list[float] = []
                    _collect_positions(geometry.get("coordinates"), xs, ys)
                    if xs and ys and all(-180.0 <= x <= 180.0 for x in xs) and all(-90.0 <= y <= 90.0 for y in ys):
                        normalized["crs"] = "EPSG:4326"
                    else:
                        normalized["crs"] = "BEDROCK:LOCAL_FEET"
        topography = normalized.get("topography")
        topography_payload = dict(topography) if isinstance(topography, dict) else {}
        slope_percent = normalized.get("slope_percent")
        if slope_percent is None:
            slope_value = topography_payload.get("slope_percent")
            if isinstance(slope_value, (int, float)):
                normalized["slope_percent"] = float(slope_value)
        elif "slope_percent" not in topography_payload:
            topography_payload["slope_percent"] = slope_percent
        if topography_payload:
            normalized["topography"] = topography_payload
        return normalized

    @property
    def area(self) -> float:
        """Compatibility alias for older Bedrock consumers."""

        return self.area_sqft


def _collect_positions(value: object, xs: list[float], ys: list[float]) -> None:
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(axis, (int, float)) for axis in value[:2]):
            xs.append(float(value[0]))
            ys.append(float(value[1]))
            return
        for item in value:
            _collect_positions(item, xs, ys)
