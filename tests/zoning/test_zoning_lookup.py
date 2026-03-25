from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))

from bedrock.api.zoning_api import create_app
from bedrock.contracts.parcel import Parcel
from bedrock.services.zoning_rule_normalizer import normalize_rules
from bedrock.services.zoning_service import (
    AmbiguousZoningMatchError,
    IncompleteZoningRulesError,
    InvalidZoningRulesError,
    NoZoningMatchError,
    Setbacks,
    ZoningLookupResult,
    ZoningService,
    ZoningRules,
    normalize_zoning_rules as normalize_service_rules,
)
from zoning_data_scraper.services import zoning_code_rules as zoning_code_rules_module
from zoning_data_scraper.services.zoning_overlay import OverlayMatch
from zoning_data_scraper.services import rule_normalization as rule_normalization_module
from zoning_data_scraper.services import zoning_overlay as zoning_overlay_module
from zoning_data_scraper.services.zoning_overlay import lookup_zoning_district


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _geojson_polygon(coords: list[tuple[float, float]]) -> dict:
    return {"type": "Polygon", "coordinates": [[list(point) for point in coords]]}


@pytest.fixture
def dataset_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dataset_root = tmp_path / "zoning_dataset_sample"
    monkeypatch.setattr(zoning_code_rules_module, "NORMALIZED_RULES_ROOT", tmp_path / "normalized_rules")
    for city_slug, city_name in (
        ("salt-lake-city", "Salt Lake City"),
        ("lehi", "Lehi"),
        ("draper", "Draper"),
    ):
        city_dir = dataset_root / city_slug
        _write_json(
            city_dir / "metadata.json",
            {
                "city": city_name,
                "city_slug": city_slug,
                "county_name": "Salt Lake" if city_slug != "lehi" else "Utah",
                "feature_count": 2,
            },
        )

    _write_json(
        dataset_root / "salt-lake-city" / "normalized_zoning.json",
        [
            {
                "city": "Salt Lake City",
                "zoning_code": "R-1-7000",
                "zoning_name": "Single Family Residential",
                "density": "4 du/ac",
                "source_layer": "slc-zoning",
                "geometry": _geojson_polygon([(-111.92, 40.75), (-111.92, 40.77), (-111.90, 40.77), (-111.90, 40.75), (-111.92, 40.75)]),
            },
            {
                "city": "Salt Lake City",
                "zoning_code": "RMF-35",
                "zoning_name": "Residential Multi-Family",
                "density": "12 du/ac",
                "source_layer": "slc-zoning",
                "geometry": _geojson_polygon([(-111.90, 40.75), (-111.90, 40.77), (-111.88, 40.77), (-111.88, 40.75), (-111.90, 40.75)]),
            },
        ],
    )
    _write_json(
        dataset_root / "lehi" / "normalized_zoning.json",
        [
            {
                "city": "Lehi",
                "zoning_code": "R-1-22",
                "zoning_name": "Residential",
                "density": "3 du/ac",
                "source_layer": "lehi-zoning",
                "geometry": _geojson_polygon([(-111.89, 40.38), (-111.89, 40.40), (-111.86, 40.40), (-111.86, 40.38), (-111.89, 40.38)]),
            },
            {
                "city": "Lehi",
                "zoning_code": "TH-5",
                "zoning_name": "Townhome",
                "density": "5 du/ac",
                "source_layer": "lehi-zoning",
                "geometry": _geojson_polygon([(-111.86, 40.38), (-111.86, 40.40), (-111.83, 40.40), (-111.83, 40.38), (-111.86, 40.38)]),
            },
        ],
    )
    _write_json(
        dataset_root / "draper" / "normalized_zoning.json",
        [
            {
                "city": "Draper",
                "zoning_code": "R3",
                "zoning_name": "Residential",
                "density": "4 du/ac",
                "source_layer": "draper-zoning",
                "geometry": _geojson_polygon([(-111.88, 40.49), (-111.88, 40.51), (-111.85, 40.51), (-111.85, 40.49), (-111.88, 40.49)]),
            },
            {
                "city": "Draper",
                "zoning_code": "C-2",
                "zoning_name": "Commercial",
                "density": "10 du/ac",
                "source_layer": "draper-zoning",
                "geometry": _geojson_polygon([(-111.85, 40.49), (-111.85, 40.51), (-111.82, 40.51), (-111.82, 40.49), (-111.85, 40.49)]),
            },
        ],
    )
    _write_json(
        dataset_root / "draper" / "overlay_layers.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Hillside Overlay"},
                    "geometry": _geojson_polygon([(-111.879, 40.491), (-111.879, 40.509), (-111.851, 40.509), (-111.851, 40.491), (-111.879, 40.491)]),
                }
            ],
        },
    )
    _write_json(
        dataset_root / "draper" / "district_rules.json",
        {
            "R3": {
                "district": "R3",
                "min_lot_size_sqft": 8000,
                "max_units_per_acre": 4,
                "setbacks": {"front": 25, "side": 8, "rear": 20},
                "height_limit_ft": 35,
                "lot_coverage_max": 45,
                "allowed_use_types": ["single_family_residential"],
            },
            "C-2": {
                "district": "C-2",
                "minimum_lot_size_sqft": 2500,
                "density": 10,
                "front_setback_ft": 10,
                "side_setback_ft": 0,
                "rear_setback_ft": 10,
                "height_limit": 50,
                "lot_coverage_limit": 0.75,
                "allowed_use_types": ["commercial", "mixed_use"],
            }
        },
    )

    zoning_overlay_module._dataset_info.cache_clear()
    zoning_overlay_module._zoning_features.cache_clear()
    zoning_overlay_module._overlay_features.cache_clear()
    zoning_overlay_module._build_strtree.cache_clear()
    zoning_overlay_module._filtered_zoning_rows.cache_clear()
    zoning_overlay_module._layer_source_index.cache_clear()
    rule_normalization_module._load_rule_index.cache_clear()
    rule_normalization_module._load_rule_index_with_sources.cache_clear()
    zoning_code_rules_module._load_normalized_rule_index.cache_clear()
    zoning_code_rules_module._load_normalized_rules_document.cache_clear()
    return tmp_path


