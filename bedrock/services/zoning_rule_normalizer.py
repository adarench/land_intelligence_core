"""Deterministic normalization of raw zoning rules into the canonical contract."""

from __future__ import annotations

import re
from typing import Any, Optional

from bedrock.contracts.base import EngineMetadata
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.zoning_rules import SetbackSet as Setbacks
from bedrock.contracts.zoning_rules import ZoningRules


ACRE_TO_SQFT = 43560.0

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "min_lot_size_sqft": (
        "min_lot_size_sqft",
        "minimum_lot_size_sqft",
        "minimum lot size",
        "minimum parcel area",
        "min lot area",
        "lot area minimum",
        "minimum lot area",
        "min parcel area",
        "min_lot_area",
        "minimum_lot_area",
        "MIN_LOTSIZ",
        "MIN_LOT_SIZE",
    ),
    "max_units_per_acre": (
        "max_units_per_acre",
        "density",
        "density limit",
        "max density",
        "maximum density",
        "max dwelling units per acre",
        "maximum dwelling units per acre",
        "dwelling units per acre",
        "units per acre",
        "du/ac",
        "DENSITY",
        "MAX_DENSITY",
        "UNIT_RATE",
    ),
    "height_limit_ft": (
        "height_limit_ft",
        "max_building_height_ft",
        "height_limit",
        "max_height",
        "maximum height",
        "building height",
        "MAX_HEIGHT",
    ),
    "min_frontage_ft": (
        "min_frontage_ft",
        "min_lot_width_ft",
        "min_frontage_ft",
        "minimum lot width",
        "minimum frontage",
        "min lot width",
        "lot width minimum",
    ),
    "lot_coverage_max": (
        "lot_coverage_max",
        "max_lot_coverage",
        "lot_coverage_max",
        "lot_coverage_limit",
        "maximum lot coverage",
        "max lot coverage",
        "lot coverage maximum",
        "MAX_LOT_CO",
    ),
    "allowed_uses": (
        "allowed_uses",
        "allowed_use_types",
        "allowed_uses",
        "permitted uses",
        "allowed uses",
    ),
}

SETBACK_ALIASES: dict[str, tuple[str, ...]] = {
    "front": (
        "front",
        "front setback",
        "front yard setback",
        "minimum front yard",
        "front_setback_ft",
        "min_front_setback_ft",
        "FRONT_YARD",
        "front_yard",
    ),
    "side": (
        "side",
        "side setback",
        "side yard setback",
        "minimum side yard",
        "side_setback_ft",
        "min_side_setback_ft",
        "SIDE_YARD",
        "side_yard",
    ),
    "rear": (
        "rear",
        "rear setback",
        "rear yard setback",
        "minimum rear yard",
        "rear_setback_ft",
        "min_rear_setback_ft",
        "BACK_YARD",
        "REAR_YARD",
        "rear_yard",
    ),
}


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def _normalized_lookup(raw_rules: dict[str, Any]) -> dict[str, Any]:
    return {_normalize_key(str(key)): value for key, value in raw_rules.items()}


def _lookup_value(raw_rules: dict[str, Any], *aliases: str) -> Any:
    normalized = _normalized_lookup(raw_rules)
    for alias in aliases:
        raw_key = alias
        if raw_key in raw_rules and raw_rules[raw_key] not in (None, ""):
            return raw_rules[raw_key]
        normalized_key = _normalize_key(alias)
        if normalized_key in normalized and normalized[normalized_key] not in (None, ""):
            return normalized[normalized_key]
    return None


