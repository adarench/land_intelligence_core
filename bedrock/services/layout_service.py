"""Canonical layout service wrapping the existing GIS layout engine."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import LineString, MultiLineString, Polygon, shape
from shapely.geometry.base import BaseGeometry

from bedrock.contracts.base import EngineMetadata, Geometry
from bedrock.contracts.layout_candidate_batch import LayoutCandidateBatch, LayoutSearchPlan
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.layout_result import LayoutResult
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.zoning_rules import DevelopmentStandard, ZoningRules
from bedrock.services.zoning_layout_translation import translate_zoning_for_layout

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
GIS_ROOT = WORKSPACE_ROOT / "GIS_lot_layout_optimizer"
GIS_LAYOUT_ENGINE_ROOT = GIS_ROOT / "apps" / "python-api" / "services" / "layout_engine"
GIS_LAYOUT_ENGINE_PACKAGE = "gis_layout_runtime"

if GIS_LAYOUT_ENGINE_PACKAGE not in sys.modules:
    package = types.ModuleType(GIS_LAYOUT_ENGINE_PACKAGE)
    package.__path__ = [str(GIS_LAYOUT_ENGINE_ROOT)]  # type: ignore[attr-defined]
    sys.modules[GIS_LAYOUT_ENGINE_PACKAGE] = package

run_layout_search = importlib.import_module(f"{GIS_LAYOUT_ENGINE_PACKAGE}.layout_search").run_layout_search

PRODUCTION_STRATEGIES = ("grid", "spine-road", "cul-de-sac", "herringbone", "t_junction", "loop_custom")
DEFAULT_RUNTIME_BUDGET_SECONDS = 55.0
MIN_FEASIBLE_LOT_DEPTH_FT = 40.0
CANONICAL_PRECISION = 6
MAX_CANDIDATE_CAP = 48
MAX_REPAIR_AREA_CHANGE_RATIO = 0.01
MIN_REPAIR_DISTANCE = 1e-6
SIMPLIFICATION_TOLERANCE_RATIO = 0.0005
MAX_SIMPLIFICATION_AREA_CHANGE_RATIO = 0.005


class LayoutSearchError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


class LayoutConstraintViolationError(RuntimeError):
    """Raised when a candidate violates deterministic zoning constraints."""


@dataclass(frozen=True)
class SolverConstraints:
    zoning_rules: dict
    min_lot_area_sqft: float
    max_units: int
    min_frontage_ft: float | None
    max_frontage_ft: float | None
    required_buildable_width_ft: float
    side_setback_ft: float
    max_buildable_depth_ft: float
    setbacks: dict[str, float | None]
    road_right_of_way_ft: float | None
    road_access_required: bool
    max_block_length_ft: float | None
    easement_buffer_ft: float | None
    additional_zoning_constraints: dict[str, float | bool]


@dataclass(frozen=True)
class SearchHeuristics:
    road_width_ft: float
    target_lot_depth_ft: float
    frontage_hint_ft: float
    strategies: tuple[str, ...]
    runtime_budget_seconds: float


@dataclass(frozen=True)
class SearchAttemptProfile:
    label: str
    strategies: tuple[str, ...]
    max_candidates: int
    lot_depth_factor: float = 1.0
    frontage_hint_factor: float = 1.0
    road_width_factor: float = 1.0
    runtime_budget_factor: float = 1.0


def _record_partial_candidate(
    debug_metrics: dict[str, object],
    candidate,
    *,
    violation: str,
    strategy: str,
    profile_label: str | None = None,
) -> None:
    result = getattr(candidate, "result", None)
    metrics = getattr(result, "metrics", {}) if result is not None else {}
    summary = {
        "score": round(float(getattr(candidate, "score", 0.0) or 0.0), CANONICAL_PRECISION),
        "violation": violation,
        "strategy": strategy,
        "profile": profile_label,
        "lot_count": int(metrics.get("lot_count", 0) or 0),
        "avg_frontage_ft": round(float(metrics.get("avg_frontage_ft", 0.0) or 0.0), CANONICAL_PRECISION),
        "avg_depth_ft": round(float(metrics.get("avg_depth_ft", 0.0) or 0.0), CANONICAL_PRECISION),
        "total_road_ft": round(float(metrics.get("total_road_ft", 0.0) or 0.0), CANONICAL_PRECISION),
        "compliance_rate": round(float(metrics.get("compliance_rate", 0.0) or 0.0), CANONICAL_PRECISION),
    }
    partials = list(debug_metrics.get("partial_candidates", []))
    partials.append(summary)
    partials.sort(
        key=lambda item: (
            -float(item.get("score", 0.0) or 0.0),
            -int(item.get("lot_count", 0) or 0),
            float(item.get("total_road_ft", 0.0) or 0.0),
        )
    )
    debug_metrics["partial_candidates"] = partials[:5]


def _geometry_to_local_feet(geometry: Geometry) -> tuple[Polygon, dict[str, float]]:
    geom = shape(geometry)
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda item: item.area)
    if geom.geom_type != "Polygon":
        raise ValueError("Parcel.geometry must be a Polygon or MultiPolygon")

    minx, miny, maxx, maxy = geom.bounds
    is_geographic = minx >= -180 and maxx <= 180 and miny >= -90 and maxy <= 90
    if not is_geographic:
        return geom, {"origin_lng": 0.0, "origin_lat": 0.0, "feet_per_degree_lng": 1.0, "feet_per_degree_lat": 1.0}

    centroid = geom.centroid
    feet_per_degree_lat = 364000.0
    feet_per_degree_lng = max(feet_per_degree_lat * math.cos(math.radians(centroid.y)), 1.0)
    coords = [
        (
            (lng - centroid.x) * feet_per_degree_lng,
            (lat - centroid.y) * feet_per_degree_lat,
        )
        for lng, lat in geom.exterior.coords
    ]
    return Polygon(coords), {
        "origin_lng": centroid.x,
        "origin_lat": centroid.y,
        "feet_per_degree_lng": feet_per_degree_lng,
        "feet_per_degree_lat": feet_per_degree_lat,
    }


def _to_geojson_coords(coords, projection: dict[str, float]) -> list[list[float]]:
    if projection["feet_per_degree_lng"] == 1.0 and projection["feet_per_degree_lat"] == 1.0:
        return [[float(x), float(y)] for x, y in coords]
    return [
        [
            projection["origin_lng"] + (float(x) / projection["feet_per_degree_lng"]),
            projection["origin_lat"] + (float(y) / projection["feet_per_degree_lat"]),
        ]
        for x, y in coords
    ]


def _derive_min_frontage(min_lot_size_sqft: float, lot_depth_ft: float, side_setback_ft: float) -> float:
    required_buildable_width = min_lot_size_sqft / max(lot_depth_ft, 1.0)
    return max(35.0, required_buildable_width + side_setback_ft)


def _standard_numeric_value(standards: list[DevelopmentStandard], standard_type: str) -> float | None:
    for item in standards:
        if item.standard_type.lower() != standard_type.lower():
            continue
        value = item.value
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _setback_value(zoning: ZoningRules, field_name: str) -> float:
    setbacks = zoning.setbacks
    if isinstance(setbacks, dict):
        direct_value = setbacks.get(field_name)
    else:
        direct_value = getattr(setbacks, field_name, None)
    standards_value = _standard_numeric_value(zoning.standards, f"{field_name}_setback_ft")
    if direct_value is not None and standards_value is not None and not math.isclose(
        float(direct_value),
        float(standards_value),
        rel_tol=1e-6,
        abs_tol=1e-6,
    ):
        raise ValueError(
            f"Ambiguous ZoningRules value for setbacks.{field_name}: direct={direct_value}, standards={standards_value}"
        )
    value = direct_value if direct_value is not None else standards_value
    if value is None:
        raise ValueError(f"ZoningRules.setbacks.{field_name} is required for layout search")
    return float(value)


def _required_zoning_value(zoning: ZoningRules, field_name: str) -> float:
    direct_value = getattr(zoning, field_name, None)
    derived = _standard_numeric_value(zoning.standards, field_name)
    if direct_value is not None and derived is not None and not math.isclose(
        float(direct_value),
        float(derived),
        rel_tol=1e-6,
        abs_tol=1e-6,
    ):
        raise ValueError(f"Ambiguous ZoningRules value for {field_name}: direct={direct_value}, standards={derived}")
    if direct_value is not None:
        return float(direct_value)
    if derived is not None:
        return derived

    raise ValueError(f"ZoningRules.{field_name} is required for layout search")


def _stable_layout_id(parcel: Parcel, candidate) -> str:
    raise NotImplementedError("_stable_layout_id no longer accepts raw candidates")


def _normalize_candidate(parcel: Parcel, candidate, debug_metrics: dict | None = None) -> LayoutResult:
    geojson = getattr(candidate, "geojson", {})
    features = geojson.get("features", []) if isinstance(geojson, dict) else []
    if not isinstance(features, list):
        raise LayoutConstraintViolationError(f"Layout candidate returned invalid feature payload for parcel {parcel.parcel_id}")

    lot_geometries: list[Geometry] = []
    road_geometries: list[Geometry] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or "type" not in geometry:
            continue
        layer = str(feature.get("properties", {}).get("layer", "")).strip().lower()
        normalized_geometry = _normalize_output_geometry(geometry, debug_metrics=debug_metrics)
        if normalized_geometry is None:
            continue
        if layer in {"lot", "lots"}:
            lot_geometries.append(normalized_geometry)
        elif layer in {"road", "roads", "street", "streets"}:
            road_geometries.append(normalized_geometry)

    lot_geometries.sort(key=_lot_geometry_sort_key)
    road_geometries.sort(key=_road_geometry_sort_key)

    metrics = getattr(candidate.result, "metrics", {})
    metric_unit_count = int(metrics.get("lot_count", len(lot_geometries)))
    if metric_unit_count < 0:
        raise LayoutConstraintViolationError(f"Layout candidate returned negative lot_count for parcel {parcel.parcel_id}")
    if metric_unit_count > 0 and not lot_geometries:
        raise LayoutConstraintViolationError(f"Layout candidate omitted lot geometries for parcel {parcel.parcel_id}")
    unit_count = len(lot_geometries)

    road_length = round(float(metrics.get("total_road_ft", 0.0)), CANONICAL_PRECISION)
    if not math.isfinite(road_length) or road_length < 0.0:
        raise LayoutConstraintViolationError(f"Layout candidate returned invalid road length for parcel {parcel.parcel_id}")
    score = round(float(candidate.score), CANONICAL_PRECISION)
    if not math.isfinite(score):
        raise LayoutConstraintViolationError(f"Layout candidate returned invalid score for parcel {parcel.parcel_id}")

    layout = LayoutResult(
        layout_id="",
        parcel_id=parcel.parcel_id,
        unit_count=unit_count,
        lot_geometries=lot_geometries,
        road_geometries=road_geometries,
        road_length_ft=road_length,
        open_space_area_sqft=0.0,
        utility_length_ft=0.0,
        score=score,
        metadata=None,
    )
    return layout.model_copy(update={"layout_id": _stable_layout_id_from_layout(parcel, layout)})


def _round_coord(value: float) -> float:
    return round(float(value), CANONICAL_PRECISION)


def _stable_layout_id_from_layout(parcel: Parcel, layout: LayoutResult) -> str:
    digest_payload = {
        "parcel_id": str(parcel.parcel_id),
        "unit_count": int(layout.unit_count),
        "road_length_ft": round(float(layout.road_length_ft), CANONICAL_PRECISION),
        "score": round(float(layout.score or 0.0), CANONICAL_PRECISION),
        "lot_geometries": layout.lot_geometries,
        "road_geometries": layout.road_geometries,
    }
    digest = hashlib.sha1(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:12]
    return f"layout-{parcel.parcel_id}-{digest}"


def _repair_geometry_with_limits(geometry: BaseGeometry) -> tuple[BaseGeometry | None, str | None]:
    if geometry.is_empty:
        return None, None
    if geometry.is_valid:
        return geometry, None
    try:
        repaired = geometry.buffer(0)
    except Exception:
        return None, None
    if repaired.is_empty or not repaired.is_valid:
        return None, None
    original_area = max(float(getattr(geometry, "area", 0.0) or 0.0), 0.0)
    repaired_area = max(float(getattr(repaired, "area", 0.0) or 0.0), 0.0)
    if original_area > 0.0:
        area_change_ratio = abs(repaired_area - original_area) / original_area
        if area_change_ratio > MAX_REPAIR_AREA_CHANGE_RATIO:
            return None, None
    try:
        diagonal = math.hypot(geometry.bounds[2] - geometry.bounds[0], geometry.bounds[3] - geometry.bounds[1])
        max_displacement = max(MIN_REPAIR_DISTANCE, diagonal * 0.01)
        if geometry.hausdorff_distance(repaired) > max_displacement:
            return None, None
    except Exception:
        return None, None
    return repaired, "buffer0"


def _simplify_geometry_with_limits(geometry: BaseGeometry) -> tuple[BaseGeometry | None, str | None]:
    if geometry.is_empty:
        return None, None
    diagonal = math.hypot(geometry.bounds[2] - geometry.bounds[0], geometry.bounds[3] - geometry.bounds[1])
    tolerance = max(MIN_REPAIR_DISTANCE, diagonal * SIMPLIFICATION_TOLERANCE_RATIO)
    try:
        simplified = geometry.simplify(tolerance, preserve_topology=True)
    except Exception:
        return None, None
    if simplified.is_empty or not simplified.is_valid:
        return None, None
    original_area = max(float(getattr(geometry, "area", 0.0) or 0.0), 0.0)
    simplified_area = max(float(getattr(simplified, "area", 0.0) or 0.0), 0.0)
    if original_area > 0.0:
        area_change_ratio = abs(simplified_area - original_area) / original_area
        if area_change_ratio > MAX_SIMPLIFICATION_AREA_CHANGE_RATIO:
            return None, None
    return simplified, "simplify"


def _preprocess_parcel_polygon(parcel_polygon: Polygon, debug_metrics: dict | None = None) -> Polygon:
    processed: BaseGeometry = parcel_polygon
    if not processed.is_valid:
        repaired, repair_type = _repair_geometry_with_limits(processed)
        if repaired is None:
            raise LayoutSearchError(
                "invalid_geometry",
                "Parcel geometry could not be repaired for layout search",
                details={"reason_category": "GEOMETRY_INVALID"},
            )
        processed = repaired
        if debug_metrics is not None and repair_type is not None:
            debug_metrics["parcel_preprocessing"] = list(debug_metrics.get("parcel_preprocessing", [])) + [repair_type]
    simplified, simplify_type = _simplify_geometry_with_limits(processed)
    if simplified is not None:
        processed = simplified
        if debug_metrics is not None and simplify_type is not None:
            debug_metrics["parcel_preprocessing"] = list(debug_metrics.get("parcel_preprocessing", [])) + [simplify_type]
    if processed.geom_type == "MultiPolygon":
        processed = max(processed.geoms, key=lambda item: item.area)
    if processed.geom_type != "Polygon" or processed.is_empty or not processed.is_valid:
        raise LayoutSearchError(
            "invalid_geometry",
            "Parcel geometry is not solver-compatible after preprocessing",
            details={"reason_category": "GEOMETRY_INVALID"},
        )
    return processed


def _normalize_output_geometry(geometry: Geometry, debug_metrics: dict | None = None) -> Geometry | None:
    try:
        geom = shape(geometry)
    except Exception:
        return None
    if geom.is_empty:
        return None
    if not geom.is_valid:
        repaired, repair_type = _repair_geometry_with_limits(geom)
        if repaired is None:
            return None
        geom = repaired
        if debug_metrics is not None and repair_type is not None:
            debug_metrics["repair_events"] = debug_metrics.get("repair_events", []) + [repair_type]
    if geom.is_empty:
        return None
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda item: item.area)
    if geom.geom_type == "MultiLineString":
        segments = [segment for segment in geom.geoms if segment.length > 1e-6]
        if not segments:
            return None
        geom = MultiLineString(segments)
    if geom.geom_type == "Polygon":
        coords = [[[_round_coord(x), _round_coord(y)] for x, y in geom.exterior.coords]]
        return {"type": "Polygon", "coordinates": coords}
    if geom.geom_type == "LineString":
        coords = [[_round_coord(x), _round_coord(y)] for x, y in geom.coords]
        return {"type": "LineString", "coordinates": coords}
    if geom.geom_type == "MultiLineString":
        coords = [
            [[_round_coord(x), _round_coord(y)] for x, y in segment.coords]
            for segment in geom.geoms
        ]
        return {"type": "MultiLineString", "coordinates": coords}
    return None


def _geometry_dict_sort_key(geometry: Geometry) -> tuple:
    payload = json.dumps(geometry, sort_keys=True, separators=(",", ":"))
    return (geometry.get("type", ""), payload)


def _geometry_centroid(geometry: Geometry) -> tuple[float, float]:
    geom = shape(geometry)
    centroid = geom.centroid
    return (_round_coord(centroid.x), _round_coord(centroid.y))


def _lot_geometry_sort_key(geometry: Geometry) -> tuple:
    centroid_x, centroid_y = _geometry_centroid(geometry)
    return (centroid_x, centroid_y, _geometry_dict_sort_key(geometry))


def _road_geometry_sort_key(geometry: Geometry) -> tuple:
    geom = shape(geometry)
    bounds = tuple(_round_coord(value) for value in geom.bounds)
    return (bounds, _geometry_dict_sort_key(geometry))


def _max_units(parcel_area_sqft: float, zoning: ZoningRules, min_lot_size_sqft: float) -> int:
    density_limit = getattr(zoning, "max_units_per_acre", None)
    if density_limit is None:
        density_limit = _standard_numeric_value(zoning.standards, "max_units_per_acre")
    by_lot_size = max(0, math.floor(parcel_area_sqft / max(min_lot_size_sqft, 1.0)))
    if density_limit is None:
        return by_lot_size
    density_limit = float(density_limit)
    if density_limit <= 0.0:
        return 0
    by_density = max(0, math.floor((parcel_area_sqft / 43560.0) * density_limit))
    return min(by_density, by_lot_size)


def _approx_max_frontage_ft(parcel_polygon: Polygon) -> float:
    lengths = []
    coords = list(parcel_polygon.exterior.coords)
    for index in range(len(coords) - 1):
        x1, y1 = coords[index]
        x2, y2 = coords[index + 1]
        lengths.append(math.hypot(x2 - x1, y2 - y1))
    minx, miny, maxx, maxy = parcel_polygon.bounds
    lengths.extend([maxx - minx, maxy - miny])
    return max(lengths or [0.0])


def _classified_layout_failure(
    *,
    parcel: Parcel,
    parcel_polygon: Polygon,
    constraints: SolverConstraints,
    debug_metrics: dict[str, object],
) -> LayoutSearchError:
    parcel_area_sqft = float(parcel.area or parcel_polygon.area)
    frontage_requirement = max(
        float(constraints.min_frontage_ft or 0.0),
        float(constraints.required_buildable_width_ft + (2.0 * constraints.side_setback_ft)),
    )
    approx_frontage_ft = _approx_max_frontage_ft(parcel_polygon)
    rejected_invalid = int(debug_metrics.get("candidates_rejected_invalid_geometry", 0))
    rejected_connectivity = int(debug_metrics.get("candidates_rejected_connectivity", 0))
    generated = int(debug_metrics.get("total_candidates_generated", 0))

    details = {
        "parcel_id": parcel.parcel_id,
        "parcel_area_sqft": round(parcel_area_sqft, CANONICAL_PRECISION),
        "min_lot_area_sqft": round(float(constraints.min_lot_area_sqft), CANONICAL_PRECISION),
        "max_units": int(constraints.max_units),
        "required_frontage_ft": round(frontage_requirement, CANONICAL_PRECISION),
        "approx_frontage_ft": round(approx_frontage_ft, CANONICAL_PRECISION),
        "candidates_generated": generated,
        "candidates_rejected_invalid_geometry": rejected_invalid,
        "candidates_rejected_connectivity": rejected_connectivity,
        "best_attempt_summary": dict((list(debug_metrics.get("partial_candidates", [])) or [{}])[0]) if debug_metrics.get("partial_candidates") else {},
    }

    if not parcel_polygon.is_valid:
        return LayoutSearchError(
            "invalid_geometry",
            f"Parcel geometry is invalid for layout search: {parcel.parcel_id}",
            details={"reason_category": "GEOMETRY_INVALID", **details},
        )
    if parcel_area_sqft + 1e-6 < float(constraints.min_lot_area_sqft):
        return LayoutSearchError(
            "too_small",
            f"Parcel is smaller than the minimum lot size requirement: {parcel.parcel_id}",
            details={"reason_category": "TOO_SMALL", **details},
        )
    if constraints.max_units <= 0:
        return LayoutSearchError(
            "zoning_constraint_fail",
            f"Zoning constraints allow zero buildable units for parcel {parcel.parcel_id}",
            details={"reason_category": "ZONING_CONSTRAINT_FAIL", **details},
        )
    if approx_frontage_ft + 1e-6 < frontage_requirement:
        return LayoutSearchError(
            "frontage_fail",
            f"Parcel frontage cannot satisfy the required frontage envelope: {parcel.parcel_id}",
            details={"reason_category": "FRONTAGE_FAIL", **details},
        )
    if rejected_invalid >= max(generated, 1):
        return LayoutSearchError(
            "invalid_geometry",
            f"Generated candidates failed geometry validation for parcel {parcel.parcel_id}",
            details={"reason_category": "GEOMETRY_INVALID", **details},
        )
    if generated > 0:
        return LayoutSearchError(
            "zoning_constraint_fail",
            f"Generated candidates could not satisfy layout constraints for parcel {parcel.parcel_id}",
            details={"reason_category": "ZONING_CONSTRAINT_FAIL", **details},
        )
    return LayoutSearchError(
        "solver_fail",
        f"Layout solver produced no viable candidates for parcel {parcel.parcel_id}",
        details={"reason_category": "SOLVER_FAIL", **details},
    )


def _build_layout_parameters(
    parcel: Parcel,
    zoning: ZoningRules,
    parcel_area_sqft: float,
    additional_constraints: dict[str, float | bool] | None = None,
) -> tuple[SolverConstraints, SearchHeuristics]:
    additional_constraints = dict(additional_constraints or {})
    explicit_block_depth = additional_constraints.get("block_depth_ft")
    explicit_block_depth_ft = float(explicit_block_depth) if isinstance(explicit_block_depth, (int, float)) else None
    explicit_frontage_target = additional_constraints.get("lot_frontage_ft")
    explicit_frontage_target_ft = (
        float(explicit_frontage_target) if isinstance(explicit_frontage_target, (int, float)) else None
    )
    min_lot_size_sqft = _required_zoning_value(zoning, "min_lot_size_sqft")
    front = _setback_value(zoning, "front")
    side = _setback_value(zoning, "side")
    rear = _setback_value(zoning, "rear")
    base_lot_depth_ft = max(MIN_FEASIBLE_LOT_DEPTH_FT, 110.0 - front - rear)
    derived_frontage_ft = _derive_min_frontage(min_lot_size_sqft, base_lot_depth_ft, side)
    road_width_ft = float(zoning.road_right_of_way_ft) if zoning.road_right_of_way_ft is not None else 32.0
    max_units = _max_units(parcel_area_sqft, zoning, min_lot_size_sqft)
    if max_units <= 0:
        raise LayoutSearchError(
            "zoning_constraint_fail",
            f"Density/min-lot constraints allow zero units for parcel {parcel.parcel_id}",
            details={
                "reason_category": "ZONING_CONSTRAINT_FAIL",
                "parcel_area_sqft": round(parcel_area_sqft, CANONICAL_PRECISION),
                "min_lot_area_sqft": round(min_lot_size_sqft, CANONICAL_PRECISION),
                "max_units": 0,
            },
        )
    derived_min_frontage = bool(additional_constraints.get("derived_min_frontage_ft", False))
    configured_min_frontage = float(zoning.min_frontage_ft) if zoning.min_frontage_ft is not None else None
    if derived_min_frontage:
        configured_min_frontage = None
    min_frontage_from_agent = additional_constraints.get("frontage_min_ft")
    if isinstance(min_frontage_from_agent, (int, float)):
        configured_min_frontage = (
            max(float(configured_min_frontage or 0.0), float(min_frontage_from_agent))
            if configured_min_frontage is not None
            else float(min_frontage_from_agent)
        )
    if explicit_frontage_target_ft is not None:
        configured_min_frontage = (
            max(float(configured_min_frontage or 0.0), explicit_frontage_target_ft)
            if configured_min_frontage is not None
            else explicit_frontage_target_ft
        )
    frontage_hint_ft = configured_min_frontage if configured_min_frontage is not None else derived_frontage_ft
    if frontage_hint_ft <= 0.0:
        frontage_hint_ft = derived_frontage_ft
    area_compatible_depth_ft = min_lot_size_sqft / max(frontage_hint_ft, 1.0)
    target_lot_depth_ft = (
        explicit_block_depth_ft
        if explicit_block_depth_ft is not None
        else max(base_lot_depth_ft, area_compatible_depth_ft)
    )
    required_buildable_width_ft = max((min_lot_size_sqft / max(target_lot_depth_ft, 1.0)) - (2.0 * side), 0.0)
    max_frontage_from_agent = additional_constraints.get("frontage_max_ft")
    max_frontage_ft = float(max_frontage_from_agent) if isinstance(max_frontage_from_agent, (int, float)) else None
    road_access_required = bool(additional_constraints.get("road_access_required", False))
    max_block_length = additional_constraints.get("max_block_length_ft")
    max_block_length_ft = float(max_block_length) if isinstance(max_block_length, (int, float)) else None
    easement = additional_constraints.get("easement_buffer_ft")
    easement_buffer_ft = float(easement) if isinstance(easement, (int, float)) else None

    solver_constraints = SolverConstraints(
        zoning_rules=zoning.model_dump(),
        min_lot_area_sqft=min_lot_size_sqft,
        max_units=max_units,
        min_frontage_ft=configured_min_frontage,
        max_frontage_ft=max_frontage_ft,
        required_buildable_width_ft=required_buildable_width_ft,
        side_setback_ft=side,
        max_buildable_depth_ft=target_lot_depth_ft,
        setbacks={
            "front": getattr(zoning.setbacks, "front", None),
            "side": getattr(zoning.setbacks, "side", None),
            "rear": getattr(zoning.setbacks, "rear", None),
        },
        road_right_of_way_ft=float(zoning.road_right_of_way_ft) if zoning.road_right_of_way_ft is not None else None,
        road_access_required=road_access_required,
        max_block_length_ft=max_block_length_ft,
        easement_buffer_ft=easement_buffer_ft,
        additional_zoning_constraints=additional_constraints,
    )
    heuristics = SearchHeuristics(
        road_width_ft=road_width_ft,
        target_lot_depth_ft=target_lot_depth_ft,
        frontage_hint_ft=frontage_hint_ft,
        strategies=PRODUCTION_STRATEGIES,
        runtime_budget_seconds=DEFAULT_RUNTIME_BUDGET_SECONDS,
    )
    return solver_constraints, heuristics


def _solver_constraint_payload(constraints: SolverConstraints) -> dict:
    return {
        "min_lot_area_sqft": constraints.min_lot_area_sqft,
        "max_units": constraints.max_units,
        "min_frontage_ft": constraints.min_frontage_ft,
        "max_frontage_ft": constraints.max_frontage_ft,
        "required_buildable_width_ft": constraints.required_buildable_width_ft,
        "side_setback_ft": constraints.side_setback_ft,
        "max_buildable_depth_ft": constraints.max_buildable_depth_ft,
        "setbacks": constraints.setbacks,
        "road_right_of_way_ft": constraints.road_right_of_way_ft,
        "road_access_required": constraints.road_access_required,
        "max_block_length_ft": constraints.max_block_length_ft,
        "easement_buffer_ft": constraints.easement_buffer_ft,
        "additional_zoning_constraints": constraints.additional_zoning_constraints,
    }


def _search_heuristics_payload(heuristics: SearchHeuristics) -> dict:
    return {
        "road_width_ft": heuristics.road_width_ft,
        "target_lot_depth_ft": heuristics.target_lot_depth_ft,
        "frontage_hint_ft": heuristics.frontage_hint_ft,
        "strategies": list(heuristics.strategies),
        "max_runtime_seconds": heuristics.runtime_budget_seconds,
    }


def _candidate_layer_geometries(candidate) -> tuple[list[BaseGeometry], list[BaseGeometry]]:
    geojson = getattr(candidate, "geojson", {})
    features = geojson.get("features", []) if isinstance(geojson, dict) else []
    lot_geometries: list[BaseGeometry] = []
    road_geometries: list[BaseGeometry] = []
    if not isinstance(features, list):
        return lot_geometries, road_geometries
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or "type" not in geometry:
            continue
        layer = str(feature.get("properties", {}).get("layer", "")).strip().lower()
        try:
            geom = shape(geometry)
        except Exception:
            continue
        if layer in {"lot", "lots"}:
            lot_geometries.append(geom)
        elif layer in {"road", "roads", "street", "streets"}:
            road_geometries.append(geom)
    return lot_geometries, road_geometries


def _candidate_result_geometries(candidate) -> tuple[list[BaseGeometry], list[BaseGeometry]]:
    local_lots: list[BaseGeometry] = []
    local_roads: list[BaseGeometry] = []
    result = getattr(candidate, "result", None)
    if result is None:
        return local_lots, local_roads
    for lot in getattr(result, "lots", []) or []:
        polygon = getattr(lot, "polygon", None)
        if isinstance(polygon, BaseGeometry) and not polygon.is_empty:
            local_lots.append(polygon)
    for segment in getattr(result, "segments", []) or []:
        line = getattr(segment, "line", None)
        if isinstance(line, BaseGeometry) and not line.is_empty:
            local_roads.append(line)
    return local_lots, local_roads


def _max_road_segment_length_ft(road_geometries: list[BaseGeometry]) -> float:
    lengths: list[float] = []
    for geom in road_geometries:
        if isinstance(geom, LineString):
            lengths.append(float(geom.length))
        elif isinstance(geom, MultiLineString):
            lengths.extend(float(segment.length) for segment in geom.geoms)
    return max(lengths) if lengths else 0.0


def _roads_form_connected_graph(road_geometries: list[BaseGeometry]) -> bool:
    if not road_geometries:
        return True
    segments: list[LineString] = []
    for geom in road_geometries:
        if isinstance(geom, LineString):
            segments.append(geom)
        elif isinstance(geom, MultiLineString):
            segments.extend(list(geom.geoms))
    if not segments:
        return True
    adjacency: dict[int, set[int]] = {index: set() for index in range(len(segments))}
    for index, segment in enumerate(segments):
        for other_index in range(index + 1, len(segments)):
            other = segments[other_index]
            try:
                if segment.intersects(other) or segment.touches(other):
                    adjacency[index].add(other_index)
                    adjacency[other_index].add(index)
            except Exception:
                continue
    visited: set[int] = set()
    stack = [0]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        stack.extend(sorted(adjacency[node] - visited))
    return len(visited) == len(segments)


def _lots_are_non_overlapping(lot_geometries: list[BaseGeometry]) -> bool:
    for index, lot in enumerate(lot_geometries):
        for other in lot_geometries[index + 1 :]:
            try:
                if lot.intersection(other).area > 1e-4:
                    return False
            except Exception:
                return False
    return True


def _validate_candidate_geometry(parcel_id: str, candidate, parcel_polygon: Polygon) -> None:
    local_lots, local_roads = _candidate_result_geometries(candidate)
    output_lots, _output_roads = _candidate_layer_geometries(candidate)
    if not local_lots and not output_lots:
        return
    for lot in local_lots:
        if lot.is_empty or not lot.is_valid:
            raise LayoutConstraintViolationError(f"Layout produced invalid lot geometry for parcel {parcel_id}")
        if lot.geom_type != "Polygon":
            raise LayoutConstraintViolationError(f"Layout produced non-polygon lot geometry for parcel {parcel_id}")
        if not lot.within(parcel_polygon.buffer(1e-6)):
            raise LayoutConstraintViolationError(f"Layout produced out-of-bounds lot geometry for parcel {parcel_id}")
    overlap_lots = output_lots or local_lots
    if not _lots_are_non_overlapping(overlap_lots):
        raise LayoutConstraintViolationError(f"Layout produced overlapping lots for parcel {parcel_id}")
    if local_roads:
        for road in local_roads:
            if road.is_empty or not road.is_valid:
                raise LayoutConstraintViolationError(f"Layout produced invalid road geometry for parcel {parcel_id}")
        if not _roads_form_connected_graph(local_roads):
            raise LayoutConstraintViolationError(f"Layout produced disconnected road geometry for parcel {parcel_id}")


def _validate_candidate_constraints(
    parcel_id: str,
    candidate,
    constraints: SolverConstraints,
    parcel_polygon: Polygon | None = None,
) -> None:
    candidate_lots = getattr(candidate.result, "lots", [])
    lot_count = int(candidate.result.metrics.get("lot_count", len(candidate_lots)))
    if lot_count > constraints.max_units:
        raise LayoutConstraintViolationError(f"Layout exceeds zoning density limit for parcel {parcel_id}")
    for lot in candidate_lots:
        if lot.area_sqft + 1e-6 < constraints.min_lot_area_sqft:
            raise LayoutConstraintViolationError(f"Layout violates min_lot_size_sqft for parcel {parcel_id}")
        if constraints.min_frontage_ft is not None and lot.frontage_ft + 1e-6 < constraints.min_frontage_ft:
            raise LayoutConstraintViolationError(f"Layout violates min_frontage_ft for parcel {parcel_id}")
        if constraints.max_frontage_ft is not None and lot.frontage_ft - 1e-6 > constraints.max_frontage_ft:
            raise LayoutConstraintViolationError(f"Layout violates max frontage constraint for parcel {parcel_id}")
        if lot.frontage_ft + 1e-6 < constraints.required_buildable_width_ft + (2.0 * constraints.side_setback_ft):
            raise LayoutConstraintViolationError(f"Layout violates side-setback buildable width for parcel {parcel_id}")
        if lot.depth_ft - 1e-6 > constraints.max_buildable_depth_ft * 1.02:
            raise LayoutConstraintViolationError(f"Layout violates setback-derived lot depth for parcel {parcel_id}")

    lot_geometries, road_geometries = _candidate_result_geometries(candidate)
    if constraints.road_access_required and lot_geometries:
        if not road_geometries:
            raise LayoutConstraintViolationError(f"Layout violates road_access_required for parcel {parcel_id}")
        for lot_geometry in lot_geometries:
            has_access = any(
                lot_geometry.touches(road_geometry) or lot_geometry.intersects(road_geometry)
                for road_geometry in road_geometries
            )
            if not has_access:
                raise LayoutConstraintViolationError(f"Layout violates road_access_required for parcel {parcel_id}")

    if constraints.max_block_length_ft is not None and road_geometries:
        if _max_road_segment_length_ft(road_geometries) - 1e-6 > constraints.max_block_length_ft:
            raise LayoutConstraintViolationError(f"Layout violates max_block_length_ft for parcel {parcel_id}")

    if (
        parcel_polygon is not None
        and constraints.easement_buffer_ft is not None
        and constraints.easement_buffer_ft > 0.0
        and lot_geometries
    ):
        parcel_boundary = parcel_polygon.boundary
        for lot_geometry in lot_geometries:
            if lot_geometry.distance(parcel_boundary) + 1e-6 < constraints.easement_buffer_ft:
                raise LayoutConstraintViolationError(f"Layout violates easement_buffer_ft for parcel {parcel_id}")

    if parcel_polygon is not None:
        _validate_candidate_geometry(parcel_id, candidate, parcel_polygon)


def _to_subdivision_layout(parcel: Parcel, layout: LayoutResult) -> SubdivisionLayout:
    lot_area = sum(float(shape(geometry).area) for geometry in layout.lot_geometries)
    return SubdivisionLayout(
        layout_id=layout.layout_id,
        parcel_id=parcel.parcel_id,
        street_network=layout.road_geometries,
        lot_geometries=layout.lot_geometries,
        lot_count=layout.units,
        open_space_area=max(float(parcel.area) - lot_area, 0.0),
        road_length=layout.road_length,
        utility_length=0.0,
        metadata=EngineMetadata(source_engine="bedrock.layout_service", source_run_id=None),
    )


def _run_layout_search_safe(**kwargs):
    try:
        return run_layout_search(**kwargs)
    except (ValueError, LayoutSearchError):
        raise
    except Exception as exc:
        raise LayoutSearchError("layout_solver_failure", f"Layout solver failed safely: {exc}") from exc


def _run_strategy_search(
    *,
    parcel_polygon_local: Polygon,
    parcel_area_sqft: float,
    projection: dict[str, float],
    solver_constraints: SolverConstraints,
    search_heuristics: SearchHeuristics,
    strategy: str,
    n_candidates: int,
    n_top: int,
    use_prior: bool,
):
    return _run_layout_search_safe(
        parcel_polygon=parcel_polygon_local,
        area_sqft=parcel_area_sqft,
        to_lnglat=lambda x_ft, y_ft: _to_geojson_coords([(x_ft, y_ft)], projection)[0],
        n_candidates=max(1, n_candidates),
        n_top=max(1, n_top),
        zoning_rules=solver_constraints.zoning_rules,
        solver_constraints=_solver_constraint_payload(solver_constraints),
        search_heuristics={**_search_heuristics_payload(search_heuristics), "strategies": [strategy]},
        road_width_ft=search_heuristics.road_width_ft,
        lot_depth=search_heuristics.target_lot_depth_ft,
        min_frontage_ft=search_heuristics.frontage_hint_ft,
        min_lot_area_sqft=solver_constraints.min_lot_area_sqft,
        side_setback_ft=solver_constraints.side_setback_ft,
        min_buildable_width_ft=solver_constraints.required_buildable_width_ft,
        max_units=solver_constraints.max_units,
        use_prior=use_prior,
    )


def _candidate_rank_key(candidate, constraints: SolverConstraints, parcel_polygon: Polygon):
    metrics = getattr(candidate.result, "metrics", {})
    lot_count = int(metrics.get("lot_count", 0))
    road_length = float(metrics.get("total_road_ft", 0.0))
    lot_geometries, road_geometries = _candidate_layer_geometries(candidate)
    soft_penalty = 0.0
    if constraints.max_block_length_ft is not None and road_geometries:
        soft_penalty += max(0.0, _max_road_segment_length_ft(road_geometries) - constraints.max_block_length_ft)
    if constraints.easement_buffer_ft is not None and constraints.easement_buffer_ft > 0.0 and lot_geometries:
        parcel_boundary = parcel_polygon.boundary
        min_clearance = min(float(lot.distance(parcel_boundary)) for lot in lot_geometries)
        soft_penalty += max(0.0, constraints.easement_buffer_ft - min_clearance)
    fingerprint_payload = {
        "score": getattr(candidate, "score", 0.0),
        "metrics": metrics,
        "geojson": getattr(candidate, "geojson", {}),
    }
    fingerprint = hashlib.sha1(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return (
        -round(float(candidate.score), CANONICAL_PRECISION),
        soft_penalty,
        -lot_count,
        road_length,
        fingerprint,
    )


def generate_candidates(
    parcel: Parcel,
    zoning: ZoningRules,
    max_candidates: int = 50,
    debug_metrics: dict | None = None,
    *,
    strategies: tuple[str, ...] | None = None,
    search_overrides: dict[str, float] | None = None,
):
    if max_candidates < 1:
        raise ValueError("max_candidates must be >= 1")
    max_candidates = min(int(max_candidates), MAX_CANDIDATE_CAP)
    debug_metrics = debug_metrics if debug_metrics is not None else {}
    debug_metrics.setdefault("total_candidates_generated", 0)
    debug_metrics.setdefault("candidates_rejected_invalid_geometry", 0)
    debug_metrics.setdefault("candidates_rejected_connectivity", 0)
    debug_metrics.setdefault("candidates_surviving", 0)
    debug_metrics.setdefault("final_selected_layout_index", None)
    debug_metrics.setdefault("repair_events", [])
    debug_metrics.setdefault("parcel_preprocessing", [])
    debug_metrics.setdefault("partial_candidates", [])
    translation = translate_zoning_for_layout(parcel, zoning)
    if translation.usability_class == "non_usable" or translation.zoning is None:
        raise LayoutSearchError(
            "non_usable_zoning",
            json.dumps(
                {
                    "reason": "zoning_not_layout_usable",
                    "usability_class": translation.usability_class,
                    "issues": [
                        {"code": issue.code, "field": issue.field, "message": issue.message}
                        for issue in translation.issues
                    ],
                },
                separators=(",", ":"),
            ),
        )
    zoning = translation.zoning
    started = time.perf_counter()
    parcel_polygon_local, projection = _geometry_to_local_feet(parcel.geometry)
    parcel_polygon_local = _preprocess_parcel_polygon(parcel_polygon_local, debug_metrics)
    parcel_area_sqft = float(parcel.area or parcel_polygon_local.area)
    solver_constraints, search_heuristics = _build_layout_parameters(
        parcel,
        zoning,
        parcel_area_sqft,
        additional_constraints=translation.additional_constraints,
    )
    search_overrides = dict(search_overrides or {})
    density_factor = min(1.0, max(0.25, float(search_overrides.get("density_factor", 1.0))))
    density_capped_units = max(1, math.floor(solver_constraints.max_units * density_factor))
    solver_constraints = SolverConstraints(
        zoning_rules=solver_constraints.zoning_rules,
        min_lot_area_sqft=solver_constraints.min_lot_area_sqft,
        max_units=min(solver_constraints.max_units, density_capped_units),
        min_frontage_ft=solver_constraints.min_frontage_ft,
        max_frontage_ft=solver_constraints.max_frontage_ft,
        required_buildable_width_ft=solver_constraints.required_buildable_width_ft,
        side_setback_ft=solver_constraints.side_setback_ft,
        max_buildable_depth_ft=solver_constraints.max_buildable_depth_ft,
        setbacks=solver_constraints.setbacks,
        road_right_of_way_ft=solver_constraints.road_right_of_way_ft,
        road_access_required=solver_constraints.road_access_required,
        max_block_length_ft=solver_constraints.max_block_length_ft,
        easement_buffer_ft=solver_constraints.easement_buffer_ft,
        additional_zoning_constraints=solver_constraints.additional_zoning_constraints,
    )
    strategy_tuple = tuple(strategies or PRODUCTION_STRATEGIES)
    search_heuristics = SearchHeuristics(
        road_width_ft=max(24.0, search_heuristics.road_width_ft * float(search_overrides.get("road_width_factor", 1.0))),
        target_lot_depth_ft=max(
            MIN_FEASIBLE_LOT_DEPTH_FT,
            search_heuristics.target_lot_depth_ft * float(search_overrides.get("lot_depth_factor", 1.0)),
        ),
        frontage_hint_ft=max(35.0, search_heuristics.frontage_hint_ft * float(search_overrides.get("frontage_hint_factor", 1.0))),
        strategies=tuple(strategy_tuple),
        runtime_budget_seconds=max(
            10.0,
            search_heuristics.runtime_budget_seconds * float(search_overrides.get("runtime_budget_factor", 1.0)),
        ),
    )

    strategy_count = len(strategy_tuple)
    per_strategy_candidates = max(1, max_candidates // strategy_count)
    per_strategy_top = max(1, min(3, per_strategy_candidates))

    aggregated = []
    for strategy in strategy_tuple:
        if (time.perf_counter() - started) > search_heuristics.runtime_budget_seconds:
            break
        strategy_candidates = []
        generation_started = time.perf_counter()
        try:
            strategy_candidates = _run_strategy_search(
                parcel_polygon_local=parcel_polygon_local,
                parcel_area_sqft=parcel_area_sqft,
                projection=projection,
                solver_constraints=solver_constraints,
                search_heuristics=search_heuristics,
                strategy=strategy,
                n_candidates=per_strategy_candidates,
                n_top=per_strategy_top,
                use_prior=True,
            )
        except RuntimeError:
            strategy_candidates = []
        if not strategy_candidates:
            try:
                strategy_candidates = _run_strategy_search(
                    parcel_polygon_local=parcel_polygon_local,
                    parcel_area_sqft=parcel_area_sqft,
                    projection=projection,
                    solver_constraints=solver_constraints,
                    search_heuristics=search_heuristics,
                    strategy=strategy,
                    n_candidates=max(1, min(per_strategy_candidates, 8)),
                    n_top=per_strategy_top,
                    use_prior=False,
                )
            except RuntimeError:
                strategy_candidates = []
        debug_metrics["candidate_generation_seconds"] = round(
            float(debug_metrics.get("candidate_generation_seconds", 0.0)) + (time.perf_counter() - generation_started),
            CANONICAL_PRECISION,
        )
        debug_metrics["total_candidates_generated"] = int(debug_metrics["total_candidates_generated"]) + len(strategy_candidates)
        validation_started = time.perf_counter()
        for candidate in strategy_candidates:
            try:
                _validate_candidate_constraints(parcel.parcel_id, candidate, solver_constraints, parcel_polygon_local)
            except LayoutConstraintViolationError:
                message = str(sys.exc_info()[1] or "")
                _record_partial_candidate(
                    debug_metrics,
                    candidate,
                    violation=message or "constraint_violation",
                    strategy=strategy,
                    profile_label=str(debug_metrics.get("attempt_profile") or ""),
                )
                if "disconnected road geometry" in message:
                    debug_metrics["candidates_rejected_connectivity"] = int(debug_metrics["candidates_rejected_connectivity"]) + 1
                else:
                    debug_metrics["candidates_rejected_invalid_geometry"] = int(debug_metrics["candidates_rejected_invalid_geometry"]) + 1
                continue
            aggregated.append(candidate)
        debug_metrics["validation_seconds"] = round(
            float(debug_metrics.get("validation_seconds", 0.0)) + (time.perf_counter() - validation_started),
            CANONICAL_PRECISION,
        )

    if not aggregated:
        if (time.perf_counter() - started) > search_heuristics.runtime_budget_seconds:
            raise LayoutSearchError(
                "runtime_budget_exceeded",
                f"Layout search exceeded runtime budget for parcel {parcel.parcel_id}",
                details={"reason_category": "SOLVER_FAIL", "parcel_id": parcel.parcel_id},
            )
        fallback_strategies = list(strategy_tuple)
        fallback_candidates = _run_layout_search_safe(
            parcel_polygon=parcel_polygon_local,
            area_sqft=parcel_area_sqft,
            to_lnglat=lambda x_ft, y_ft: _to_geojson_coords([(x_ft, y_ft)], projection)[0],
            n_candidates=max(1, min(max_candidates, 12)),
            n_top=max(1, min(3, max_candidates)),
            zoning_rules=solver_constraints.zoning_rules,
            solver_constraints=_solver_constraint_payload(solver_constraints),
            search_heuristics={**_search_heuristics_payload(search_heuristics), "strategies": fallback_strategies},
            road_width_ft=search_heuristics.road_width_ft,
            lot_depth=search_heuristics.target_lot_depth_ft,
            min_frontage_ft=search_heuristics.frontage_hint_ft,
            min_lot_area_sqft=solver_constraints.min_lot_area_sqft,
            side_setback_ft=solver_constraints.side_setback_ft,
            min_buildable_width_ft=solver_constraints.required_buildable_width_ft,
            max_units=solver_constraints.max_units,
            use_prior=False,
        )
        debug_metrics["total_candidates_generated"] = int(debug_metrics["total_candidates_generated"]) + len(fallback_candidates)
        validation_started = time.perf_counter()
        for candidate in fallback_candidates:
            try:
                _validate_candidate_constraints(parcel.parcel_id, candidate, solver_constraints, parcel_polygon_local)
            except LayoutConstraintViolationError:
                message = str(sys.exc_info()[1] or "")
                _record_partial_candidate(
                    debug_metrics,
                    candidate,
                    violation=message or "constraint_violation",
                    strategy="fallback",
                    profile_label=str(debug_metrics.get("attempt_profile") or ""),
                )
                if "disconnected road geometry" in message:
                    debug_metrics["candidates_rejected_connectivity"] = int(debug_metrics["candidates_rejected_connectivity"]) + 1
                else:
                    debug_metrics["candidates_rejected_invalid_geometry"] = int(debug_metrics["candidates_rejected_invalid_geometry"]) + 1
                continue
            aggregated.append(candidate)
        debug_metrics["validation_seconds"] = round(
            float(debug_metrics.get("validation_seconds", 0.0)) + (time.perf_counter() - validation_started),
            CANONICAL_PRECISION,
        )

    if not aggregated:
        raise _classified_layout_failure(
            parcel=parcel,
            parcel_polygon=parcel_polygon_local,
            constraints=solver_constraints,
            debug_metrics=debug_metrics,
        )

    aggregated.sort(key=lambda candidate: _candidate_rank_key(candidate, solver_constraints, parcel_polygon_local))
    selected = aggregated[: max(1, max_candidates)]
    debug_metrics["candidates_surviving"] = len(selected)
    debug_metrics["total_runtime_seconds"] = round(time.perf_counter() - started, CANONICAL_PRECISION)
    return selected


def _retry_profiles(zoning: ZoningRules, max_candidates: int) -> tuple[SearchAttemptProfile, ...]:
    low_density = bool(
        (zoning.min_lot_size_sqft is not None and float(zoning.min_lot_size_sqft) >= 15000.0)
        or (zoning.max_units_per_acre is not None and float(zoning.max_units_per_acre) <= 2.5)
    )
    return (
        SearchAttemptProfile(
            label="default",
            strategies=PRODUCTION_STRATEGIES,
            max_candidates=max_candidates,
        ),
        SearchAttemptProfile(
            label="default_dense",
            strategies=PRODUCTION_STRATEGIES,
            max_candidates=min(MAX_CANDIDATE_CAP, max_candidates + 8),
            lot_depth_factor=0.92,
            frontage_hint_factor=0.95,
            road_width_factor=0.95,
            runtime_budget_factor=1.05,
        ),
        SearchAttemptProfile(
            label="expanded_topologies",
            strategies=PRODUCTION_STRATEGIES + ("radial",),
            max_candidates=min(MAX_CANDIDATE_CAP, max_candidates + 12),
            road_width_factor=0.95,
            runtime_budget_factor=1.1,
        ),
        SearchAttemptProfile(
            label="expanded_wide_spacing",
            strategies=("loop_custom", "spine-road", "grid", "t_junction", "radial"),
            max_candidates=min(MAX_CANDIDATE_CAP, max_candidates + 16),
            lot_depth_factor=1.1,
            frontage_hint_factor=0.92,
            road_width_factor=0.88,
            runtime_budget_factor=1.15,
        ),
        SearchAttemptProfile(
            label="loop_focus",
            strategies=("loop_custom", "t_junction", "herringbone", "spine-road", "cul-de-sac"),
            max_candidates=min(MAX_CANDIDATE_CAP, max_candidates + 12),
            lot_depth_factor=1.15 if low_density else 1.0,
            road_width_factor=0.9,
            runtime_budget_factor=1.15,
        ),
        SearchAttemptProfile(
            label="loop_dense",
            strategies=("loop_custom", "t_junction", "herringbone", "grid"),
            max_candidates=min(MAX_CANDIDATE_CAP, max_candidates + 18),
            lot_depth_factor=0.9 if not low_density else 1.05,
            frontage_hint_factor=0.9,
            road_width_factor=0.85,
            runtime_budget_factor=1.2,
        ),
        SearchAttemptProfile(
            label="grid_compact",
            strategies=("grid", "herringbone", "spine-road"),
            max_candidates=min(MAX_CANDIDATE_CAP, max_candidates + 10),
            lot_depth_factor=0.85,
            frontage_hint_factor=0.9,
            road_width_factor=0.9,
            runtime_budget_factor=1.0,
        ),
        SearchAttemptProfile(
            label="grid_wide",
            strategies=("grid", "loop_custom", "radial"),
            max_candidates=min(MAX_CANDIDATE_CAP, max_candidates + 14),
            lot_depth_factor=1.18,
            frontage_hint_factor=0.95,
            road_width_factor=0.92,
            runtime_budget_factor=1.1,
        ),
        SearchAttemptProfile(
            label="culdesac_branching",
            strategies=("cul-de-sac", "t_junction", "loop_custom"),
            max_candidates=min(MAX_CANDIDATE_CAP, max_candidates + 16),
            lot_depth_factor=1.08,
            frontage_hint_factor=0.94,
            road_width_factor=0.9,
            runtime_budget_factor=1.15,
        ),
        SearchAttemptProfile(
            label="rural_low_density" if low_density else "compact_retry",
            strategies=("loop_custom", "spine-road", "t_junction", "grid", "herringbone", "radial"),
            max_candidates=min(MAX_CANDIDATE_CAP, max_candidates + 18),
            lot_depth_factor=1.3 if low_density else 0.95,
            frontage_hint_factor=1.0,
            road_width_factor=0.9 if low_density else 1.0,
            runtime_budget_factor=1.2,
        ),
        SearchAttemptProfile(
            label="rural_extreme" if low_density else "compact_extreme",
            strategies=("loop_custom", "spine-road", "radial", "t_junction", "grid"),
            max_candidates=MAX_CANDIDATE_CAP,
            lot_depth_factor=1.45 if low_density else 0.85,
            frontage_hint_factor=0.88,
            road_width_factor=0.82 if low_density else 0.9,
            runtime_budget_factor=1.35,
        ),
    )


def _near_feasible_result(
    parcel: Parcel,
    zoning: ZoningRules,
    error: LayoutSearchError,
    attempted_profiles: list[str],
    attempted_repairs: list[str],
) -> dict | None:
    details = dict(error.details or {})
    reason_category = str(details.get("reason_category") or error.code).upper()
    parcel_area_sqft = float(details.get("parcel_area_sqft") or parcel.area or 0.0)
    min_lot_area_sqft = float(details.get("min_lot_area_sqft") or zoning.min_lot_size_sqft or 0.0)
    approx_frontage_ft = float(details.get("approx_frontage_ft") or 0.0)
    required_frontage_ft = float(details.get("required_frontage_ft") or zoning.min_frontage_ft or 0.0)
    limiting_constraints: dict[str, object] = {}
    required_relaxation: dict[str, object] = {}
    best_attempt_summary = dict(details.get("best_attempt_summary") or {})

    if reason_category == "ZONING_CONSTRAINT_FAIL":
        max_units = int(details.get("max_units") or 0)
        limiting_constraints = {
            "parcel_area_sqft": round(parcel_area_sqft, CANONICAL_PRECISION),
            "min_lot_area_sqft": round(min_lot_area_sqft, CANONICAL_PRECISION),
            "max_units": max_units,
            "district": zoning.district,
        }
        if parcel_area_sqft > 0.0 and min_lot_area_sqft > 0.0:
            target_lot_area_sqft = parcel_area_sqft / max(max_units or 1, 1)
            required_relaxation["min_lot_area_sqft"] = {
                "current": round(min_lot_area_sqft, CANONICAL_PRECISION),
                "needed": round(target_lot_area_sqft, CANONICAL_PRECISION),
                "reduction_sqft": round(max(min_lot_area_sqft - target_lot_area_sqft, 0.0), CANONICAL_PRECISION),
            }
        if required_frontage_ft > 0.0 and approx_frontage_ft > 0.0 and approx_frontage_ft < required_frontage_ft:
            required_relaxation["min_frontage_ft"] = {
                "current": round(required_frontage_ft, CANONICAL_PRECISION),
                "needed": round(approx_frontage_ft, CANONICAL_PRECISION),
                "reduction_ft": round(required_frontage_ft - approx_frontage_ft, CANONICAL_PRECISION),
            }
    elif reason_category == "GEOMETRY_INVALID":
        limiting_constraints = {
            "parcel_id": parcel.parcel_id,
            "geometry_validation": "failed_after_repair",
        }
        required_relaxation["geometry_recovery"] = {
            "action": "manual_geometry_cleanup",
            "attempted_repairs": list(attempted_repairs),
        }
    elif reason_category == "FRONTAGE_FAIL":
        limiting_constraints = {
            "approx_frontage_ft": round(approx_frontage_ft, CANONICAL_PRECISION),
            "required_frontage_ft": round(required_frontage_ft, CANONICAL_PRECISION),
        }
        required_relaxation["min_frontage_ft"] = {
            "current": round(required_frontage_ft, CANONICAL_PRECISION),
            "needed": round(approx_frontage_ft, CANONICAL_PRECISION),
            "reduction_ft": round(max(required_frontage_ft - approx_frontage_ft, 0.0), CANONICAL_PRECISION),
        }
    elif reason_category == "SOLVER_FAIL":
        limiting_constraints = {
            "parcel_area_sqft": round(parcel_area_sqft, CANONICAL_PRECISION),
            "min_lot_area_sqft": round(min_lot_area_sqft, CANONICAL_PRECISION),
            "required_frontage_ft": round(required_frontage_ft, CANONICAL_PRECISION),
            "approx_frontage_ft": round(approx_frontage_ft, CANONICAL_PRECISION),
            "max_units": int(details.get("max_units") or 0),
            "district": zoning.district,
            "candidates_generated": int(details.get("candidates_generated") or 0),
        }
        required_relaxation["search_budget"] = {
            "attempted_profiles": list(attempted_profiles),
            "candidate_budget_increase": 18,
            "road_spacing_sweep_required": True,
            "lot_depth_sweep_required": True,
            "frontage_sweep_required": True,
        }
        if best_attempt_summary:
            required_relaxation["best_partial_candidate"] = best_attempt_summary
    else:
        return None

    return {
        "status": "near_feasible",
        "reason_category": reason_category,
        "limiting_constraints": limiting_constraints,
        "required_relaxation": required_relaxation,
        "best_attempt_summary": best_attempt_summary,
        "attempted_strategies": list(attempted_profiles),
        "attempted_repairs": list(attempted_repairs),
    }


def _normalize_selected_candidate(parcel: Parcel, candidates, debug_metrics: dict) -> LayoutResult:
    for index, candidate in enumerate(candidates):
        try:
            layout = _normalize_candidate(parcel, candidate, debug_metrics=debug_metrics)
            debug_metrics["final_selected_layout_index"] = index
            debug_metrics["repair_usage_ratio"] = round(
                len(debug_metrics.get("repair_events", [])) / max(int(debug_metrics.get("total_candidates_generated", 0)), 1),
                CANONICAL_PRECISION,
            )
            return layout
        except LayoutConstraintViolationError:
            debug_metrics["candidates_rejected_invalid_geometry"] = int(debug_metrics.get("candidates_rejected_invalid_geometry", 0)) + 1
            continue
    raise LayoutSearchError(
        "solver_fail",
        f"No viable layouts generated for parcel {parcel.parcel_id}",
        details={"reason_category": "SOLVER_FAIL", "parcel_id": parcel.parcel_id},
    )


def _normalize_candidate_batch(
    parcel: Parcel,
    candidates,
    *,
    max_layouts: int,
    debug_metrics: dict[str, object],
    search_plan: LayoutSearchPlan,
) -> LayoutCandidateBatch:
    normalized: list[LayoutResult] = []
    seen_layout_ids: set[str] = set()
    for candidate in candidates:
        try:
            layout = _normalize_candidate(parcel, candidate, debug_metrics=debug_metrics)
        except LayoutConstraintViolationError:
            debug_metrics["candidates_rejected_invalid_geometry"] = int(
                debug_metrics.get("candidates_rejected_invalid_geometry", 0)
            ) + 1
            continue
        if layout.layout_id in seen_layout_ids:
            continue
        seen_layout_ids.add(layout.layout_id)
        normalized.append(
            layout.model_copy(
                update={
                    "metadata": EngineMetadata(
                        source_engine="bedrock.services.layout_service",
                        source_type="candidate_search",
                    )
                }
            )
        )
        if len(normalized) >= max_layouts:
            break
    return LayoutCandidateBatch(
        parcel_id=parcel.parcel_id,
        search_plan=search_plan,
        candidate_count_generated=int(debug_metrics.get("total_candidates_generated", 0)),
        candidate_count_valid=len(normalized),
        layouts=normalized,
        search_debug=dict(debug_metrics),
    )


def search_layout_candidates_debug(
    parcel: Parcel,
    zoning: ZoningRules,
    *,
    search_plan: LayoutSearchPlan,
) -> LayoutCandidateBatch:
    debug_metrics: dict[str, object] = {"attempt_profile": search_plan.label}
    candidates = generate_candidates(
        parcel,
        zoning,
        max_candidates=search_plan.max_candidates,
        debug_metrics=debug_metrics,
        strategies=tuple(search_plan.strategies) if search_plan.strategies else None,
        search_overrides=search_plan.search_overrides(),
    )
    debug_metrics["attempt_profiles"] = [search_plan.label]
    return _normalize_candidate_batch(
        parcel,
        candidates,
        max_layouts=search_plan.max_layouts,
        debug_metrics=debug_metrics,
        search_plan=search_plan,
    )


def search_layout_candidates(
    parcel: Parcel,
    zoning: ZoningRules,
    *,
    search_plan: LayoutSearchPlan,
) -> list[LayoutResult]:
    return search_layout_candidates_debug(parcel, zoning, search_plan=search_plan).layouts


def search_layout(parcel: Parcel, zoning: ZoningRules, max_candidates: int = 50) -> LayoutResult:
    attempted_profiles: list[str] = []
    last_error: LayoutSearchError | None = None
    for profile in _retry_profiles(zoning, max_candidates):
        attempted_profiles.append(profile.label)
        try:
            candidates = generate_candidates(
                parcel,
                zoning,
                max_candidates=profile.max_candidates,
                strategies=profile.strategies,
                search_overrides={
                    "lot_depth_factor": profile.lot_depth_factor,
                    "frontage_hint_factor": profile.frontage_hint_factor,
                    "road_width_factor": profile.road_width_factor,
                    "runtime_budget_factor": profile.runtime_budget_factor,
                },
            )
            return _normalize_selected_candidate(parcel, candidates, {"attempt_profiles": attempted_profiles})
        except LayoutSearchError as exc:
            last_error = exc
            continue
    if last_error is not None:
        last_error.details = {
            **dict(last_error.details),
            "attempted_profiles": attempted_profiles,
        }
        raise last_error
    raise LayoutSearchError(
        "solver_fail",
        f"No viable layouts generated for parcel {parcel.parcel_id}",
        details={"reason_category": "SOLVER_FAIL", "parcel_id": parcel.parcel_id, "attempted_profiles": attempted_profiles},
    )


def search_layout_debug(parcel: Parcel, zoning: ZoningRules, max_candidates: int = 50) -> tuple[LayoutResult, dict]:
    attempt_debug: list[dict[str, object]] = []
    last_error: LayoutSearchError | None = None
    for profile in _retry_profiles(zoning, max_candidates):
        debug_metrics: dict[str, object] = {"attempt_profile": profile.label}
        attempt_debug.append(debug_metrics)
        try:
            candidates = generate_candidates(
                parcel,
                zoning,
                max_candidates=profile.max_candidates,
                debug_metrics=debug_metrics,
                strategies=profile.strategies,
                search_overrides={
                    "lot_depth_factor": profile.lot_depth_factor,
                    "frontage_hint_factor": profile.frontage_hint_factor,
                    "road_width_factor": profile.road_width_factor,
                    "runtime_budget_factor": profile.runtime_budget_factor,
                },
            )
            layout = _normalize_selected_candidate(parcel, candidates, debug_metrics)
            debug_metrics["attempt_profiles"] = [entry.get("attempt_profile") for entry in attempt_debug]
            return layout, debug_metrics
        except LayoutSearchError as exc:
            last_error = exc
            debug_metrics["failure"] = dict(exc.details)
            continue
    if last_error is not None:
        last_error.details = {**dict(last_error.details), "attempt_profiles": [entry.get("attempt_profile") for entry in attempt_debug]}
        raise last_error
    raise LayoutSearchError(
        "solver_fail",
        f"No viable layouts generated for parcel {parcel.parcel_id}",
        details={"reason_category": "SOLVER_FAIL", "parcel_id": parcel.parcel_id, "attempt_profiles": [entry.get("attempt_profile") for entry in attempt_debug]},
    )


def search_subdivision_layout(parcel: Parcel, zoning: ZoningRules, max_candidates: int = 50) -> SubdivisionLayout:
    return _to_subdivision_layout(parcel, search_layout(parcel, zoning, max_candidates=max_candidates))


def search_subdivision_layout_candidates(
    parcel: Parcel,
    zoning: ZoningRules,
    *,
    search_plan: LayoutSearchPlan,
) -> list[SubdivisionLayout]:
    return [
        _to_subdivision_layout(parcel, layout)
        for layout in search_layout_candidates(parcel, zoning, search_plan=search_plan)
    ]