def _parcel(parcel_id: str, jurisdiction: str, coords: list[tuple[float, float]], *, centroid: list[float] | None = None) -> Parcel:
    return Parcel(
        parcel_id=parcel_id,
        geometry=_geojson_polygon(coords),
        area_sqft=100000,
        centroid=centroid,
        bounding_box=None,
        jurisdiction=jurisdiction,
        zoning_district=None,
        utilities=[],
        access_points=[],
        topography={},
        existing_structures=[],
    )


def _real_dataset_parcel(jurisdiction: str, district: str, dataset_path: Path, *, delta: float = 0.00008) -> Parcel:
    rows = json.loads(dataset_path.read_text())
    for row in rows:
        if row.get("zoning_code") != district:
            continue
        geometry = shape(row["geometry"])
        point = geometry.representative_point()
        return _parcel(
            f"real-{jurisdiction.lower().replace(' ', '-')}-{district.lower()}",
            jurisdiction,
            [
                (point.x - delta, point.y - delta),
                (point.x - delta, point.y + delta),
                (point.x + delta, point.y + delta),
                (point.x + delta, point.y - delta),
                (point.x - delta, point.y - delta),
            ],
        )
    raise AssertionError(f"Missing district {district!r} in {dataset_path}")


def test_service_uses_real_overlay_lookup(dataset_root: Path) -> None:
    parcel = _parcel("draper-r3", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])

    result = ZoningService(dataset_root=dataset_root).lookup(parcel)

    assert isinstance(result, ZoningLookupResult)
    assert result.jurisdiction == "Draper"
    assert result.district == "R3"
    assert isinstance(result.rules, ZoningRules)
    assert result.rules.parcel_id == "draper-r3"
    assert result.rules.district == "R3"


def test_service_extracts_and_normalizes_rules(dataset_root: Path) -> None:
    parcel = _parcel("draper-r3", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])

    rules = ZoningService(dataset_root=dataset_root).lookup(parcel).rules

    assert rules.min_lot_size_sqft == 8000
    assert rules.max_units_per_acre == 4
    assert rules.setbacks.front == 25
    assert rules.setbacks.side == 8
    assert rules.setbacks.rear == 20
    assert rules.height_limit_ft == 35
    assert rules.lot_coverage_max == 0.45
    assert rules.min_frontage_ft is None
    assert rules.road_right_of_way_ft is None
    assert rules.allowed_uses == ["single_family_residential"]
    assert "Hillside Overlay" in rules.overlays


def test_service_loads_complete_rules_from_normalized_rules_store(dataset_root: Path) -> None:
    normalized_root = dataset_root / "normalized_rules"
    _write_json(
        normalized_root / "lehi.json",
        {
            "jurisdiction": "Lehi",
            "jurisdiction_slug": "lehi",
            "districts": {
                "R-1-22": {
                    "district": "R-1-22",
                    "aliases": ["Residential"],
                    "min_lot_size_sqft": 22000,
                    "max_units_per_acre": 1.98,
                    "setbacks": {"front": 30, "side": 10, "rear": 25},
                    "height_limit_ft": 35,
                    "lot_coverage_max": 0.35,
                    "allowed_uses": ["single_family_residential"],
                }
            },
        },
    )
    city_dir = dataset_root / "zoning_dataset_sample" / "lehi"
    if (city_dir / "district_rules.json").exists():
        (city_dir / "district_rules.json").unlink()
    zoning_code_rules_module._load_normalized_rule_index.cache_clear()
    zoning_code_rules_module._load_normalized_rules_document.cache_clear()
    parcel = _parcel("lehi-r1", "Lehi", [(-111.888, 40.385), (-111.888, 40.387), (-111.886, 40.387), (-111.886, 40.385), (-111.888, 40.385)])

    rules = ZoningService(dataset_root=dataset_root).lookup(parcel).rules

    assert rules.district == "R-1-22"
    assert rules.min_lot_size_sqft == 22000.0
    assert rules.max_units_per_acre == 1.98
    assert rules.setbacks == Setbacks(front=30.0, side=10.0, rear=25.0)
    assert rules.height_limit_ft == 35.0
    assert rules.lot_coverage_max == 0.35


