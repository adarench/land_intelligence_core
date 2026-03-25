"""Translate zoning inputs into layout-ready deterministic constraints."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from bedrock.contracts.parcel import Parcel
from bedrock.contracts.validators import validate_contract
from bedrock.contracts.zoning_rules import DevelopmentStandard, ZoningRules

UsabilityClass = Literal["layout_safe", "partially_usable", "non_usable"]

_DEFAULT_ROAD_RIGHT_OF_WAY_FT = 32.0


@dataclass(frozen=True)
class TranslationIssue:
    code: str
    field: str
    message: str


@dataclass(frozen=True)
class LayoutZoningTranslationResult:
    usability_class: UsabilityClass
    zoning: ZoningRules | None
    degraded_fields: tuple[str, ...]
    issues: tuple[TranslationIssue, ...]
    additional_constraints: dict[str, float | bool]


def _standard_numeric_value(standards: list[DevelopmentStandard], standard_type: str) -> float | None:
    for item in standards:
        if item.standard_type.lower() != standard_type.lower():
            continue
        value = item.value
        if isinstance(value, bool):
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return numeric
    return None


def _first_standard_numeric_value(standards: list[DevelopmentStandard], *standard_types: str) -> float | None:
    for standard_type in standard_types:
        value = _standard_numeric_value(standards, standard_type)
        if value is not None:
            return value
    return None


def _standard_bool_value(standards: list[DevelopmentStandard], standard_type: str) -> bool | None:
    for item in standards:
        if item.standard_type.lower() != standard_type.lower():
            continue
        value = item.value
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "required"}:
            return True
        if text in {"false", "0", "no", "n", "optional"}:
            return False
        return None
    return None


def _derive_min_frontage(min_lot_size_sqft: float, front_setback_ft: float, rear_setback_ft: float, side_setback_ft: float) -> float:
    target_lot_depth_ft = max(80.0, 110.0 - front_setback_ft - rear_setback_ft)
    required_buildable_width_ft = min_lot_size_sqft / max(target_lot_depth_ft, 1.0)
    return max(35.0, required_buildable_width_ft + side_setback_ft)


def _set_required_numeric(
    payload: dict,
    standards: list[DevelopmentStandard],
    issues: list[TranslationIssue],
    degraded_fields: list[str],
    *,
    field_name: str,
    standard_type: str,
) -> None:
    direct = payload.get(field_name)
    derived = _standard_numeric_value(standards, standard_type)
    if direct is not None:
        try:
            direct = float(direct)
        except (TypeError, ValueError):
            direct = None
    if direct is not None and not math.isfinite(direct):
        direct = None

    if direct is not None and derived is not None and not math.isclose(direct, derived, rel_tol=1e-6, abs_tol=1e-6):
        issues.append(
            TranslationIssue(
                code="ambiguous_zoning_value",
                field=field_name,
                message=f"Conflicting direct and standards values: direct={direct}, standards={derived}",
            )
        )
        return

    resolved = direct if direct is not None else derived
    if resolved is None or resolved <= 0.0:
        issues.append(
            TranslationIssue(
                code="missing_required_zoning_field",
                field=field_name,
                message=f"{field_name} is required and must be > 0",
            )
        )
        return

    if direct is None and derived is not None:
        degraded_fields.append(field_name)
    payload[field_name] = float(resolved)


def _set_required_setback(
    payload: dict,
    standards: list[DevelopmentStandard],
    issues: list[TranslationIssue],
    degraded_fields: list[str],
    *,
    edge: str,
) -> None:
    setbacks = dict(payload.get("setbacks") or {})
    direct = setbacks.get(edge)
    standard_type = f"{edge}_setback_ft"
    derived = _standard_numeric_value(standards, standard_type)

    if direct is not None:
        try:
            direct = float(direct)
        except (TypeError, ValueError):
            direct = None
    if direct is not None and not math.isfinite(direct):
        direct = None

    if direct is not None and derived is not None and not math.isclose(direct, derived, rel_tol=1e-6, abs_tol=1e-6):
        issues.append(
            TranslationIssue(
                code="ambiguous_zoning_value",
                field=f"setbacks.{edge}",
                message=f"Conflicting direct and standards values: direct={direct}, standards={derived}",
            )
        )
        return

    resolved = direct if direct is not None else derived
    if resolved is None or resolved <= 0.0:
        issues.append(
            TranslationIssue(
                code="missing_required_zoning_field",
                field=f"setbacks.{edge}",
                message=f"setbacks.{edge} is required and must be > 0",
            )
        )
        return

    if direct is None and derived is not None:
        degraded_fields.append(f"setbacks.{edge}")
    setbacks[edge] = float(resolved)
    payload["setbacks"] = setbacks


def translate_zoning_for_layout(parcel: Parcel, zoning: ZoningRules | dict) -> LayoutZoningTranslationResult:
    contract = validate_contract("ZoningRules", zoning)
    payload = contract.model_dump()
    standards = list(contract.standards)
    issues: list[TranslationIssue] = []
    degraded_fields: list[str] = []
    additional_constraints: dict[str, float | bool] = {}

    if not payload.get("district"):
        issues.append(
            TranslationIssue(
                code="missing_required_zoning_field",
                field="district",
                message="district is required",
            )
        )

    if payload.get("parcel_id") != parcel.parcel_id:
        issues.append(
            TranslationIssue(
                code="zoning_parcel_mismatch",
                field="parcel_id",
                message="ZoningRules.parcel_id must match Parcel.parcel_id",
            )
        )

    _set_required_numeric(
        payload,
        standards,
        issues,
        degraded_fields,
        field_name="min_lot_size_sqft",
        standard_type="min_lot_size_sqft",
    )
    _set_required_numeric(
        payload,
        standards,
        issues,
        degraded_fields,
        field_name="max_units_per_acre",
        standard_type="max_units_per_acre",
    )
    for edge in ("front", "side", "rear"):
        _set_required_setback(
            payload,
            standards,
            issues,
            degraded_fields,
            edge=edge,
        )

    if issues:
        return LayoutZoningTranslationResult(
            usability_class="non_usable",
            zoning=None,
            degraded_fields=tuple(sorted(set(degraded_fields))),
            issues=tuple(issues),
            additional_constraints={},
        )

    setbacks = dict(payload.get("setbacks") or {})
    front = float(setbacks["front"])
    side = float(setbacks["side"])
    rear = float(setbacks["rear"])
    min_lot_size_sqft = float(payload["min_lot_size_sqft"])

    if payload.get("min_frontage_ft") is None:
        payload["min_frontage_ft"] = _derive_min_frontage(min_lot_size_sqft, front, rear, side)
        degraded_fields.append("min_frontage_ft")
        additional_constraints["derived_min_frontage_ft"] = True
    if payload.get("road_right_of_way_ft") is None:
        payload["road_right_of_way_ft"] = _DEFAULT_ROAD_RIGHT_OF_WAY_FT
        degraded_fields.append("road_right_of_way_ft")

    frontage_min_from_standard = _standard_numeric_value(standards, "frontage_min_ft")
    frontage_max_from_standard = _standard_numeric_value(standards, "frontage_max_ft")
    frontage_target_from_standard = _first_standard_numeric_value(
        standards,
        "lot_frontage_ft",
        "layout_lot_frontage_ft",
        "frontage_target_ft",
    )
    block_depth_ft = _first_standard_numeric_value(
        standards,
        "block_depth_ft",
        "lot_depth_ft",
        "layout_block_depth_ft",
    )
    if frontage_min_from_standard is not None:
        if frontage_min_from_standard <= 0.0:
            issues.append(
                TranslationIssue(
                    code="invalid_zoning_value",
                    field="frontage_min_ft",
                    message="frontage_min_ft must be > 0",
                )
            )
        else:
            additional_constraints["frontage_min_ft"] = float(frontage_min_from_standard)
    if frontage_max_from_standard is not None:
        if frontage_max_from_standard <= 0.0:
            issues.append(
                TranslationIssue(
                    code="invalid_zoning_value",
                    field="frontage_max_ft",
                    message="frontage_max_ft must be > 0",
                )
            )
        else:
            additional_constraints["frontage_max_ft"] = float(frontage_max_from_standard)
    if frontage_target_from_standard is not None:
        if frontage_target_from_standard <= 0.0:
            issues.append(
                TranslationIssue(
                    code="invalid_zoning_value",
                    field="lot_frontage_ft",
                    message="lot_frontage_ft must be > 0",
                )
            )
        else:
            additional_constraints["lot_frontage_ft"] = float(frontage_target_from_standard)

    road_access_required = _standard_bool_value(standards, "road_access_required")
    if road_access_required is None:
        road_access_required = _standard_bool_value(standards, "requires_road_access")
    if road_access_required is not None:
        additional_constraints["road_access_required"] = road_access_required

    max_block_length_ft = _standard_numeric_value(standards, "max_block_length_ft")
    if max_block_length_ft is None:
        max_block_length_ft = _standard_numeric_value(standards, "block_length_max_ft")
    if max_block_length_ft is not None:
        if max_block_length_ft <= 0.0:
            issues.append(
                TranslationIssue(
                    code="invalid_zoning_value",
                    field="max_block_length_ft",
                    message="max_block_length_ft must be > 0",
                )
            )
        else:
            additional_constraints["max_block_length_ft"] = float(max_block_length_ft)
    if block_depth_ft is not None:
        if block_depth_ft <= 0.0:
            issues.append(
                TranslationIssue(
                    code="invalid_zoning_value",
                    field="block_depth_ft",
                    message="block_depth_ft must be > 0",
                )
            )
        else:
            additional_constraints["block_depth_ft"] = float(block_depth_ft)

    easement_buffer_ft = _standard_numeric_value(standards, "easement_buffer_ft")
    if easement_buffer_ft is None:
        easement_buffer_ft = _standard_numeric_value(standards, "min_easement_buffer_ft")
    if easement_buffer_ft is not None:
        if easement_buffer_ft < 0.0:
            issues.append(
                TranslationIssue(
                    code="invalid_zoning_value",
                    field="easement_buffer_ft",
                    message="easement_buffer_ft must be >= 0",
                )
            )
        else:
            additional_constraints["easement_buffer_ft"] = float(easement_buffer_ft)

    for standard in standards:
        key = str(standard.standard_type or "").strip().lower()
        if not key.startswith("layout_"):
            continue
        value = standard.value
        if isinstance(value, bool):
            additional_constraints[key] = value
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            additional_constraints[key] = numeric

    if issues:
        return LayoutZoningTranslationResult(
            usability_class="non_usable",
            zoning=None,
            degraded_fields=tuple(sorted(set(degraded_fields))),
            issues=tuple(issues),
            additional_constraints={},
        )

    translated = ZoningRules.model_validate(payload)
    usability_class: UsabilityClass = "partially_usable" if degraded_fields else "layout_safe"
    return LayoutZoningTranslationResult(
        usability_class=usability_class,
        zoning=translated,
        degraded_fields=tuple(sorted(set(degraded_fields))),
        issues=tuple(),
        additional_constraints=additional_constraints,
    )
