from __future__ import annotations

from dataclasses import dataclass

import pytest

from bedrock.contracts.parcel import Parcel
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.layout_service import LayoutSearchError, search_layout


@dataclass
class _Lot:
    area_sqft: float
    frontage_ft: float
    depth_ft: float


@dataclass
class _Result:
    metrics: dict
    lots: list[_Lot]


@dataclass
class _Candidate:
    geojson: dict
    result: _Result
    score: float


def _parcel() -> Parcel:
    return Parcel.model_validate(
        {
            "parcel_id": "rich-zoning-parcel-001",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [0.0, 0.0],
                    [0.0, 500.0],
                    [600.0, 500.0],
                    [600.0, 0.0],
                    [0.0, 0.0],
                ]],
            },
            "area_sqft": 300000.0,
            "centroid": [300.0, 250.0],
            "bounding_box": [0.0, 0.0, 600.0, 500.0],
            "jurisdiction": "BenchmarkCounty_UT",
            "utilities": [],
            "access_points": [],
            "topography": {},
            "existing_structures": [],
        }
    )


def _base_zoning(parcel: Parcel) -> dict:
    return {
        "parcel_id": parcel.parcel_id,
        "district": "R-1",
        "min_lot_size_sqft": 6000.0,
        "max_units_per_acre": 5.0,
        "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
    }


def test_max_block_length_constraint_filters_out_long_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel()
    zoning = ZoningRules.model_validate(
        {
            **_base_zoning(parcel),
            "standards": [
                {"id": "r1-block-max", "standard_type": "max_block_length_ft", "value": 120.0, "units": "ft"},
            ],
        }
    )

    violating = _Candidate(
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[10, 10], [10, 60], [60, 60], [60, 10], [10, 10]]]},
                    "properties": {"layer": "lots"},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[0, 20], [250, 20]]},
                    "properties": {"layer": "road"},
                },
            ],
        },
        result=_Result(metrics={"lot_count": 1, "total_road_ft": 250.0}, lots=[_Lot(area_sqft=7000.0, frontage_ft=100.0, depth_ft=70.0)]),
        score=0.95,
    )
    passing = _Candidate(
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[10, 10], [10, 60], [60, 60], [60, 10], [10, 10]]]},
                    "properties": {"layer": "lots"},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[0, 20], [100, 20]]},
                    "properties": {"layer": "road"},
                },
            ],
        },
        result=_Result(metrics={"lot_count": 1, "total_road_ft": 100.0}, lots=[_Lot(area_sqft=7000.0, frontage_ft=100.0, depth_ft=70.0)]),
        score=0.80,
    )

    monkeypatch.setattr("bedrock.services.layout_service.run_layout_search", lambda **_: [violating, passing])
    result = search_layout(parcel, zoning, max_candidates=8)
    assert result.road_length_ft == 100.0


def test_road_access_required_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel()
    zoning = ZoningRules.model_validate(
        {
            **_base_zoning(parcel),
            "standards": [
                {"id": "r1-access", "standard_type": "road_access_required", "value": True},
            ],
        }
    )

    no_access = _Candidate(
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[300, 300], [300, 350], [350, 350], [350, 300], [300, 300]]]},
                    "properties": {"layer": "lots"},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[10, 20], [120, 20]]},
                    "properties": {"layer": "road"},
                },
            ],
        },
        result=_Result(metrics={"lot_count": 1, "total_road_ft": 110.0}, lots=[_Lot(area_sqft=7000.0, frontage_ft=100.0, depth_ft=70.0)]),
        score=0.90,
    )
    has_access = _Candidate(
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[10, 10], [10, 60], [60, 60], [60, 10], [10, 10]]]},
                    "properties": {"layer": "lots"},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[0, 10], [100, 10]]},
                    "properties": {"layer": "road"},
                },
            ],
        },
        result=_Result(metrics={"lot_count": 1, "total_road_ft": 100.0}, lots=[_Lot(area_sqft=7000.0, frontage_ft=100.0, depth_ft=70.0)]),
        score=0.80,
    )
    monkeypatch.setattr("bedrock.services.layout_service.run_layout_search", lambda **_: [no_access, has_access])
    result = search_layout(parcel, zoning, max_candidates=8)
    assert result.road_length_ft == 100.0


def test_easement_and_frontage_bounds_can_make_constraints_infeasible(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel()
    zoning = ZoningRules.model_validate(
        {
            **_base_zoning(parcel),
            "standards": [
                {"id": "r1-frontage-max", "standard_type": "frontage_max_ft", "value": 60.0, "units": "ft"},
                {"id": "r1-easement", "standard_type": "easement_buffer_ft", "value": 20.0, "units": "ft"},
            ],
        }
    )

    constrained_out = _Candidate(
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[5, 5], [5, 65], [65, 65], [65, 5], [5, 5]]]},
                    "properties": {"layer": "lots"},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[0, 10], [100, 10]]},
                    "properties": {"layer": "road"},
                },
            ],
        },
        result=_Result(metrics={"lot_count": 1, "total_road_ft": 100.0}, lots=[_Lot(area_sqft=7000.0, frontage_ft=85.0, depth_ft=90.0)]),
        score=0.90,
    )

    monkeypatch.setattr("bedrock.services.layout_service.run_layout_search", lambda **_: [constrained_out])
    with pytest.raises(LayoutSearchError) as exc:
        search_layout(parcel, zoning, max_candidates=8)
    assert exc.value.code == "no_viable_layout"