def test_api_returns_canonical_zoning_rules(dataset_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ZoningService(dataset_root=dataset_root)
    monkeypatch.setattr("bedrock.api.zoning_api.ZoningService", lambda: service)
    client = TestClient(create_app())
    parcel = _parcel("draper-r3-api", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])

    response = client.post("/zoning/lookup", json={"parcel": parcel.model_dump()})

    assert response.status_code == 200
    body = response.json()
    assert body["jurisdiction"] == "Draper"
    assert body["district"] == "R3"
    assert body["parcel_id"] == "draper-r3-api"
    assert body["district"] == "R3"
    assert body["min_lot_size_sqft"] == 8000.0
    assert body["max_units_per_acre"] == 4.0
    assert body["setbacks"]["front"] == 25.0
    assert body["setbacks"]["side"] == 8.0
    assert body["setbacks"]["rear"] == 20.0
    assert body["height_limit_ft"] == 35.0
    assert body["lot_coverage_max"] == 0.45
    assert body["min_frontage_ft"] is None
    assert body["road_right_of_way_ft"] is None
    assert body["allowed_uses"] == ["single_family_residential"]
    assert set(body) >= {
        "parcel_id",
        "jurisdiction",
        "district",
        "min_lot_size_sqft",
        "max_units_per_acre",
        "setbacks",
        "height_limit_ft",
        "min_frontage_ft",
        "road_right_of_way_ft",
        "lot_coverage_max",
        "allowed_uses",
    }
    assert "max_height" not in body
    assert "max_lot_coverage" not in body


def test_api_normalizes_direct_parcel_input_before_lookup(
    dataset_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Parcel] = []
    service = ZoningService(dataset_root=dataset_root)

    class _CapturingService:
        def lookup(self, parcel: Parcel):
            seen.append(parcel)
            return service.lookup(parcel)

    monkeypatch.setattr("bedrock.api.zoning_api.ZoningService", lambda: _CapturingService())
    client = TestClient(create_app())

    response = client.post(
        "/zoning/lookup",
        json={
            "parcel": {
                "parcel_id": "draper-r3-bowtie",
                "geometry": _geojson_polygon(
                    [
                        (-111.8780, 40.4950),
                        (-111.8760, 40.4970),
                        (-111.8780, 40.4970),
                        (-111.8760, 40.4950),
                        (-111.8780, 40.4950),
                    ]
                ),
                "area_sqft": 1.0,
                "centroid": [-111.8770, 40.4960],
                "bounding_box": [-111.8780, 40.4950, -111.8760, 40.4970],
                "jurisdiction": "Draper",
                "zoning_district": None,
                "utilities": [],
                "access_points": [],
                "topography": {},
                "existing_structures": [],
            }
        },
    )

    assert response.status_code == 200
    assert len(seen) == 1
    assert seen[0].geometry["type"] in {"Polygon", "MultiPolygon"}
    assert seen[0].area_sqft > 0


def test_real_lookup_jurisdiction_does_not_synthesize_repairs_for_real_data_jurisdictions(
    dataset_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ZoningService(dataset_root=dataset_root)
    monkeypatch.setattr("bedrock.api.zoning_api.ZoningService", lambda: service)
    client = TestClient(create_app())
    parcel = _parcel("draper-c2", "Draper", [(-111.848, 40.495), (-111.848, 40.497), (-111.846, 40.497), (-111.846, 40.495), (-111.848, 40.495)])

    response = client.post("/zoning/lookup", json={"parcel": parcel.model_dump()})

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "incomplete_zoning_rules"
    assert body["district"] == "C-2"
    assert "setbacks.side" in body["missing_fields"]
    assert body["synthetic_fallback_used"] is False


def test_normalize_zoning_rules_fills_missing_fields_with_nulls() -> None:
    parcel = _parcel("normalize-null", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])

    rules = normalize_service_rules(
        {
            "min_lot_size_sqft": "8000",
            "max_units_per_acre": 4,
            "setbacks": {"front": 25, "side": 8, "rear": 20},
        },
        parcel=parcel,
        jurisdiction="Draper",
        district="R3",
    )

    assert isinstance(rules, ZoningRules)
    assert rules.min_lot_size_sqft == 8000.0
    assert rules.max_units_per_acre == 4.0
    assert rules.height_limit_ft is None
    assert rules.min_frontage_ft is None
    assert rules.road_right_of_way_ft is None
    assert rules.lot_coverage_max is None
    assert rules.allowed_uses == []


