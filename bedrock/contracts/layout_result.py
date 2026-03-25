"""Canonical layout result contract for zoning-to-feasibility handoff."""

from __future__ import annotations

from typing import List, Optional

from pydantic import AliasChoices, Field, field_validator

from .base import BedrockModel, EngineMetadata, Geometry


class LayoutResult(BedrockModel):
    """Authoritative output contract for layout generation services."""

    schema_name: str = Field(default="LayoutResult", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    layout_id: str
    parcel_id: str
    unit_count: int = Field(ge=0, validation_alias=AliasChoices("unit_count", "units", "lot_count"))
    road_length_ft: float = Field(default=0.0, ge=0, validation_alias=AliasChoices("road_length_ft", "road_length"))
    lot_geometries: List[Geometry] = Field(default_factory=list)
    road_geometries: List[Geometry] = Field(
        default_factory=list,
        validation_alias=AliasChoices("road_geometries", "street_network"),
    )
    open_space_area_sqft: float = Field(
        default=0.0,
        ge=0,
        validation_alias=AliasChoices("open_space_area_sqft", "open_space_area"),
    )
    utility_length_ft: float = Field(
        default=0.0,
        ge=0,
        validation_alias=AliasChoices("utility_length_ft", "utility_length"),
    )
    score: Optional[float] = None
    buildable_area_sqft: Optional[float] = Field(default=None, ge=0)
    metadata: Optional[EngineMetadata] = None

    @field_validator("lot_geometries", "road_geometries")
    @classmethod
    def _validate_geometry_collection(cls, value: List[Geometry]) -> List[Geometry]:
        for item in value:
            if not isinstance(item, dict) or "type" not in item:
                raise ValueError("Layout geometry collections must contain GeoJSON objects")
        return value

    @property
    def lot_count(self) -> int:
        return self.unit_count

    @property
    def units(self) -> int:
        return self.unit_count

    @property
    def road_length(self) -> float:
        return self.road_length_ft

    @property
    def street_network(self) -> List[Geometry]:
        return self.road_geometries

    @property
    def open_space_area(self) -> float:
        return self.open_space_area_sqft

    @property
    def utility_length(self) -> float:
        return self.utility_length_ft
