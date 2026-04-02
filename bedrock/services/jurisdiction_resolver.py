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


def _discover_boundary_sources() -> tuple[BoundarySource, ...]:
    """Auto-discover all cities with normalized_zoning.json in dataset directories.

    Scans zoning_dataset_v* directories in descending version order.
    Higher versions take priority. Each city appears at most once.
    """
    repo_root = Path(__file__).resolve().parents[2]
    scraper_root = repo_root / "zoning_data_scraper"
    sources: list[BoundarySource] = []
    seen_slugs: set[str] = set()

    def _version_key(d: Path) -> tuple[int, str]:
        """Extract numeric version for sorting. zoning_dataset_v10 > v9b > v8."""
        import re
        match = re.search(r"v(\d+)", d.name)
        num = int(match.group(1)) if match else 0
        suffix = d.name.split(str(num))[-1] if match else d.name
        return (num, suffix)

    dataset_dirs = sorted(
        [d for d in scraper_root.iterdir() if d.is_dir() and d.name.startswith("zoning_dataset_v")],
        key=_version_key,
        reverse=True,
    )
    for dataset_dir in dataset_dirs:
        for city_dir in sorted(dataset_dir.iterdir()):
            if not city_dir.is_dir():
                continue
            nz = city_dir / "normalized_zoning.json"
            if not nz.exists():
                continue
            slug = city_dir.name
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            jurisdiction = slug.replace("-", " ").title()
            rel_path = str(nz.relative_to(repo_root))
            sources.append(BoundarySource(jurisdiction, rel_path))

    return tuple(sources)


_BOUNDARY_SOURCES: tuple[BoundarySource, ...] = _discover_boundary_sources()
_UTAH_EXTENT = (-114.1, 36.9, -108.9, 42.5)
_FIXTURE_JURISDICTIONS = {
    "BenchmarkCounty_UT",
    "Cottonwood Heights",
    "Draper",
    "Eagle Mountain",
    "Example City",
    "Herriman",
    "Lehi",
    "Murray",
    "Provo",
    "Riverton",
    "SampleCounty_CA",
    "Salt Lake City",
    "Salt Lake County",
    "Saratoga Springs",
    "South Jordan",
    "Test City",
    "West Valley City",
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
            raw = json.loads(data_path.read_text())
            features = raw.get("features", raw) if isinstance(raw, dict) else raw
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