def test_normalize_zoning_rules_sanitizes_malformed_values() -> None:
    parcel = _parcel("normalize-bad", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])

    rules = normalize_service_rules(
        {
            "min_lot_size_sqft": "bad",
            "max_units_per_acre": "10",
            "setbacks": {"front": -1, "side": "x", "rear": 15},
            "height_limit": -2,
            "allowed_use_types": "single_family, duplex",
        },
        parcel=parcel,
        jurisdiction="Draper",
        district="R3",
    )

    assert rules.min_lot_size_sqft is None
    assert rules.max_units_per_acre == 10.0
    assert rules.setbacks == Setbacks(front=None, side=None, rear=15.0)
    assert rules.height_limit_ft is None
    assert rules.allowed_uses == ["single_family", "duplex"]


def test_rule_normalizer_maps_variant_names_and_units() -> None:
    parcel = _parcel("normalize-variants", "Eagle Mountain", [(-111.95, 40.31), (-111.95, 40.32), (-111.94, 40.32), (-111.94, 40.31), (-111.95, 40.31)])

    rules = normalize_rules(
        {
            "district": "R-1",
            "minimum parcel area": "0.25 acres",
            "front yard setback": "25 ft",
            "maximum dwelling units per acre": "4 du/ac",
            "maximum lot coverage": "45%",
            "maximum height": "35 feet",
        },
        parcel=parcel,
    )

    assert rules.district == "R-1"
    assert rules.min_lot_size_sqft == 10890.0
    assert rules.max_units_per_acre == 4.0
    assert rules.setbacks.front == 25.0
    assert rules.setbacks.side is None
    assert rules.setbacks.rear is None
    assert rules.lot_coverage_max == 0.45
    assert rules.height_limit_ft == 35.0


def test_rule_normalizer_supports_nested_and_flat_aliases_together() -> None:
    parcel = _parcel("normalize-aliases", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])

    rules = normalize_rules(
        {
            "district": "C-2",
            "min lot area": "2500",
            "setbacks": {"rear": "10 ft"},
            "minimum front yard": "10 ft",
            "side setback": "0 ft",
            "allowed uses": "commercial, mixed_use",
        },
        parcel=parcel,
        jurisdiction="Draper",
    )

    assert rules.min_lot_size_sqft == 2500.0
    assert rules.setbacks == Setbacks(front=10.0, side=0.0, rear=10.0)
    assert rules.allowed_uses == ["commercial", "mixed_use"]


def test_service_extracts_rules_from_layer_attributes_without_rule_file(dataset_root: Path) -> None:
    city_dir = dataset_root / "zoning_dataset_sample" / "draper"
    (city_dir / "district_rules.json").unlink()
    _write_json(
        city_dir / "zoning_layers.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "ZONING": "R3",
                        "DESCRIPTION": "Residential",
                        "MIN_LOTSIZ": "13000",
                        "FRONT_YARD": "25/35",
                        "SIDE_YARD": 10,
                        "BACK_YARD": "20/30",
                        "MAX_HEIGHT": 35,
                        "MAX_LOT_CO": 0.45,
                    },
                    "geometry": _geojson_polygon([(-111.88, 40.49), (-111.88, 40.51), (-111.85, 40.51), (-111.85, 40.49), (-111.88, 40.49)]),
                }
            ],
        },
    )
    rule_normalization_module._load_rule_index.cache_clear()
    parcel = _parcel("draper-r3-layer", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])

    rules = ZoningService(dataset_root=dataset_root).lookup(parcel).rules

    assert rules.district == "R3"
    assert rules.min_lot_size_sqft == 13000.0
    assert rules.max_units_per_acre == 4.0
    assert rules.setbacks.front == 25.0
    assert rules.setbacks.side == 10.0
    assert rules.setbacks.rear == 20.0


def test_service_applies_jurisdiction_fallback_defaults_for_missing_rule_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel("benchmark-fallback", "BenchmarkCounty_UT", [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])
    service = ZoningService()

    monkeypatch.setattr(
        service,
        "_resolve_raw_rules",
        lambda _parcel, _geometry: {
            "jurisdiction": "BenchmarkCounty_UT",
            "district": "R-1",
            "setbacks": {"front": None, "side": None, "rear": None},
            "min_lot_size_sqft": None,
            "max_units_per_acre": None,
            "height_limit_ft": None,
            "lot_coverage_max": None,
            "allowed_uses": None,
            "overlays": [],
            "rule_source": "layer_attributes",
        },
    )

    result = service.lookup(parcel)

    assert result.district == "R-1"
    assert result.rules.min_lot_size_sqft == 5500.0
    assert result.rules.max_units_per_acre == 6.0
    assert result.rules.setbacks == Setbacks(front=20.0, side=8.0, rear=15.0)
    assert result.rules.height_limit_ft == 35.0
    assert result.rules.lot_coverage_max == 0.5


