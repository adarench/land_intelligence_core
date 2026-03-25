"""Normalization helpers for parcel geometry ingestion."""

from __future__ import annotations

import math
from typing import Any

from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.validation import explain_validity
from shapely.validation import make_valid

from utils.geometry_validation import validate_coordinate_ranges, validate_geojson_polygon, validate_topology

MAX_VERTEX_COUNT = 512
SIMPLIFICATION_TOLERANCE = 1e-7
WGS84 = "EPSG:4326"
LOCAL_FEET = "BEDROCK:LOCAL_FEET"
SUPPORTED_INTERNAL_CRS = {WGS84, LOCAL_FEET}


def normalize_polygon_geometry(
    geometry: dict[str, Any],
    *,
    input_crs: str | None = None,
) -> tuple[dict[str, Any], BaseGeometry, str]:
    validate_geojson_polygon(geometry)
    validate_coordinate_ranges(geometry["coordinates"])
    normalized_crs = normalize_input_crs(geometry, input_crs=input_crs)

    if geometry["type"] == "Polygon":
        normalized_surface = _normalize_polygon_coordinates(geometry["coordinates"])
    else:
        polygons = [_normalize_polygon_coordinates(polygon_coordinates) for polygon_coordinates in geometry["coordinates"]]
        normalized_surface = _merge_surfaces(polygons)

    normalized_surface = _simplify_if_needed(normalized_surface)
    normalized_surface = _orient_surface(normalized_surface)
    validate_topology(normalized_surface)
    return _to_geojson_dict(mapping(normalized_surface)), normalized_surface, normalized_crs


def compute_area_sqft(geometry: BaseGeometry, *, crs: str) -> float:
    if crs == WGS84:
        return round(_project_surface_to_local_feet(geometry).area, 2)
    return round(float(geometry.area), 2)


def compute_centroid(geometry: BaseGeometry) -> list[float]:
    centroid = geometry.centroid
    return [round(float(centroid.x), 6), round(float(centroid.y), 6)]


def compute_bounding_box(geometry: BaseGeometry) -> list[float]:
    min_x, min_y, max_x, max_y = geometry.bounds
    return [round(float(min_x), 6), round(float(min_y), 6), round(float(max_x), 6), round(float(max_y), 6)]


def _normalize_polygon_coordinates(coordinates: list[list[list[float]]]) -> BaseGeometry:
    exterior = _normalize_ring(coordinates[0])
    holes = [_normalize_ring(ring) for ring in coordinates[1:]]
    polygon = Polygon(exterior, holes)
    if polygon.is_empty:
        raise ValueError("polygon geometry cannot be empty")
    if not polygon.is_valid:
        polygon = _repair_surface(polygon)
    return polygon


def _normalize_ring(ring: list[list[float]]) -> list[tuple[float, float]]:
    cleaned: list[tuple[float, float]] = []

    for position in ring:
        coord = (float(position[0]), float(position[1]))
        if not cleaned or coord != cleaned[-1]:
            cleaned.append(coord)

    if cleaned[0] != cleaned[-1]:
        cleaned.append(cleaned[0])

    if len(cleaned) < 4:
        raise ValueError("polygon rings must contain at least 4 coordinates after normalization")

    return cleaned


def _repair_surface(geometry: BaseGeometry) -> BaseGeometry:
    validity_reason = explain_validity(geometry)
    repaired = _extract_surface(make_valid(geometry))
    if repaired is not None:
        return repaired

    repaired = _extract_surface(geometry.buffer(0))
    if repaired is not None:
        return repaired
    raise ValueError(f"invalid polygon topology: {validity_reason}")


def _looks_like_geographic_coordinates(geometry: BaseGeometry) -> bool:
    min_x, min_y, max_x, max_y = geometry.bounds
    return -180.0 <= min_x <= 180.0 and -180.0 <= max_x <= 180.0 and -90.0 <= min_y <= 90.0 and -90.0 <= max_y <= 90.0


def infer_geometry_crs(geometry: dict[str, Any]) -> str:
    xs: list[float] = []
    ys: list[float] = []
    _collect_positions(geometry.get("coordinates"), xs, ys)
    if xs and ys and all(-180.0 <= x <= 180.0 for x in xs) and all(-90.0 <= y <= 90.0 for y in ys):
        return WGS84
    return LOCAL_FEET


def normalize_input_crs(geometry: dict[str, Any], *, input_crs: str | None) -> str:
    inferred = infer_geometry_crs(geometry)
    if input_crs is None:
        return inferred
    normalized = str(input_crs).strip()
    if normalized not in SUPPORTED_INTERNAL_CRS:
        raise ValueError("Unsupported parcel CRS: " + normalized)
    if normalized != inferred:
        raise ValueError(
            f"Input geometry does not match declared CRS: declared {normalized}, inferred {inferred}"
        )
    return normalized


