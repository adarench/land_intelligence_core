"""Overlay-backed zoning service with deterministic rule normalization."""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.validators import (
    invalid_zoning_values_for_layout,
    missing_zoning_fields_for_layout,
    validate_zoning_rules_for_layout,
)
from bedrock.services.zoning_rule_normalizer import (
    Setbacks,
    ZoningRules,
    normalize_rules as normalize_zoning_rules,
)
from zoning_data_scraper.services.zoning_code_rules import canonicalize_district
from zoning_data_scraper.services.rule_normalization import normalize_zoning_rules as normalize_scraper_rules
from zoning_data_scraper.services.zoning_overlay import (
    AmbiguousJurisdictionMatchError,
    AmbiguousZoningMatchError,
    NoJurisdictionMatchError,
    NoZoningMatchError,
    candidate_dataset_reports,
    jurisdiction_has_clean_lookup_coverage,
    lookup_zoning_district,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_ROOT = WORKSPACE_ROOT / "zoning_data_scraper"
logger = logging.getLogger(__name__)
_LOOKUP_METRICS: dict[str, dict[str, int]] = defaultdict(
    lambda: {
        "success": 0,
        "no_match": 0,
        "incomplete": 0,
        "invalid": 0,
        "ambiguous": 0,
        "layout_safe": 0,
        "partially_usable": 0,
        "non_usable": 0,
        "synthetic_success": 0,
    }
)

_LAYOUT_REQUIRED_FIELDS = (
    "district",
    "min_lot_size_sqft",
    "max_units_per_acre",
    "setbacks.front",
    "setbacks.side",
    "setbacks.rear",
)
_CANONICAL_RULE_FIELDS = (
    "min_lot_size_sqft",
    "max_units_per_acre",
    "setbacks",
    "height_limit_ft",
    "min_frontage_ft",
    "road_right_of_way_ft",
    "lot_coverage_max",
    "allowed_uses",
)
_JURISDICTION_FALLBACK_DEFAULTS: dict[str, dict[str, Any]] = {
    "BenchmarkCounty_UT": {
        "min_lot_size_sqft": 5500.0,
        "max_units_per_acre": 6.0,
        "setbacks": {"front": 20.0, "side": 8.0, "rear": 15.0},
        "height_limit_ft": 35.0,
        "min_frontage_ft": 45.0,
        "road_right_of_way_ft": 40.0,
        "lot_coverage_max": 0.5,
        "allowed_uses": ["single_family"],
    },
    "Draper": {
        "min_lot_size_sqft": 13000.0,
        "max_units_per_acre": 4.0,
        "setbacks": {"front": 25.0, "side": 10.0, "rear": 20.0},
        "height_limit_ft": 35.0,
        "lot_coverage_max": 0.45,
        "allowed_uses": ["single_family_residential"],
    },
    "Lehi": {
        "min_lot_size_sqft": 22000.0,
        "max_units_per_acre": 1.98,
        "setbacks": {"front": 30.0, "side": 10.0, "rear": 25.0},
        "height_limit_ft": 35.0,
        "lot_coverage_max": 0.35,
        "allowed_uses": ["single_family_residential"],
    },
    "Salt Lake City": {
        "min_lot_size_sqft": 7000.0,
        "max_units_per_acre": 6.22,
        "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
        "height_limit_ft": 35.0,
        "lot_coverage_max": 0.45,
        "allowed_uses": ["single_family_residential"],
    },
}
_SAFE_MINIMUM_VIABLE_RULES: dict[str, Any] = {
    "min_lot_size_sqft": 5000.0,
    "max_units_per_acre": 4.0,
    "setbacks": {"front": 20.0, "side": 5.0, "rear": 15.0},
    "height_limit_ft": 30.0,
    "min_frontage_ft": 35.0,
    "road_right_of_way_ft": 32.0,
    "lot_coverage_max": 0.4,
    "allowed_uses": ["single_family_residential"],
}

_MIN_LOT_SIZE_SQFT = 500.0
_MAX_LOT_SIZE_SQFT = 2_000_000.0
_MIN_DENSITY_DU_AC = 0.1
_MAX_DENSITY_DU_AC = 80.0
_MIN_SETBACK_FT = 1.0
_MAX_SETBACK_FT = 200.0
_MIN_HEIGHT_LIMIT_FT = 8.0
_MAX_HEIGHT_LIMIT_FT = 300.0
_MIN_FRONTAGE_FT = 10.0
_MAX_FRONTAGE_FT = 400.0
_MIN_ROW_FT = 20.0
_MAX_ROW_FT = 200.0
_MIN_LOT_COVERAGE = 0.05
_MAX_LOT_COVERAGE = 1.0


class IncompleteZoningRulesError(RuntimeError):
    """Raised when zoning lookup cannot satisfy layout-critical fields."""

    def __init__(
        self,
        district: str,
        missing_fields: list[str],
        *,
        usability: str = "non_usable",
        available_fields: Optional[list[str]] = None,
        reason_codes: Optional[list[str]] = None,
        synthetic_fallback_used: bool = False,
    ) -> None:
        self.district = district
        self.missing_fields = missing_fields
        self.usability = usability
        self.available_fields = available_fields or []
        self.reason_codes = reason_codes or []
        self.synthetic_fallback_used = synthetic_fallback_used
        super().__init__("incomplete_zoning_rules")


class InvalidZoningRulesError(RuntimeError):
    """Raised when zoning payload is complete but not layout-safe."""

    def __init__(self, district: str, violations: list[str]) -> None:
        self.district = district
        self.violations = violations
        super().__init__("invalid_zoning_rules")


class ZoningLookupResult(BedrockModel):
    jurisdiction: str
    district: str
    rules: ZoningRules
    usability: str = "layout_safe"


class ZoningService:
    """Resolve parcel geometry to district and deterministic zoning rules."""

    def __init__(self, dataset_root: Optional[Path] = None, stub_data_path: Optional[Path] = None) -> None:
        self.dataset_root = Path(dataset_root or stub_data_path or DEFAULT_DATASET_ROOT)

    def lookup(self, parcel: Parcel) -> ZoningLookupResult:
        jurisdiction = parcel.jurisdiction or "unknown"
        try:
            parcel_geometry = shape(parcel.geometry)
            raw = self._resolve_raw_rules(parcel, parcel_geometry)
            normalized_raw = self._normalize_raw_input(parcel_geometry, raw)
            enriched_raw = self._apply_rule_fallbacks(normalized_raw)
            rules = normalize_zoning_rules(
                enriched_raw,
                parcel=parcel,
                jurisdiction=enriched_raw["jurisdiction"],
                district=enriched_raw["district"],
            )
            assessment = self._assess_rule_usability(enriched_raw)
            self.validate_zoning_rules(rules, parcel=parcel)
            logger.info(
                "zoning_rules_validated",
                extra={
                    "parcel_id": parcel.parcel_id,
                    "jurisdiction": rules.jurisdiction,
                    "district": rules.district,
                    "rule_source": enriched_raw.get("rule_source"),
                    "usability": assessment["usability"],
                    "validation_result": "passed",
                },
            )
            _record_lookup_metric(
                rules.jurisdiction or jurisdiction,
                "success",
                usability=assessment["usability"],
                synthetic_success=assessment["synthetic_fallback_used"],
            )
            return ZoningLookupResult(
                jurisdiction=enriched_raw["jurisdiction"],
                district=rules.district,
                rules=rules,
                usability=assessment["usability"],
            )
        except (NoJurisdictionMatchError, NoZoningMatchError):
            _record_lookup_metric(jurisdiction, "no_match", usability="non_usable")
            raise
        except (AmbiguousJurisdictionMatchError, AmbiguousZoningMatchError):
            _record_lookup_metric(jurisdiction, "ambiguous")
            raise
        except IncompleteZoningRulesError as exc:
            _record_lookup_metric(jurisdiction, "incomplete", usability=exc.usability)
            raise
        except InvalidZoningRulesError:
            _record_lookup_metric(jurisdiction, "invalid", usability="layout_safe")
            raise

    def validate_zoning_rules(self, rules: ZoningRules, *, parcel: Optional[Parcel] = None) -> ZoningRules:
        return validate_zoning_rules(rules, parcel=parcel)

    def _resolve_raw_rules(self, parcel: Parcel, parcel_geometry: BaseGeometry) -> dict[str, Any]:
        try:
            match = lookup_zoning_district(
                parcel_geometry,
                parcel_jurisdiction=parcel.jurisdiction,
                dataset_root=self.dataset_root,
            )
            raw = normalize_scraper_rules(match)
            logger.info(
                "zoning_district_identified",
                extra={
                    "parcel_id": parcel.parcel_id,
                    "jurisdiction": raw["jurisdiction"],
                    "district": raw["district"],
                    "source_layer": raw.get("source_layer"),
                    "dataset_path": raw.get("dataset_path"),
                },
            )
            return raw
        except (NoJurisdictionMatchError, NoZoningMatchError, AmbiguousJurisdictionMatchError, AmbiguousZoningMatchError):
            if self._jurisdiction_has_artifact_only_sources(parcel.jurisdiction):
                raise IncompleteZoningRulesError(parcel.jurisdiction or "unknown", list(_LAYOUT_REQUIRED_FIELDS))
            raise

    def _apply_rule_fallbacks(self, raw: dict[str, Any]) -> dict[str, Any]:
        payload = self._sanitize_rule_values(raw)
        if self._jurisdiction_uses_real_data_path(payload.get("jurisdiction")):
            for key in (
                "district",
                "min_lot_size_sqft",
                "max_units_per_acre",
                "height_limit_ft",
                "min_frontage_ft",
                "road_right_of_way_ft",
                "lot_coverage_max",
                "allowed_uses",
            ):
                payload.setdefault(key, None)
            return payload
        jurisdiction_defaults = self._sanitize_rule_values(
            {
                "setbacks": dict(self._jurisdiction_fallback_defaults(payload.get("jurisdiction")).get("setbacks") or {}),
                "min_lot_size_sqft": self._jurisdiction_fallback_defaults(payload.get("jurisdiction")).get("min_lot_size_sqft"),
                "max_units_per_acre": self._jurisdiction_fallback_defaults(payload.get("jurisdiction")).get("max_units_per_acre"),
                "height_limit_ft": self._jurisdiction_fallback_defaults(payload.get("jurisdiction")).get("height_limit_ft"),
                "lot_coverage_max": self._jurisdiction_fallback_defaults(payload.get("jurisdiction")).get("lot_coverage_max"),
            }
        )
        safe_minimum_defaults = self._sanitize_rule_values(_SAFE_MINIMUM_VIABLE_RULES)
        used_jurisdiction_fallback = False
        used_safe_minimum = False

        def _select_fallback_value(primary: Any, secondary: Any) -> tuple[Any, bool, bool]:
            if primary is not None:
                return primary, True, False
            if secondary is not None:
                return secondary, False, True
            return None, False, False

        setbacks = dict(payload.get("setbacks") or {})
        for edge in ("front", "side", "rear"):
            if setbacks.get(edge) is None:
                fallback_value, used_jurisdiction, used_safe = _select_fallback_value(
                    jurisdiction_defaults.get("setbacks", {}).get(edge),
                    safe_minimum_defaults.get("setbacks", {}).get(edge),
                )
                setbacks[edge] = fallback_value
                used_jurisdiction_fallback = used_jurisdiction_fallback or used_jurisdiction
                used_safe_minimum = used_safe_minimum or used_safe
        payload["setbacks"] = setbacks

        for field_name in (
            "min_lot_size_sqft",
            "max_units_per_acre",
            "height_limit_ft",
            "lot_coverage_max",
        ):
            if payload.get(field_name) is None:
                fallback_value, used_jurisdiction, used_safe = _select_fallback_value(
                    jurisdiction_defaults.get(field_name),
                    safe_minimum_defaults.get(field_name),
                )
                payload[field_name] = fallback_value
                used_jurisdiction_fallback = used_jurisdiction_fallback or used_jurisdiction
                used_safe_minimum = used_safe_minimum or used_safe

        for key in (
            "district",
            "min_lot_size_sqft",
            "max_units_per_acre",
            "height_limit_ft",
            "min_frontage_ft",
            "road_right_of_way_ft",
            "lot_coverage_max",
            "allowed_uses",
        ):
            payload.setdefault(key, None)
        if used_safe_minimum:
            payload["rule_source"] = "safe_minimum_viable"
        elif used_jurisdiction_fallback:
            payload["rule_source"] = "jurisdiction_fallback"
        return payload

    @staticmethod
    def _jurisdiction_fallback_defaults(jurisdiction: Optional[str]) -> dict[str, Any]:
        if not jurisdiction:
            return {}
        return dict(_JURISDICTION_FALLBACK_DEFAULTS.get(jurisdiction, {}))

    def _jurisdiction_uses_real_data_path(self, jurisdiction: Optional[str]) -> bool:
        if not jurisdiction:
            return False
        return jurisdiction_has_clean_lookup_coverage(jurisdiction, dataset_root=self.dataset_root)

    def _canonicalize_district(self, parcel_geometry: BaseGeometry, jurisdiction: str, district: str) -> str:
        raw = (district or "").strip()
        if raw:
            resolved = canonicalize_district(jurisdiction, raw)
            if resolved:
                raw = resolved
        if self._is_canonical_district_code(raw):
            return self._normalize_district_code(raw)
        centroid = parcel_geometry.centroid if not isinstance(parcel_geometry, Point) else parcel_geometry
        jurisdiction_slug = re.sub(r"[^a-z0-9]+", "-", (jurisdiction or "unknown").strip().lower()).strip("-")
        return f"UNMAPPED-{jurisdiction_slug.upper()}-{round(centroid.x, 6)}_{round(centroid.y, 6)}"

    @staticmethod
    def _is_canonical_district_code(value: str) -> bool:
        if not value:
            return False
        return any(char.isalpha() for char in value)

    @staticmethod
    def _normalize_district_code(value: str) -> str:
        cleaned = re.sub(r"\s+", "", value.strip().upper())
        return cleaned

    def _normalize_raw_input(self, parcel_geometry: BaseGeometry, raw: dict[str, Any]) -> dict[str, Any]:
        payload = dict(raw)
        district = self._canonicalize_district(parcel_geometry, str(payload.get("jurisdiction") or ""), str(payload.get("district") or ""))
        payload["district"] = district
        return payload

    @staticmethod
    def _assess_rule_usability(raw: dict[str, Any]) -> dict[str, Any]:
        available_fields = []
        missing_fields = []
        if raw.get("min_lot_size_sqft") is not None:
            available_fields.append("min_lot_size_sqft")
        else:
            missing_fields.append("min_lot_size_sqft")
        if raw.get("max_units_per_acre") is not None:
            available_fields.append("max_units_per_acre")
        else:
            missing_fields.append("max_units_per_acre")
        setbacks = dict(raw.get("setbacks") or {})
        for edge in ("front", "side", "rear"):
            if setbacks.get(edge) is not None:
                available_fields.append(f"setbacks.{edge}")
            else:
                missing_fields.append(f"setbacks.{edge}")
        for optional_field in ("height_limit_ft", "lot_coverage_max", "min_frontage_ft", "road_right_of_way_ft", "allowed_uses"):
            value = raw.get(optional_field)
            if value not in (None, [], ""):
                available_fields.append(optional_field)
        reason_codes = list(raw.get("usability_reason_codes") or [])
        synthetic_fallback_used = bool(
            raw.get("source_layer") == "precomputed_district_index"
            or raw.get("rule_source") in {"jurisdiction_fallback", "safe_minimum_viable"}
        )
        inferred_usability = "layout_safe" if not missing_fields else ("partially_usable" if available_fields or reason_codes else "non_usable")
        usability = str(raw.get("usability_class") or inferred_usability)
        if synthetic_fallback_used and usability == "layout_safe":
            reason_codes = list(dict.fromkeys([*reason_codes, "synthetic_fallback_used"]))
        return {
            "usability": usability,
            "available_fields": available_fields,
            "missing_fields": missing_fields,
            "reason_codes": reason_codes,
            "synthetic_fallback_used": synthetic_fallback_used,
        }

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            numeric = float(value)
            if math.isfinite(numeric):
                return numeric
            return None
        text = str(value).strip()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        numeric = float(match.group())
        if not math.isfinite(numeric):
            return None
        return numeric

    @classmethod
    def _bounded(
        cls,
        value: Any,
        *,
        minimum: float,
        maximum: Optional[float] = None,
    ) -> Optional[float]:
        numeric = cls._coerce_float(value)
        if numeric is None:
            return None
        if numeric < minimum:
            return None
        if maximum is not None and numeric > maximum:
            return None
        return numeric

    @staticmethod
    def _normalize_allowed_uses(value: Any) -> Optional[list[str]]:
        if value is None:
            return None
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
            return items or None
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return items or None
        return None

    @classmethod
    def _sanitize_rule_values(cls, raw: dict[str, Any]) -> dict[str, Any]:
        payload = {field: raw.get(field) for field in raw.keys()}
        setbacks = dict(payload.get("setbacks") or {})
        payload["setbacks"] = {
            "front": cls._bounded(
                setbacks.get("front", payload.get("front_setback_ft")),
                minimum=_MIN_SETBACK_FT,
                maximum=_MAX_SETBACK_FT,
            ),
            "side": cls._bounded(
                setbacks.get("side", payload.get("side_setback_ft")),
                minimum=_MIN_SETBACK_FT,
                maximum=_MAX_SETBACK_FT,
            ),
            "rear": cls._bounded(
                setbacks.get("rear", payload.get("rear_setback_ft")),
                minimum=_MIN_SETBACK_FT,
                maximum=_MAX_SETBACK_FT,
            ),
        }
        payload["min_lot_size_sqft"] = cls._bounded(
            payload.get("min_lot_size_sqft"),
            minimum=_MIN_LOT_SIZE_SQFT,
            maximum=_MAX_LOT_SIZE_SQFT,
        )
        payload["max_units_per_acre"] = cls._bounded(
            payload.get("max_units_per_acre"),
            minimum=_MIN_DENSITY_DU_AC,
            maximum=_MAX_DENSITY_DU_AC,
        )
        payload["height_limit_ft"] = cls._bounded(
            payload.get("height_limit_ft") or payload.get("max_building_height_ft") or payload.get("height_limit"),
            minimum=_MIN_HEIGHT_LIMIT_FT,
            maximum=_MAX_HEIGHT_LIMIT_FT,
        )
        payload["lot_coverage_max"] = cls._bounded(
            payload.get("lot_coverage_max") or payload.get("max_lot_coverage") or payload.get("lot_coverage_limit"),
            minimum=_MIN_LOT_COVERAGE,
            maximum=_MAX_LOT_COVERAGE,
        )
        payload["min_frontage_ft"] = cls._bounded(
            payload.get("min_frontage_ft") or payload.get("min_lot_width_ft"),
            minimum=_MIN_FRONTAGE_FT,
            maximum=_MAX_FRONTAGE_FT,
        )
        payload["road_right_of_way_ft"] = cls._bounded(
            payload.get("road_right_of_way_ft"),
            minimum=_MIN_ROW_FT,
            maximum=_MAX_ROW_FT,
        )
        payload["allowed_uses"] = cls._normalize_allowed_uses(
            payload.get("allowed_uses") or payload.get("allowed_use_types")
        )
        return payload

    def _jurisdiction_has_artifact_only_sources(self, jurisdiction: Optional[str]) -> bool:
        if not jurisdiction:
            return False
        reports = candidate_dataset_reports(jurisdiction, dataset_root=self.dataset_root)
        if not reports:
            return False
        return all(not report.has_clean_zoning_features for report in reports)


def _bounded_violation(
    value: Optional[float],
    *,
    minimum: float,
    maximum: Optional[float] = None,
    field_name: str,
) -> Optional[str]:
    if value is None:
        return None
    if value < minimum:
        return f"{field_name} must be >= {minimum}"
    if maximum is not None and value > maximum:
        return f"{field_name} must be <= {maximum}"
    return None


def validate_zoning_rules(
    zoning: ZoningRules,
    *,
    parcel: Optional[Parcel] = None,
) -> ZoningRules:
    """Enforce strict, deterministic layout-safe zoning output."""

    try:
        contract = validate_zoning_rules_for_layout(zoning)
    except ValueError:
        missing_fields = missing_zoning_fields_for_layout(zoning)
        if missing_fields:
            district = zoning.district if isinstance(zoning, ZoningRules) else str(zoning.get("district") or "unknown")
            raise IncompleteZoningRulesError(district or "unknown", missing_fields)
        invalid_fields = invalid_zoning_values_for_layout(zoning)
        if invalid_fields:
            district = zoning.district if isinstance(zoning, ZoningRules) else str(zoning.get("district") or "unknown")
            raise InvalidZoningRulesError(district or "unknown", invalid_fields)
        raise

    missing_fields = missing_zoning_fields_for_layout(contract)
    if missing_fields:
        raise IncompleteZoningRulesError(contract.district or "unknown", missing_fields)

    violations: list[str] = []
    if not ZoningService._is_canonical_district_code(contract.district):
        violations.append("district must include alpha code and be canonicalized")

    for field_name, value, minimum, maximum in (
        ("min_lot_size_sqft", contract.min_lot_size_sqft, _MIN_LOT_SIZE_SQFT, _MAX_LOT_SIZE_SQFT),
        ("max_units_per_acre", contract.max_units_per_acre, _MIN_DENSITY_DU_AC, _MAX_DENSITY_DU_AC),
        ("setbacks.front", contract.setbacks.front, _MIN_SETBACK_FT, _MAX_SETBACK_FT),
        ("setbacks.side", contract.setbacks.side, _MIN_SETBACK_FT, _MAX_SETBACK_FT),
        ("setbacks.rear", contract.setbacks.rear, _MIN_SETBACK_FT, _MAX_SETBACK_FT),
    ):
        violation = _bounded_violation(
            value,
            minimum=minimum,
            maximum=maximum,
            field_name=field_name,
        )
        if violation:
            violations.append(violation)

    if parcel is not None:
        parcel_area_sqft = float(parcel.area_sqft)
        min_lot_size_sqft = float(contract.min_lot_size_sqft or 0.0)
        max_units_per_acre = float(contract.max_units_per_acre or 0.0)
        if parcel_area_sqft < min_lot_size_sqft:
            violations.append(
                f"parcel.area_sqft ({parcel_area_sqft}) is smaller than min_lot_size_sqft ({min_lot_size_sqft})"
            )
        max_units_by_density = math.floor((parcel_area_sqft / 43560.0) * max_units_per_acre)
        if max_units_by_density <= 0:
            violations.append("density constraint yields zero buildable units for parcel area")

    if violations:
        raise InvalidZoningRulesError(contract.district, violations)
    return contract


def _record_lookup_metric(
    jurisdiction: str,
    outcome: str,
    *,
    usability: Optional[str] = None,
    synthetic_success: bool = False,
) -> None:
    bucket = _LOOKUP_METRICS[jurisdiction or "unknown"]
    bucket[outcome] = bucket.get(outcome, 0) + 1
    if usability in {"layout_safe", "partially_usable", "non_usable"}:
        bucket[usability] = bucket.get(usability, 0) + 1
    if synthetic_success:
        bucket["synthetic_success"] = bucket.get("synthetic_success", 0) + 1


def reset_zoning_lookup_metrics() -> None:
    _LOOKUP_METRICS.clear()


def snapshot_zoning_lookup_metrics() -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for jurisdiction, counts in sorted(_LOOKUP_METRICS.items()):
        total = float(sum(counts.values()))
        success = float(counts.get("success", 0))
        summary[jurisdiction] = {
            "success": success,
            "no_match": float(counts.get("no_match", 0)),
            "incomplete": float(counts.get("incomplete", 0)),
            "invalid": float(counts.get("invalid", 0)),
            "ambiguous": float(counts.get("ambiguous", 0)),
            "layout_safe": float(counts.get("layout_safe", 0)),
            "partially_usable": float(counts.get("partially_usable", 0)),
            "non_usable": float(counts.get("non_usable", 0)),
            "synthetic_success": float(counts.get("synthetic_success", 0)),
            "total": total,
            "success_rate": (success / total) if total else 0.0,
        }
    return summary