def test_service_does_not_repair_missing_rule_fields_for_real_lookup_jurisdiction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parcel = _parcel("draper-no-defaults", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])
    service = ZoningService()

    monkeypatch.setattr(
        service,
        "_resolve_raw_rules",
        lambda _parcel, _geometry: {
            "jurisdiction": "Draper",
            "district": "R3",
            "setbacks": {"front": None, "side": None, "rear": None},
            "min_lot_size_sqft": None,
            "max_units_per_acre": None,
            "height_limit_ft": None,
            "lot_coverage_max": None,
            "allowed_uses": None,
            "overlays": [],
            "rule_source": "normalized_rules",
        },
    )

    with pytest.raises(IncompleteZoningRulesError) as exc_info:
        service.lookup(parcel)

    assert exc_info.value.district == "R3"
    assert exc_info.value.synthetic_fallback_used is False
    assert set(exc_info.value.missing_fields) == {
        "min_lot_size_sqft",
        "max_units_per_acre",
        "setbacks.front",
        "setbacks.side",
        "setbacks.rear",
    }


def test_service_does_not_repair_partial_rule_extraction_for_real_lookup_jurisdiction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parcel = _parcel("slc-mu3-partial", "Salt Lake City", [(-111.90, 40.75), (-111.90, 40.76), (-111.89, 40.76), (-111.89, 40.75), (-111.90, 40.75)])
    service = ZoningService()

    monkeypatch.setattr(
        service,
        "_resolve_raw_rules",
        lambda _parcel, _geometry: {
            "jurisdiction": "Salt Lake City",
            "district": "MU-3",
            "district_name": "Mixed Use 3",
            "setbacks": {"front": None, "side": None, "rear": None},
            "min_lot_size_sqft": None,
            "max_units_per_acre": None,
            "height_limit_ft": 60.0,
            "allowed_uses": ["mixed_use_residential"],
            "usability_reason_codes": ["mixed_use_district_has_form_or_use_conditionals"],
            "rule_source": "normalized_rules",
        },
    )

    with pytest.raises(IncompleteZoningRulesError) as exc_info:
        service.lookup(parcel)

    assert exc_info.value.district == "MU-3"
    assert exc_info.value.synthetic_fallback_used is False
    assert set(exc_info.value.missing_fields) == {
        "min_lot_size_sqft",
        "max_units_per_acre",
        "setbacks.front",
        "setbacks.side",
        "setbacks.rear",
    }


def test_service_rejects_unrealistic_rule_values_with_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel("invalid-values", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])
    service = ZoningService()

    monkeypatch.setattr(
        service,
        "_resolve_raw_rules",
        lambda _parcel, _geometry: {
            "jurisdiction": "Draper",
            "district": "R3",
            "setbacks": {"front": 5, "side": 5, "rear": 5},
            "min_lot_size_sqft": 200000,
            "max_units_per_acre": 4,
            "height_limit_ft": 10,
            "lot_coverage_max": 0.9,
            "allowed_uses": None,
            "overlays": [],
            "rule_source": "layer_attributes",
        },
    )

    with pytest.raises(InvalidZoningRulesError) as exc_info:
        service.lookup(parcel)
    assert exc_info.value.district == "R3"
    assert any("parcel.area_sqft" in message for message in exc_info.value.violations)


def test_invalid_zoning_failure_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel("invalid-repeat", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])
    service = ZoningService()

    monkeypatch.setattr(
        service,
        "_resolve_raw_rules",
        lambda _parcel, _geometry: {
            "jurisdiction": "Draper",
            "district": "R3",
            "setbacks": {"front": 1, "side": 1, "rear": 1},
            "min_lot_size_sqft": 1000000,
            "max_units_per_acre": 4,
            "rule_source": "layer_attributes",
        },
    )

    violations_by_run: list[list[str]] = []
    for _ in range(2):
        with pytest.raises(InvalidZoningRulesError) as exc_info:
            service.lookup(parcel)
        violations_by_run.append(exc_info.value.violations)

    assert violations_by_run[0] == violations_by_run[1]


def test_api_returns_422_for_invalid_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubService:
        def lookup(self, parcel):
            raise InvalidZoningRulesError("R3", ["max_units_per_acre must be <= 80.0"])

    monkeypatch.setattr("bedrock.api.zoning_api.ZoningService", lambda: StubService())
    client = TestClient(create_app())

    response = client.post("/zoning/lookup", json={"parcel": _parcel("draper-invalid", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)]).model_dump()})

    assert response.status_code == 422
    assert response.json() == {
        "error": "invalid_zoning_rules",
        "district": "R3",
        "violations": ["max_units_per_acre must be <= 80.0"],
    }


