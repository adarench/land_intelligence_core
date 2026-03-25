"""CAD export bridge for canonical Bedrock layout results."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from shapely.geometry import LineString, MultiLineString, shape
from shapely.ops import transform

from bedrock.contracts.layout_result import LayoutResult
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.layout_service import _geometry_to_local_feet

from GIS_lot_layout_optimizer.ai_subdivision import dxf_export
from GIS_lot_layout_optimizer.ai_subdivision.geojson_export import layout_to_geojson_bytes
from GIS_lot_layout_optimizer.ai_subdivision.geometry import (
    Polygon2D,
    RoadPlan,
    export_layout_to_cadquery_step,
    geometry_to_polygon_list,
)
from GIS_lot_layout_optimizer.ai_subdivision.subdivision import LayoutData, LotLabel
from GIS_lot_layout_optimizer.ai_subdivision.street_network import StreetNetworkCandidate

ExportFormat = Literal["dxf", "step", "geojson"]
EXPORT_ROOT = Path(__file__).resolve().parents[1] / "exports"


class LayoutExportError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LayoutExportArtifact:
    path: Path
    filename: str
    media_type: str
    export_format: ExportFormat


def export_layout_artifact(
    parcel: Parcel,
    layout: LayoutResult,
    *,
    export_format: ExportFormat = "dxf",
    zoning: ZoningRules | None = None,
) -> LayoutExportArtifact:
    if layout.parcel_id != parcel.parcel_id:
        raise LayoutExportError(
            "layout_export_contract_mismatch",
            "LayoutResult.parcel_id must match Parcel.parcel_id for export.",
        )

    export_layout = _to_export_layout(parcel, layout, zoning=zoning)
    export_dir = EXPORT_ROOT / layout.layout_id
    export_dir.mkdir(parents=True, exist_ok=True)

    if export_format == "dxf":
        path = export_dir / "subdivision_layout.dxf"
        dxf_export._export_ascii_dxf(export_layout, output_path=str(path))
        return LayoutExportArtifact(
            path=path,
            filename=path.name,
            media_type="application/dxf",
            export_format=export_format,
        )

    if export_format == "geojson":
        path = export_dir / "subdivision_layout.geojson"
        path.write_bytes(layout_to_geojson_bytes(export_layout))
        return LayoutExportArtifact(
            path=path,
            filename=path.name,
            media_type="application/geo+json",
            export_format=export_format,
        )

    if export_format == "step":
        path = export_dir / "subdivision_layout.step"
        try:
            export_layout_to_cadquery_step(export_layout, path=str(path))
        except RuntimeError as exc:
            raise LayoutExportError("step_export_unavailable", str(exc)) from exc
        return LayoutExportArtifact(
            path=path,
            filename=path.name,
            media_type="application/step",
            export_format=export_format,
        )

    raise LayoutExportError("unsupported_export_format", f"Unsupported export format: {export_format}")


def _to_export_layout(parcel: Parcel, layout: LayoutResult, zoning: ZoningRules | None = None) -> LayoutData:
    _, projection = _geometry_to_local_feet(parcel.geometry)

    parcel_polygons = _geometry_list_to_polygons([parcel.geometry], projection)
    lot_polygons = _sort_polygons(_geometry_list_to_polygons(layout.lot_geometries, projection))
    road_centerlines = _sort_lines(_geometry_list_to_lines(layout.road_geometries, projection))
    road_width_ft = float(zoning.road_right_of_way_ft) if zoning and zoning.road_right_of_way_ft is not None else 32.0
    road_polygons = _sort_polygons(
        _geometry_list_to_road_polygons(layout.road_geometries, projection=projection, road_width_ft=road_width_ft)
    )
    lot_labels = [
        LotLabel(text=f"LOT_{index}", position=polygon.label_point())
        for index, polygon in enumerate(lot_polygons, start=1)
    ]

    return LayoutData(
        parcel=parcel_polygons,
        road=road_polygons,
        lots=lot_polygons,
        easements=[],
        lot_labels=lot_labels,
        road_plan=RoadPlan(orientation="export"),
        street_network=StreetNetworkCandidate(
            topology="export",
            centerlines=road_centerlines,
            corridors=[],
            orientation="export",
            metadata={"road_width_ft": road_width_ft},
        ),
        optimized=True,
    )


def _geometry_list_to_polygons(geometries: list[dict], projection: dict[str, float]) -> list[Polygon2D]:
    polygons: list[Polygon2D] = []
    for geometry in geometries:
        local_geometry = _to_local_geometry(geometry, projection)
        for polygon in geometry_to_polygon_list(local_geometry):
            polygons.append(Polygon2D(tuple((float(x), float(y)) for x, y in polygon.exterior.coords[:-1])))
    return polygons


def _geometry_list_to_lines(geometries: list[dict], projection: dict[str, float]) -> list[LineString]:
    lines: list[LineString] = []
    for geometry in geometries:
        local_geometry = _to_local_geometry(geometry, projection)
        if isinstance(local_geometry, LineString):
            if local_geometry.length > 0:
                lines.append(local_geometry)
        elif isinstance(local_geometry, MultiLineString):
            lines.extend(segment for segment in local_geometry.geoms if segment.length > 0)
    return lines


def _geometry_list_to_road_polygons(
    geometries: list[dict],
    *,
    projection: dict[str, float],
    road_width_ft: float,
) -> list[Polygon2D]:
    polygons: list[Polygon2D] = []
    half_width_ft = road_width_ft / 2.0
    for geometry in geometries:
        local_geometry = _to_local_geometry(geometry, projection)
        if isinstance(local_geometry, LineString):
            polygons.extend(_line_to_segment_polygons(local_geometry, half_width_ft))
            continue
        if isinstance(local_geometry, MultiLineString):
            for line in local_geometry.geoms:
                polygons.extend(_line_to_segment_polygons(line, half_width_ft))
            continue
        for polygon in geometry_to_polygon_list(local_geometry):
            polygons.append(Polygon2D(tuple((float(x), float(y)) for x, y in polygon.exterior.coords[:-1])))
    return polygons


def _line_to_segment_polygons(line: LineString, half_width_ft: float) -> list[Polygon2D]:
    if half_width_ft <= 0.0:
        return []

    coords = list(line.coords)
    polygons: list[Polygon2D] = []
    for start, end in zip(coords, coords[1:]):
        start_x, start_y = float(start[0]), float(start[1])
        end_x, end_y = float(end[0]), float(end[1])
        dx = end_x - start_x
        dy = end_y - start_y
        length = math.hypot(dx, dy)
        if length <= 0.0:
            continue
        offset_x = (-dy / length) * half_width_ft
        offset_y = (dx / length) * half_width_ft
        polygons.append(
            Polygon2D(
                (
                    (start_x + offset_x, start_y + offset_y),
                    (end_x + offset_x, end_y + offset_y),
                    (end_x - offset_x, end_y - offset_y),
                    (start_x - offset_x, start_y - offset_y),
                )
            )
        )
    return polygons


def _to_local_geometry(geometry: dict, projection: dict[str, float]):
    parsed = shape(geometry)
    if projection["feet_per_degree_lng"] == 1.0 and projection["feet_per_degree_lat"] == 1.0:
        return parsed

    origin_lng = float(projection["origin_lng"])
    origin_lat = float(projection["origin_lat"])
    feet_per_degree_lng = float(projection["feet_per_degree_lng"])
    feet_per_degree_lat = float(projection["feet_per_degree_lat"])

    return transform(
        lambda x, y, z=None: (
            (x - origin_lng) * feet_per_degree_lng,
            (y - origin_lat) * feet_per_degree_lat,
        ),
        parsed,
    )


def _sort_polygons(polygons: list[Polygon2D]) -> list[Polygon2D]:
    return sorted(polygons, key=lambda polygon: _polygon_sort_key(polygon))


def _sort_lines(lines: list[LineString]) -> list[LineString]:
    return sorted(lines, key=lambda line: _line_sort_key(line))


def _polygon_sort_key(polygon: Polygon2D) -> str:
    normalized_points = tuple((round(float(x), 6), round(float(y), 6)) for x, y in polygon.points)
    return json.dumps(normalized_points, separators=(",", ":"))


def _line_sort_key(line: LineString) -> str:
    normalized_points = tuple((round(float(x), 6), round(float(y), 6)) for x, y in line.coords)
    return json.dumps(normalized_points, separators=(",", ":"))
