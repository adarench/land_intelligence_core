from __future__ import annotations

from dataclasses import dataclass

import pytest

from bedrock.contracts.parcel import Parcel
from bedrock.contracts.validators import build_zoning_rules_from_lookup
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.layout_service import search_layout


@dataclass
class _FakeResult:
    metrics: dict
    lots: list


@dataclass
class _FakeCandidate:
    geojson: dict
    result: _FakeResult
    score: float


def _parcel_polygon() -> Parcel:
    return Parcel.model_validate(
        {
            "parcel_id": "compat-parcel-001",
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


def _parcel_multipolygon() -> Parcel:
    return Parcel.model_validate(
        {
            "parcel_id": "compat-parcel-multi-001",
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[
                        [0.0, 0.0],
                        [0.0, 100.0],
                        [100.0, 100.0],
                        [100.0, 0.0],
                        [0.0, 0.0],
                    ]],
                    [[
                        [200.0, 200.0],
                        [200.0, 700.0],
                        [900.0, 700.0],
                        [900.0, 200.0],
                        [200.0, 200.0],
                    ]],
                ],
            },
            "area_sqft": 360000.0,
            "centroid": [550.0, 450.0],
            "bounding_box": [0.0, 0.0, 900.0, 700.0],
            "jurisdiction": "BenchmarkCounty_UT",
            "utilities": [],
            "access_points": [],
            "topography": {},
            "existing_structures": [],
        }
    )


def _fake_candidates() -> list[_FakeCandidate]:
    return [
        _FakeCandidate(
            geojson={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[
                                [0.0, 0.0],
                                [0.0, 150.0],
                                [150.0, 150.0],
                                [150.0, 0.0],
                                [0.0, 0.0],
                            ]],
                        },
                        "properties": {"layer": "lots"},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [150.0, 0.0]]},
                        "properties": {"layer": "road"},
                    },
                ],
            },
            result=_FakeResult(
                metrics={"lot_count": 1, "total_road_ft": 150.0, "avg_lot_area_sqft": 22500.0},
                lots=[],
            ),
            score=0.63,
        )
    ]


@pytest.mark.parametrize(
    "lookup_payload",
    [
        {
            "jurisdiction": "BenchmarkCounty_UT",
            "district": "R-1",
            "rules": {
                "district": "R-1",
                "min_lot_size_sqft": 6000.0,
                "max_units_per_acre": 5.0,
                "min_frontage_ft": 50.0,
                "road_right_of_way_ft": 50.0,
                "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
            },
        },
        {
            "jurisdiction": "BenchmarkCounty_UT",
            "district": "R-1",
            "rules": {
                "district": "R-1",
                "min_lot_size_sqft": 6000.0,
                "max_units_per_acre": 5.0,
                "min_lot_width_ft": 50.0,
                "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                "standards": [
                    {"id": "r1-road-row", "standard_type": "road_right_of_way_ft", "value": 50.0, "units": "ft"},
                ],
            },
        },
        {
            "jurisdiction": "BenchmarkCounty_UT",
            "district": "R-1",
            "rules": {
                "district": "R-1",
                "standards": [
                    {"id": "r1-min-lot", "standard_type": "min_lot_size_sqft", "value": 6000.0, "units": "sqft"},
                    {"id": "r1-density", "standard_type": "max_units_per_acre", "value": 5.0, "units": "du/ac"},
                    {"id": "r1-front", "standard_type": "front_setback_ft", "value": 25.0, "units": "ft"},
                    {"id": "r1-side", "standard_type": "side_setback_ft", "value": 8.0, "units": "ft"},
                    {"id": "r1-rear", "standard_type": "rear_setback_ft", "value": 20.0, "units": "ft"},
                    {"id": "r1-frontage", "standard_type": "min_frontage_ft", "value": 50.0, "units": "ft"},
                ],
            },
        },
    ],
)
def test_layout_search_accepts_valid_zoning_interpretations(monkeypatch: pytest.MonkeyPatch, lookup_payload: dict) -> None:
    parcel = _parcel_polygon()
    zoning = build_zoning_rules_from_lookup(parcel, lookup_payload)
    monkeypatch.setattr("bedrock.services.layout_service.run_layout_search", lambda **_: _fake_candidates())

    result = search_layout(parcel, zoning, max_candidates=8)

    assert result.unit_count == 1
    assert result.road_length_ft == 150.0
    assert result.layout_id.startswith(f"layout-{parcel.parcel_id}-")


def test_layout_search_accepts_valid_multipolygon_parcel(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel_multipolygon()
    zoning = ZoningRules.model_validate(
        {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "min_lot_size_sqft": 6000.0,
            "max_units_per_acre": 5.0,
            "min_frontage_ft": 50.0,
            "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
        }
    )
    monkeypatch.setattr("bedrock.services.layout_service.run_layout_search", lambda **_: _fake_candidates())

    result = search_layout(parcel, zoning, max_candidates=8)

    assert result.unit_count == 1
    assert len(result.lot_geometries) == 1


def test_layout_search_is_deterministic_for_same_input(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel_polygon()
    zoning = ZoningRules.model_validate(
        {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "min_lot_size_sqft": 6000.0,
            "max_units_per_acre": 5.0,
            "min_frontage_ft": 50.0,
            "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
        }
    )
    monkeypatch.setattr("bedrock.services.layout_service.run_layout_search", lambda **_: _fake_candidates())

    first = search_layout(parcel, zoning, max_candidates=8)
    second = search_layout(parcel, zoning, max_candidates=8)

    assert first.layout_id == second.layout_id
    assert first.unit_count == second.unit_count
    assert first.road_length_ft == second.road_length_ft
    assert first.lot_geometries == second.lot_geometries
    assert first.road_geometries == second.road_geometries