def _collect_positions(value: Any, xs: list[float], ys: list[float]) -> None:
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(axis, (int, float)) for axis in value[:2]):
            xs.append(float(value[0]))
            ys.append(float(value[1]))
            return
        for item in value:
            _collect_positions(item, xs, ys)


def _project_surface_to_local_feet(geometry: BaseGeometry) -> BaseGeometry:
    centroid = geometry.centroid
    lat0 = math.radians(float(centroid.y))
    feet_per_degree_lat = 111_132.92 * 3.28084
    feet_per_degree_lng = 111_412.84 * math.cos(lat0) * 3.28084

    def _project_ring(ring) -> list[tuple[float, float]]:
        projected: list[tuple[float, float]] = []
        for x, y in ring.coords:
            x_ft = (float(x) - float(centroid.x)) * feet_per_degree_lng
            y_ft = (float(y) - float(centroid.y)) * feet_per_degree_lat
            projected.append((x_ft, y_ft))
        return projected

    if geometry.geom_type == "Polygon":
        return Polygon(_project_ring(geometry.exterior), [_project_ring(interior) for interior in geometry.interiors])
    if geometry.geom_type == "MultiPolygon":
        return MultiPolygon(
            [
                Polygon(_project_ring(polygon.exterior), [_project_ring(interior) for interior in polygon.interiors])
                for polygon in geometry.geoms
            ]
        )
    raise ValueError("surface projection expects Polygon or MultiPolygon")


def _to_geojson_dict(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_to_geojson_dict(item) for item in value]
    if isinstance(value, list):
        return [_to_geojson_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_geojson_dict(item) for key, item in value.items()}
    return value


def _simplify_if_needed(geometry: BaseGeometry) -> BaseGeometry:
    if _vertex_count(geometry) <= MAX_VERTEX_COUNT:
        return geometry
    simplified = geometry.simplify(SIMPLIFICATION_TOLERANCE, preserve_topology=True)
    repaired = _extract_surface(simplified)
    return repaired if repaired is not None else geometry


def _vertex_count(geometry: BaseGeometry) -> int:
    if geometry.geom_type == "Polygon":
        polygons = [geometry]
    elif geometry.geom_type == "MultiPolygon":
        polygons = list(geometry.geoms)
    else:
        return 0
    return sum(len(polygon.exterior.coords) + sum(len(interior.coords) for interior in polygon.interiors) for polygon in polygons)


def _extract_surface(geometry: BaseGeometry) -> BaseGeometry | None:
    if geometry.is_empty:
        return None
    if geometry.geom_type == "Polygon":
        return geometry
    if geometry.geom_type == "MultiPolygon":
        polygons = [polygon for polygon in geometry.geoms if polygon.geom_type == "Polygon" and not polygon.is_empty]
        if not polygons:
            return None
        if len(polygons) == 1:
            return polygons[0]
        return MultiPolygon(polygons)
    if geometry.geom_type == "GeometryCollection":
        polygons = [item for item in geometry.geoms if item.geom_type in {"Polygon", "MultiPolygon"} and not item.is_empty]
        if not polygons:
            return None
        flattened: list[BaseGeometry] = []
        for item in polygons:
            extracted = _extract_surface(item)
            if extracted is not None:
                flattened.append(extracted)
        if not flattened:
            return None
        if len(flattened) == 1:
            return flattened[0]
        merged = unary_union(flattened)
        if merged.geom_type == "Polygon":
            return merged
        if merged.geom_type == "MultiPolygon":
            return merged
        return None
    return None


def _merge_surfaces(surfaces: list[BaseGeometry]) -> BaseGeometry:
    merged = unary_union(surfaces)
    if merged.geom_type in {"Polygon", "MultiPolygon"}:
        return merged
    extracted = _extract_surface(merged)
    if extracted is None or extracted.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("invalid polygon topology: no polygon surface remains after normalization")
    return extracted


def _orient_surface(geometry: BaseGeometry) -> BaseGeometry:
    if geometry.geom_type == "Polygon":
        return orient(geometry, sign=1.0)
    if geometry.geom_type != "MultiPolygon":
        raise ValueError("normalized geometry must remain a Polygon or MultiPolygon")
    polygons = [orient(polygon, sign=1.0) for polygon in geometry.geoms]
    polygons.sort(key=lambda polygon: (-polygon.area, polygon.centroid.x, polygon.centroid.y))
    return MultiPolygon(polygons)