def test_service_canonicalizes_noncanonical_district_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel("slc-canonical", "Salt Lake City", [(-111.911, 40.759), (-111.911, 40.761), (-111.909, 40.761), (-111.909, 40.759), (-111.911, 40.759)])
    match = OverlayMatch(
        jurisdiction="Salt Lake City",
        jurisdiction_slug="salt-lake-city",
        county_name="Salt Lake",
        dataset_dir=ROOT / "zoning_data_scraper" / "zoning_dataset_v3" / "salt-lake-city",
        district="58.2427392156863",
        district_name=None,
        overlays=(),
        density=None,
        source_layer="bad-layer",
        intersection_area=1.0,
    )

    monkeypatch.setattr("bedrock.services.zoning_service.lookup_zoning_district", lambda *args, **kwargs: match)
    monkeypatch.setattr("bedrock.services.zoning_service.canonicalize_district", lambda jurisdiction, district: "R-1-7000")
    monkeypatch.setattr(
        "bedrock.services.zoning_service.normalize_zoning_rules",
        lambda raw, *, parcel, jurisdiction, district: ZoningRules(
            parcel_id=parcel.parcel_id,
            jurisdiction=jurisdiction,
            district=district,
            min_lot_size_sqft=7000.0,
            max_units_per_acre=43560.0 / 7000.0,
            setbacks=Setbacks(front=25.0, side=8.0, rear=20.0),
            height_limit_ft=35.0,
            min_frontage_ft=None,
            road_right_of_way_ft=None,
            lot_coverage_max=0.45,
            allowed_uses=["single_family_residential"],
        ),
    )
    monkeypatch.setattr(
        "bedrock.services.zoning_service.ZoningService._apply_rule_fallbacks",
        lambda self, raw: {
            "jurisdiction": "Salt Lake City",
            "district": "R-1-7000",
            "district_name": None,
            "overlays": [],
            "setbacks": {"front": None, "side": None, "rear": None},
            "min_lot_size_sqft": None,
            "max_units_per_acre": None,
            "height_limit": None,
            "lot_coverage_limit": None,
            "dataset_path": "bad",
        },
    )

    result = ZoningService().lookup(parcel)

    assert result.district == "R-1-7000"
    assert result.rules.min_lot_size_sqft == 7000.0
    assert result.rules.setbacks.front == 25.0


def test_service_returns_404_when_no_district_matches(dataset_root: Path) -> None:
    parcel = _parcel("slc-outside", "Salt Lake City", [(-111.95, 40.80), (-111.95, 40.81), (-111.94, 40.81), (-111.94, 40.80), (-111.95, 40.80)])

    with pytest.raises(NoZoningMatchError):
        ZoningService(dataset_root=dataset_root).lookup(parcel)


def test_service_returns_409_for_district_ambiguity(dataset_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    city_dir = dataset_root / "zoning_dataset_sample" / "draper"
    _write_json(
        city_dir / "normalized_zoning.json",
        [
            {
                "city": "Draper",
                "zoning_code": "R3",
                "zoning_name": "Residential",
                "density": "4 du/ac",
                "source_layer": "draper-zoning",
                "geometry": _geojson_polygon([(-111.88, 40.49), (-111.88, 40.51), (-111.84, 40.51), (-111.84, 40.49), (-111.88, 40.49)]),
            },
            {
                "city": "Draper",
                "zoning_code": "C-2",
                "zoning_name": "Commercial",
                "density": "10 du/ac",
                "source_layer": "draper-zoning",
                "geometry": _geojson_polygon([(-111.88, 40.49), (-111.88, 40.51), (-111.84, 40.51), (-111.84, 40.49), (-111.88, 40.49)]),
            },
        ],
    )
    zoning_overlay_module._zoning_features.cache_clear()
    zoning_overlay_module._build_strtree.cache_clear()
    parcel = _parcel("draper-ambiguous", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])

    with pytest.raises(AmbiguousZoningMatchError):
        ZoningService(dataset_root=dataset_root).lookup(parcel)


