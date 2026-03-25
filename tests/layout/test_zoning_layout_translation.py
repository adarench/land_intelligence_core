from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from bedrock.contracts.parcel import Parcel
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.layout_service import LayoutSearchError, search_layout
from bedrock.services.zoning_layout_translation import translate_zoning_for_layout


@dataclass
class _FakeResult:
    metrics: dict
    lots: list


@dataclass
class _FakeCandidate:
    geojson: dict
    result: _FakeResult
    score: float


def _parcel() -> Parcel:
    return Parcel.model_validate(
        {
            "parcel_id": "translation-parcel-001",
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


def _candidate() -> list[_FakeCandidate]:
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
            result=_FakeResult(metrics={"lot_count": 1, "total_road_ft": 150.0}, lots=[]),
            score=0.7,
        )
    ]


def test_translation_layout_safe() -> None:
    parcel = _parcel()
    zoning = ZoningRules.model_validate(
        {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "min_lot_size_sqft": 6000.0,
            "max_units_per_acre": 5.0,
            "min_frontage_ft": 50.0,
            "road_right_of_way_ft": 50.0,
            "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
        }
    )

    translated = translate_zoning_for_layout(parcel, zoning)

    assert translated.usability_class == "layout_safe"
    assert translated.zoning is not None
    assert translated.degraded_fields == ()
    assert translated.issues == ()


def test_translation_partially_usable_applies_degraded_safe_mapping() -> None:
    parcel = _parcel()
    zoning = ZoningRules.model_validate(
        {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "min_lot_size_sqft": 6000.0,
            "max_units_per_acre": 5.0,
            "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
        }
    )

    translated = translate_zoning_for_layout(parcel, zoning)

    assert translated.usability_class == "partially_usable"
    assert translated.zoning is not None
    assert "min_frontage_ft" in translated.degraded_fields
    assert "road_right_of_way_ft" in translated.degraded_fields
    assert translated.zoning.min_frontage_ft is not None
    assert translated.zoning.road_right_of_way_ft == 32.0


def test_translation_non_usable_returns_structured_issues() -> None:
    parcel = _parcel()
    zoning = ZoningRules.model_validate(
        {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
        }
    )

    translated = translate_zoning_for_layout(parcel, zoning)

    assert translated.usability_class == "non_usable"
    assert translated.zoning is None
    assert any(issue.field == "min_lot_size_sqft" for issue in translated.issues)
    assert any(issue.field == "max_units_per_acre" for issue in translated.issues)


def test_search_layout_accepts_partially_usable_zoning(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel()
    zoning = ZoningRules.model_validate(
        {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "min_lot_size_sqft": 6000.0,
            "max_units_per_acre": 5.0,
            "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
        }
    )
    monkeypatch.setattr("bedrock.services.layout_service.run_layout_search", lambda **_: _candidate())

    result = search_layout(parcel, zoning, max_candidates=8)

    assert result.unit_count == 1
    assert result.road_length_ft == 150.0


def test_search_layout_non_usable_zoning_raises_structured_error() -> None:
    parcel = _parcel()
    zoning = ZoningRules.model_validate(
        {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
        }
    )

    with pytest.raises(LayoutSearchError) as exc:
        search_layout(parcel, zoning, max_candidates=8)

    assert exc.value.code == "non_usable_zoning"
    payload = json.loads(exc.value.message)
    assert payload["reason"] == "zoning_not_layout_usable"
    assert payload["usability_class"] == "non_usable"
    assert any(issue["field"] == "min_lot_size_sqft" for issue in payload["issues"])


def test_translation_extracts_layout_control_aliases_from_standards() -> None:
    parcel = _parcel()
    zoning = ZoningRules.model_validate(
        {
            "parcel_id": parcel.parcel_id,
            "district": "R-1",
            "min_lot_size_sqft": 6000.0,
            "max_units_per_acre": 5.0,
            "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
            "standards": [
                {"id": "lot-frontage", "standard_type": "lot_frontage_ft", "value": 62.0, "units": "ft"},
                {"id": "block-depth", "standard_type": "block_depth_ft", "value": 120.0, "units": "ft"},
            ],
        }
    )

    translated = translate_zoning_for_layout(parcel, zoning)

    assert translated.usability_class in {"layout_safe", "partially_usable"}
    assert translated.additional_constraints["lot_frontage_ft"] == 62.0
    assert translated.additional_constraints["block_depth_ft"] == 120.0
