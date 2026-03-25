"""Canonical zoning rules contract for parcel-to-layout handoff."""

from __future__ import annotations

from typing import Dict, List, Optional, Union

from pydantic import AliasChoices, Field, model_validator

from .base import BedrockModel, EngineMetadata

StandardValue = Union[str, float, int, bool]


class ZoningDistrict(BedrockModel):
    """Compatibility contract describing a zoning district identity."""

    id: str
    jurisdiction_id: str
    code: str = Field(validation_alias=AliasChoices("code", "district"))
    description: str = ""
    metadata: Optional[EngineMetadata] = None


class DevelopmentStandard(BedrockModel):
    """Normalized representation of a single enforceable rule."""

    id: str
    district_id: Optional[str] = None
    standard_type: str
    value: StandardValue
    units: Optional[str] = None
    conditions: List[str] = Field(default_factory=list)
    citation: Optional[str] = None
    metadata: Optional[EngineMetadata] = None


class SetbackSet(BedrockModel):
    front: Optional[float] = Field(default=None, ge=0)
    side: Optional[float] = Field(default=None, ge=0)
    rear: Optional[float] = Field(default=None, ge=0)


class ZoningRules(BedrockModel):
    """Authoritative zoning handoff object consumed by layout and feasibility."""

    schema_name: str = Field(default="ZoningRules", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    parcel_id: str
    jurisdiction: Optional[str] = None
    district: str = Field(validation_alias=AliasChoices("district", "code", "zoning_district"))
    district_id: Optional[str] = None
    description: Optional[str] = None
    overlays: List[str] = Field(default_factory=list)
    standards: List[DevelopmentStandard] = Field(default_factory=list)
    setbacks: SetbackSet = Field(default_factory=SetbackSet)
    min_lot_size_sqft: Optional[float] = Field(default=None, gt=0)
    max_units_per_acre: Optional[float] = Field(default=None, ge=0)
    height_limit_ft: Optional[float] = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "height_limit_ft",
            "height_limit",
            "max_height",
            "max_building_height_ft",
        ),
    )
    lot_coverage_max: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        validation_alias=AliasChoices(
            "lot_coverage_max",
            "lot_coverage_limit",
            "max_lot_coverage",
        ),
    )
    min_frontage_ft: Optional[float] = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("min_frontage_ft", "min_lot_width_ft"),
    )
    road_right_of_way_ft: Optional[float] = Field(default=None, ge=0)
    allowed_uses: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("allowed_uses", "allowed_use_types"),
    )
    citations: List[str] = Field(default_factory=list)
    metadata: Optional[EngineMetadata] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_standards(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        if normalized.get("height_limit_ft") is None:
            normalized["height_limit_ft"] = (
                normalized.pop("max_height", None)
                or normalized.pop("max_building_height_ft", None)
                or normalized.get("height_limit")
            )
        else:
            normalized.pop("max_height", None)
            normalized.pop("max_building_height_ft", None)
        if normalized.get("lot_coverage_max") is None:
            normalized["lot_coverage_max"] = normalized.pop("max_lot_coverage", None) or normalized.get(
                "lot_coverage_limit"
            )
        else:
            normalized.pop("max_lot_coverage", None)
        if normalized.get("min_frontage_ft") is None and normalized.get("min_lot_width_ft") is not None:
            normalized["min_frontage_ft"] = normalized.pop("min_lot_width_ft")
        else:
            normalized.pop("min_lot_width_ft", None)
        if normalized.get("allowed_uses") is None and normalized.get("allowed_use_types") is not None:
            normalized["allowed_uses"] = normalized.pop("allowed_use_types")
        else:
            normalized.pop("allowed_use_types", None)

        overlays_input = normalized.get("overlays", normalized.get("overlay", []))
        if isinstance(overlays_input, str):
            overlay_items = [overlays_input]
        elif isinstance(overlays_input, list):
            overlay_items = overlays_input
        else:
            overlay_items = []
        normalized.pop("overlay", None)
        normalized["overlays"] = list(
            dict.fromkeys(
                item.strip()
                for item in overlay_items
                if isinstance(item, str) and item.strip()
            )
        )

        standards_input = normalized.get("standards") or []
        standards_payload: List[Dict[str, object]] = []
        for item in standards_input:
            if hasattr(item, "model_dump"):
                standards_payload.append(item.model_dump())
            else:
                standards_payload.append(dict(item))

        standards_by_type: Dict[str, Dict[str, object]] = {
            str(item.get("standard_type", "")).lower(): item
            for item in standards_payload
            if item.get("standard_type")
        }

        def numeric_value(name: str) -> Optional[float]:
            item = standards_by_type.get(name)
            if item is None:
                return None
            value = item.get("value")
            if isinstance(value, bool):
                return None
            try:
                return float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        setbacks_input = normalized.get("setbacks")
        if hasattr(setbacks_input, "model_dump"):
            setbacks_payload = setbacks_input.model_dump()
        elif isinstance(setbacks_input, dict):
            setbacks_payload = dict(setbacks_input)
        else:
            setbacks_payload = {}

        for field_name in (
            "min_lot_size_sqft",
            "max_units_per_acre",
            "height_limit_ft",
            "lot_coverage_max",
            "min_frontage_ft",
            "road_right_of_way_ft",
        ):
            if normalized.get(field_name) is None:
                normalized[field_name] = numeric_value(field_name)

        for edge in ("front", "side", "rear"):
            attr_name = f"{edge}_setback_ft"
            if setbacks_payload.get(edge) is None:
                derived = numeric_value(attr_name)
                if derived is not None:
                    setbacks_payload[edge] = derived
        normalized["setbacks"] = setbacks_payload

        district = normalized.get("district") or normalized.get("code") or normalized.get("zoning_district") or "unknown"
        district_id = normalized.get("district_id")
        citations = list(normalized.get("citations") or [])
        metadata = normalized.get("metadata")

        def upsert_standard(name: str, value: Optional[Union[float, int]], units: Optional[str]) -> None:
            if value is None or name in standards_by_type:
                return
            payload = {
                "id": f"{district}:{name}",
                "district_id": district_id,
                "standard_type": name,
                "value": value,
                "units": units,
                "citation": citations[0] if citations else None,
                "metadata": metadata,
            }
            standards_payload.append(payload)
            standards_by_type[name] = payload

        upsert_standard("min_lot_size_sqft", normalized.get("min_lot_size_sqft"), "sqft")
        upsert_standard("max_units_per_acre", normalized.get("max_units_per_acre"), "du/ac")
        upsert_standard("height_limit_ft", normalized.get("height_limit_ft"), "ft")
        upsert_standard("lot_coverage_max", normalized.get("lot_coverage_max"), None)
        upsert_standard("min_frontage_ft", normalized.get("min_frontage_ft"), "ft")
        upsert_standard("road_right_of_way_ft", normalized.get("road_right_of_way_ft"), "ft")
        upsert_standard("front_setback_ft", setbacks_payload.get("front"), "ft")
        upsert_standard("side_setback_ft", setbacks_payload.get("side"), "ft")
        upsert_standard("rear_setback_ft", setbacks_payload.get("rear"), "ft")

        if not citations:
            citations = sorted({str(item["citation"]) for item in standards_payload if item.get("citation")})
        normalized["citations"] = citations
        normalized["standards"] = standards_payload
        return normalized

    @property
    def code(self) -> str:
        """Compatibility alias for older district-centric integrations."""

        return self.district
