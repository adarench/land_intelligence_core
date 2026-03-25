"""Validation helpers for parcel geometry ingestion."""

from __future__ import annotations

from typing import Any

import math

from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity


def validate_geojson_polygon(geometry: dict[str, Any]) -> None:
    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a GeoJSON object")
    geometry_type = geometry.get("type")
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("geometry.type must be 'Polygon' or 'MultiPolygon'")

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("geometry.coordinates must contain at least one polygon")

    if geometry_type == "Polygon":
        _validate_polygon_coordinates(coordinates)
        return

    for polygon_index, polygon_coordinates in enumerate(coordinates):
        if not isinstance(polygon_coordinates, list) or not polygon_coordinates:
            raise ValueError(f"polygon {polygon_index} must contain at least one linear ring")
        _validate_polygon_coordinates(polygon_coordinates, polygon_index=polygon_index)


def validate_topology(geometry: BaseGeometry) -> None:
    if geometry.is_empty:
        raise ValueError("polygon geometry cannot be empty")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("normalized geometry must remain a Polygon or MultiPolygon")
    if geometry.area <= 0:
        raise ValueError("polygon geometry must have positive area")
    if not geometry.is_valid:
        raise ValueError(f"invalid polygon topology: {explain_validity(geometry)}")

    polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    for polygon in polygons:
        if not polygon.exterior.is_simple:
            raise ValueError("polygon exterior must not contain self-crossing edges")


def validate_coordinate_ranges(coordinates: list[Any]) -> None:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in _iter_positions(coordinates):
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("polygon coordinates must be finite numeric values")
        xs.append(x)
        ys.append(y)

    if not xs:
        raise ValueError("polygon coordinates must contain at least one coordinate")

    looks_geographic = all(-90 <= y <= 90 for y in ys) and all(-360 <= x <= 360 for x in xs)
    if looks_geographic:
        for x in xs:
            if not -180 <= x <= 180:
                raise ValueError("polygon longitude must be within [-180, 180]")
        for y in ys:
            if not -90 <= y <= 90:
                raise ValueError("polygon latitude must be within [-90, 90]")


def _validate_ring(ring: Any, ring_index: Any) -> None:
    if not isinstance(ring, list):
        raise ValueError(f"ring {ring_index} must be a coordinate array")
    if len(ring) < 4:
        raise ValueError(f"ring {ring_index} must contain at least 4 positions")

    for position_index, position in enumerate(ring):
        if not _is_coordinate(position):
            raise ValueError(
                f"ring {ring_index} position {position_index} must contain numeric x/y values"
            )


def _validate_polygon_coordinates(polygon_coordinates: list[Any], polygon_index: int | None = None) -> None:
    for ring_index, ring in enumerate(polygon_coordinates):
        label = ring_index if polygon_index is None else f"{polygon_index}.{ring_index}"
        _validate_ring(ring, label)


def _is_coordinate(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return False
    return all(isinstance(axis, (int, float)) and not isinstance(axis, bool) for axis in value[:2])


def _iter_positions(value: Any):
    if _is_coordinate(value):
        yield (float(value[0]), float(value[1]))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_positions(item)
