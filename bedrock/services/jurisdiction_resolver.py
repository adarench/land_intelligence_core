"""Jurisdiction resolution using cached local GIS boundary geometries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree
from shapely.validation import make_valid


@dataclass(frozen=True)
class BoundarySource:
    jurisdiction: str
    path: str


_BOUNDARY_SOURCES: tuple[BoundarySource, ...] = (
    BoundarySource("Salt Lake City", "zoning_data_scraper/zoning_dataset_v8/salt-lake-city/zoning_layers.geojson"),
    BoundarySource("Lehi", "zoning_data_scraper/zoning_dataset_v8/lehi/zoning_layers.geojson"),
    BoundarySource("Draper", "zoning_data_scraper/zoning_data_priority_v9b/draper/zoning_layers.geojson"),
)
_UTAH_EXTENT = (-114.1, 36.9, -108.9, 42.5)
_FIXTURE_JURISDICTIONS = {
    "BenchmarkCounty_UT",
    "Cottonwood Heights",
    "Draper",
    "Example City",
    "Lehi",
    "Murray",
    "Provo",
    "SampleCounty_CA",
    "Salt Lake City",
    "Salt Lake County",
    "Test City",
    "test_county",
}


@dataclass
class _BoundaryRecord:
    jurisdiction: str
    geometry: BaseGeometry
    prepared: object


class JurisdictionResolver:
    """Loads jurisdiction boundary geometry once and resolves centroid containment."""

    def __init__(self, boundary_sources: Iterable[BoundarySource] | None = None) -> None:
        self._records = self._load_boundaries(tuple(boundary_sources or _BOUNDARY_SOURCES))
        self._tree = STRtree([record.geometry for record in self._records]) if self._records else None

    def resolve(self, centroid: list[float]) -> str | None:
        if len(centroid) != 2 or self._tree is None:
            return None

        point = Point(float(centroid[0]), float(centroid[1]))
        matches: list[_BoundaryRecord] = []
        for index in self._tree.query(point):
            record = self._records[int(index)]
            if record.prepared.contains(point) or record.prepared.covers(point):
                matches.append(record)
        if not matches:
            return None
        return min(matches, key=lambda item: item.geometry.area).jurisdiction

    def jurisdiction_names(self) -> list[str]:
        return [record.jurisdiction for record in self._records]

    def representative_point(self, jurisdiction: str) -> list[float] | None:
        for record in self._records:
            if record.jurisdiction == jurisdiction:
                point = record.geometry.representative_point()
                return [float(point.x), float(point.y)]
        return None

    @staticmethod
    def _load_boundaries(boundary_sources: tuple[BoundarySource, ...]) -> list[_BoundaryRecord]:
        repo_root = Path(__file__).resolve().parents[2]
        records: list[_BoundaryRecord] = []

        for source in boundary_sources:
            data_path = repo_root / source.path
            if not data_path.exists():
                continue
            features = json.loads(data_path.read_text()).get("features", [])
            components: list[BaseGeometry] = []
            for feature in features:
                geometry = feature.get("geometry")
                if not geometry:
                    continue
                try:
                    geom = shape(geometry)
                except Exception:
                    continue
                if geom.is_empty:
                    continue
                components.extend(_extract_boundary_components(_normalize_boundary_geometry(geom)))

            if not components:
                continue

            merged = unary_union(components)
            if merged.geom_type in {"LineString", "MultiLineString", "GeometryCollection"}:
                polygons = list(polygonize(merged))
                if not polygons:
                    continue
                merged = unary_union(polygons)

            if merged.is_empty:
                continue

            records.append(
                _BoundaryRecord(
                    jurisdiction=source.jurisdiction,
                    geometry=merged,
                    prepared=prep(merged),
                )
            )

        return records


def _extract_boundary_components(geometry: BaseGeometry) -> list[BaseGeometry]:
    if geometry.is_empty or not _intersects_utah_extent(geometry):
        return []
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return [geometry]
    if geometry.geom_type in {"LineString", "MultiLineString"}:
        return [geometry]
    if geometry.geom_type == "GeometryCollection":
        components: list[BaseGeometry] = []
        for item in geometry.geoms:
            components.extend(_extract_boundary_components(item))
        return components
    return []


def _normalize_boundary_geometry(geometry: BaseGeometry) -> BaseGeometry:
    if geometry.is_empty:
        return geometry
    if geometry.geom_type in {"Polygon", "MultiPolygon"} and not geometry.is_valid:
        repaired = make_valid(geometry)
        if not repaired.is_empty:
            geometry = repaired
    return geometry


def _intersects_utah_extent(geometry: BaseGeometry) -> bool:
    min_x, min_y, max_x, max_y = geometry.bounds
    utah_min_x, utah_min_y, utah_max_x, utah_max_y = _UTAH_EXTENT
    return not (max_x < utah_min_x or min_x > utah_max_x or max_y < utah_min_y or min_y > utah_max_y)


@lru_cache(maxsize=1)
def get_default_resolver() -> JurisdictionResolver:
    return JurisdictionResolver()


def resolve_jurisdiction(centroid: list[float]) -> str | None:
    return get_default_resolver().resolve(centroid)


def known_jurisdictions() -> set[str]:
    return set(get_default_resolver().jurisdiction_names()) | set(_FIXTURE_JURISDICTIONS)


def is_known_jurisdiction(jurisdiction: str) -> bool:
    normalized = str(jurisdiction).strip()
    return normalized in known_jurisdictions()