def test_api_maps_no_match_to_404(dataset_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ZoningService(dataset_root=dataset_root)
    monkeypatch.setattr("bedrock.api.zoning_api.ZoningService", lambda: service)
    client = TestClient(create_app())
    parcel = _parcel("slc-outside", "Salt Lake City", [(-111.95, 40.80), (-111.95, 40.81), (-111.94, 40.81), (-111.94, 40.80), (-111.95, 40.80)])

    response = client.post("/zoning/lookup", json={"parcel": parcel.model_dump()})

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "no_district_match"


def test_api_maps_ambiguity_to_409(dataset_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    city_dir = dataset_root / "zoning_dataset_sample" / "draper"
    _write_json(
        city_dir / "normalized_zoning.json",
        [
            {
                "city": "Draper",
                "zoning_code": "R3",
                "zoning_name": "Residential",
                "density": "4 du/ac",
                "source_layer": "draper-zoning",
                "geometry": _geojson_polygon([(-111.88, 40.49), (-111.88, 40.51), (-111.84, 40.51), (-111.84, 40.49), (-111.88, 40.49)]),
            },
            {
                "city": "Draper",
                "zoning_code": "C-2",
                "zoning_name": "Commercial",
                "density": "10 du/ac",
                "source_layer": "draper-zoning",
                "geometry": _geojson_polygon([(-111.88, 40.49), (-111.88, 40.51), (-111.84, 40.51), (-111.84, 40.49), (-111.88, 40.49)]),
            },
        ],
    )
    zoning_overlay_module._zoning_features.cache_clear()
    zoning_overlay_module._build_strtree.cache_clear()
    service = ZoningService(dataset_root=dataset_root)
    monkeypatch.setattr("bedrock.api.zoning_api.ZoningService", lambda: service)
    client = TestClient(create_app())
    parcel = _parcel("draper-ambiguous", "Draper", [(-111.878, 40.495), (-111.878, 40.497), (-111.876, 40.497), (-111.876, 40.495), (-111.878, 40.495)])

    response = client.post("/zoning/lookup", json={"parcel": parcel.model_dump()})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "ambiguous_district_match"


@pytest.mark.parametrize(
    ("jurisdiction", "district", "dataset_path"),
    (
        (
            "Provo",
            "R2",
            ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "provo" / "normalized_zoning.json",
        ),
        (
            "Salt Lake City",
            "R-1-7000",
            ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "salt-lake-city" / "normalized_zoning.json",
        ),
        (
            "Salt Lake City",
            "MU-3",
            ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "salt-lake-city" / "normalized_zoning.json",
        ),
        (
            "Lehi",
            "TH-5",
            ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "lehi" / "normalized_zoning.json",
        ),
    ),
)
def test_real_lookup_uses_clean_geometry_without_stub_or_jurisdiction_defaults(
    jurisdiction: str,
    district: str,
    dataset_path: Path,
) -> None:
    parcel = _real_dataset_parcel(jurisdiction, district, dataset_path)
    service = ZoningService(dataset_root=ROOT / "zoning_data_scraper")
    parcel_geometry = shape(parcel.geometry)

    raw = service._resolve_raw_rules(parcel, parcel_geometry)
    normalized = service._normalize_raw_input(parcel_geometry, raw)
    enriched = service._apply_rule_fallbacks(normalized)

    expected_canonical = "R-2" if jurisdiction == "Provo" and district == "R2" else district
    assert normalized["district"] == expected_canonical
    assert raw["source_layer"] != "precomputed_district_index"
    assert enriched.get("rule_source") != "jurisdiction_fallback"


def test_provo_real_lookup_canonicalizes_actual_district_labels_without_fallback() -> None:
    parcel = _real_dataset_parcel(
        "Provo",
        "R2",
        ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "provo" / "normalized_zoning.json",
    )
    service = ZoningService(dataset_root=ROOT / "zoning_data_scraper")
    parcel_geometry = shape(parcel.geometry)

    raw = service._resolve_raw_rules(parcel, parcel_geometry)
    normalized = service._normalize_raw_input(parcel_geometry, raw)
    enriched = service._apply_rule_fallbacks(normalized)
    result = service.lookup(parcel)

    assert raw["district"] == "R2"
    assert normalized["district"] == "R-2"
    assert raw["source_layer"] != "precomputed_district_index"
    assert enriched.get("rule_source") != "jurisdiction_fallback"
    assert result.district == "R-2"
    assert result.rules.min_lot_size_sqft == 6000.0
    assert result.rules.max_units_per_acre == 7.26


def test_provo_real_lookup_canonicalizes_r16_to_r1_6_without_fallback() -> None:
    parcel = _real_dataset_parcel(
        "Provo",
        "R16",
        ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "provo" / "normalized_zoning.json",
    )
    service = ZoningService(dataset_root=ROOT / "zoning_data_scraper")
    parcel_geometry = shape(parcel.geometry)

    raw = service._resolve_raw_rules(parcel, parcel_geometry)
    normalized = service._normalize_raw_input(parcel_geometry, raw)
    enriched = service._apply_rule_fallbacks(normalized)
    result = service.lookup(parcel)

    assert raw["district"] == "R16"
    assert normalized["district"] == "R-1-6"
    assert raw["source_layer"] != "precomputed_district_index"
    assert enriched.get("rule_source") != "jurisdiction_fallback"
    assert result.district == "R-1-6"
    assert result.rules.min_lot_size_sqft == 6000.0
    assert result.rules.max_units_per_acre == 7.26
    assert result.rules.setbacks.front == 23.0


def test_cottonwood_heights_real_lookup_canonicalizes_walsh_pdd_to_r1_8_without_fallback() -> None:
    parcel = _real_dataset_parcel(
        "Cottonwood Heights",
        "PDD-1 (Walsh)",
        ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "cottonwood-heights" / "normalized_zoning.json",
    )
    service = ZoningService(dataset_root=ROOT / "zoning_data_scraper")
    parcel_geometry = shape(parcel.geometry)

    raw = service._resolve_raw_rules(parcel, parcel_geometry)
    normalized = service._normalize_raw_input(parcel_geometry, raw)
    enriched = service._apply_rule_fallbacks(normalized)
    result = service.lookup(parcel.model_copy(update={"area_sqft": 12000.0}))

    assert raw["district"] == "PDD-1 (Walsh)"
    assert normalized["district"] == "R-1-8"
    assert raw["source_layer"] != "precomputed_district_index"
    assert enriched.get("rule_source") != "jurisdiction_fallback"
    assert result.district == "R-1-8"
    assert result.rules.min_lot_size_sqft == 8000.0
    assert result.rules.max_units_per_acre == 5.45


def test_real_provo_rc_zone_remains_explicitly_incomplete_without_fallback() -> None:
    parcel = _real_dataset_parcel(
        "Provo",
        "RC",
        ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "provo" / "normalized_zoning.json",
    ).model_copy(update={"area_sqft": 12000.0})
    service = ZoningService(dataset_root=ROOT / "zoning_data_scraper")

    with pytest.raises(IncompleteZoningRulesError) as exc_info:
        service.lookup(parcel)

    assert exc_info.value.district == "RC"
    assert set(exc_info.value.missing_fields) == {"min_lot_size_sqft", "max_units_per_acre", "setbacks.front", "setbacks.side", "setbacks.rear"}
    assert exc_info.value.synthetic_fallback_used is False


@pytest.mark.parametrize(
    ("jurisdiction", "prefix", "expected_count"),
    (
        ("Provo", "ui-provo-%", 10),
        ("Murray", "ui-murray-%", 10),
        ("Cottonwood Heights", "ui-cottonwood-heights-%", 10),
    ),
)
def test_real_ui_parcels_resolve_to_real_districts_without_stub_fallback(
    jurisdiction: str,
    prefix: str,
    expected_count: int,
) -> None:
    con = sqlite3.connect(ROOT / "bedrock" / "data" / "parcels.db")
    rows = con.execute(
        "SELECT parcel_id, geometry_json FROM parcels WHERE parcel_id LIKE ? ORDER BY parcel_id LIMIT 10",
        (prefix,),
    ).fetchall()
    con.close()

    assert len(rows) == expected_count

    for parcel_id, geometry_json in rows:
        match = lookup_zoning_district(
            shape(json.loads(geometry_json)),
            parcel_jurisdiction=jurisdiction,
            dataset_root=ROOT / "zoning_data_scraper",
        )
        assert match.jurisdiction == jurisdiction
        assert match.district
        assert match.source_layer != "precomputed_district_index"


def test_api_returns_422_for_incomplete_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubService:
        def lookup(self, parcel):
            raise IncompleteZoningRulesError(
                "R-1-7000",
                ["min_lot_size_sqft", "setbacks.front"],
                usability="partially_usable",
                available_fields=["height_limit_ft"],
                reason_codes=["mixed_use_district_has_form_or_use_conditionals"],
            )

    monkeypatch.setattr("bedrock.api.zoning_api.ZoningService", lambda: StubService())
    client = TestClient(create_app())

    response = client.post("/zoning/lookup", json={"parcel": _parcel("slc-422", "Salt Lake City", [(-111.911, 40.759), (-111.911, 40.761), (-111.909, 40.761), (-111.909, 40.759), (-111.911, 40.759)]).model_dump()})

    assert response.status_code == 422
    assert response.json() == {
        "error": "incomplete_zoning_rules",
        "district": "R-1-7000",
        "missing_fields": ["min_lot_size_sqft", "setbacks.front"],
        "usability": "partially_usable",
        "available_fields": ["height_limit_ft"],
        "reason_codes": ["mixed_use_district_has_form_or_use_conditionals"],
        "synthetic_fallback_used": False,
    }


def test_service_uses_safe_minimum_viable_rules_when_jurisdiction_defaults_are_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel("unknown-safe-min", "Unknown City", [(-111.90, 40.70), (-111.90, 40.71), (-111.89, 40.71), (-111.89, 40.70), (-111.90, 40.70)])
    service = ZoningService()

    monkeypatch.setattr(
        service,
        "_resolve_raw_rules",
        lambda _parcel, _geometry: {
            "jurisdiction": "Unknown City",
            "district": "R-1",
            "setbacks": {"front": None, "side": None, "rear": None},
            "min_lot_size_sqft": None,
            "max_units_per_acre": None,
            "height_limit_ft": None,
            "lot_coverage_max": None,
            "allowed_uses": None,
            "overlays": [],
            "rule_source": "layer_attributes",
        },
    )

    result = service.lookup(parcel)

    assert result.district == "R-1"
    assert result.rules.min_lot_size_sqft == 5000.0
    assert result.rules.max_units_per_acre == 4.0
    assert result.rules.setbacks == Setbacks(front=20.0, side=5.0, rear=15.0)
    assert result.rules.height_limit_ft == 30.0
    assert result.rules.lot_coverage_max == 0.4