def _coerce_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def _normalize_area_sqft(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    numeric = _coerce_float(text)
    if numeric is None:
        return None
    if "acre" in text or re.search(r"\bac\b", text):
        return numeric * ACRE_TO_SQFT
    return numeric


def _normalize_lot_coverage(value: Any) -> Optional[float]:
    numeric = _coerce_float(value)
    if numeric is None:
        return None
    if numeric > 1 and numeric <= 100:
        return numeric / 100.0
    return numeric


def _normalize_string_list(value: Any) -> Optional[list[str]]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
        return items or None
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or None
    return None


def _normalize_overlays(value: Any) -> list[str]:
    items = _normalize_string_list(value)
    if items is None:
        return []
    return list(dict.fromkeys(items))


def _normalize_setbacks(raw_rules: dict[str, Any]) -> dict[str, Optional[float]]:
    setbacks_input = raw_rules.get("setbacks") or {}
    if not isinstance(setbacks_input, dict):
        setbacks_input = {}

    result: dict[str, Optional[float]] = {"front": None, "side": None, "rear": None}
    for edge, aliases in SETBACK_ALIASES.items():
        nested_value = setbacks_input.get(edge)
        if nested_value not in (None, ""):
            result[edge] = _coerce_float(nested_value)
            continue
        result[edge] = _coerce_float(_lookup_value(raw_rules, *aliases))
    return result


def _clean_non_negative(value: Optional[float]) -> Optional[float]:
    if value is None or value < 0:
        return None
    return value


def normalize_rules(
    raw_rules: dict[str, Any],
    *,
    parcel: Parcel | None = None,
    parcel_id: str | None = None,
    jurisdiction: str | None = None,
    district: str | None = None,
) -> ZoningRules:
    """Normalize variant raw zoning rule payloads into canonical ZoningRules."""

    resolved_parcel_id = parcel_id or (parcel.parcel_id if parcel is not None else None)
    resolved_jurisdiction = jurisdiction or raw_rules.get("jurisdiction") or (parcel.jurisdiction if parcel is not None else None)
    resolved_district = district or _lookup_value(raw_rules, "district", "zoning_district", "code")

    if resolved_parcel_id is None:
        raise ValueError("parcel_id is required to normalize zoning rules")
    if resolved_district in (None, ""):
        raise ValueError("district is required to normalize zoning rules")

    payload = {
        "parcel_id": resolved_parcel_id,
        "jurisdiction": resolved_jurisdiction,
        "district": str(resolved_district),
        "min_lot_size_sqft": _clean_non_negative(
            _normalize_area_sqft(_lookup_value(raw_rules, *FIELD_ALIASES["min_lot_size_sqft"]))
        ),
        "max_units_per_acre": _clean_non_negative(
            _coerce_float(_lookup_value(raw_rules, *FIELD_ALIASES["max_units_per_acre"]))
        ),
        "setbacks": {
            edge: _clean_non_negative(value)
            for edge, value in _normalize_setbacks(raw_rules).items()
        },
        "height_limit_ft": _clean_non_negative(
            _coerce_float(_lookup_value(raw_rules, *FIELD_ALIASES["height_limit_ft"]))
        ),
        "min_frontage_ft": _clean_non_negative(
            _coerce_float(_lookup_value(raw_rules, *FIELD_ALIASES["min_frontage_ft"]))
        ),
        "lot_coverage_max": _clean_non_negative(
            _normalize_lot_coverage(_lookup_value(raw_rules, *FIELD_ALIASES["lot_coverage_max"]))
        ),
        "allowed_uses": _normalize_string_list(
            _lookup_value(raw_rules, *FIELD_ALIASES["allowed_uses"])
        ) or [],
        "overlays": _normalize_overlays(raw_rules.get("overlays", raw_rules.get("overlay"))),
        "metadata": EngineMetadata(
            source_engine="zoning_data_scraper",
            source_run_id=str(raw_rules.get("dataset_path") or raw_rules.get("rule_source") or "unknown"),
            source_type=raw_rules.get("source_type"),
            rule_completeness=raw_rules.get("rule_completeness"),
            legal_reliability=raw_rules.get("legal_reliability"),
        ),
    }
    return ZoningRules.model_validate(payload)


normalize_zoning_rules = normalize_rules
