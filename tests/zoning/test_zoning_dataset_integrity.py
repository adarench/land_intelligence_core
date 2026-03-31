from __future__ import annotations

import json
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
from bedrock.services.zoning_rule_normalizer import normalize_rules as canonicalize_rule_entry
from bedrock.services.zoning_service import IncompleteZoningRulesError, ZoningService, normalize_zoning_rules as normalize_service_rules
from zoning_data_scraper.services import zoning_code_rules as zoning_code_rules_module
from zoning_data_scraper.services import rule_normalization as rule_normalization_module
from zoning_data_scraper.services import zoning_overlay as zoning_overlay_module
from zoning_data_scraper.services.rule_normalization import normalize_zoning_rules as normalize_scraper_rules
from zoning_data_scraper.services.zoning_overlay import (
    OverlayMatch,
    dataset_validation_report,
    jurisdiction_has_clean_lookup_coverage,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _geojson_polygon(coords: list[tuple[float, float]]) -> dict:
    return {"type": "Polygon", "coordinates": [[list(point) for point in coords]]}


def _square_around(point: list[float], delta: float = 0.001) -> dict:
    x, y = point
    return _geojson_polygon(
        [
            (x - delta, y - delta),
            (x - delta, y + delta),
            (x + delta, y + delta),
            (x + delta, y - delta),
            (x - delta, y - delta),
        ]
    )


def test_salt_lake_city_dataset_is_artifact_only() -> None:
    report = dataset_validation_report(ROOT / "zoning_data_scraper" / "zoning_dataset_v3" / "salt-lake-city")

    assert report.accepted_feature_count == 0
    assert "Institutional Control Buffer Zone" in report.rejected_layer_names


def test_lehi_dataset_is_artifact_only() -> None:
    report = dataset_validation_report(ROOT / "zoning_data_scraper" / "zoning_dataset_one" / "lehi")

    assert report.accepted_feature_count == 0
    assert any("Land Use" in name or "Transportation" in name for name in report.rejected_layer_names)


def test_draper_dataset_keeps_zoning_and_excludes_artifacts() -> None:
    report = dataset_validation_report(ROOT / "zoning_data_scraper" / "zoning_dataset_sample" / "draper")

    assert report.accepted_feature_count > 0
    assert "Zoning" in report.accepted_layer_names
    assert "City-Owned Property" in report.rejected_layer_names


def test_phase_a_real_lookup_coverage_now_includes_salt_lake_city_and_lehi() -> None:
    dataset_root = ROOT / "zoning_data_scraper"

    assert jurisdiction_has_clean_lookup_coverage("Cottonwood Heights", dataset_root=dataset_root) is True
    assert jurisdiction_has_clean_lookup_coverage("Draper", dataset_root=dataset_root) is True
    assert jurisdiction_has_clean_lookup_coverage("Lehi", dataset_root=dataset_root) is True
    assert jurisdiction_has_clean_lookup_coverage("Murray", dataset_root=dataset_root) is True
    assert jurisdiction_has_clean_lookup_coverage("Provo", dataset_root=dataset_root) is True
    assert jurisdiction_has_clean_lookup_coverage("Salt Lake City", dataset_root=dataset_root) is True
    assert jurisdiction_has_clean_lookup_coverage("West Valley City", dataset_root=dataset_root) is True


@pytest.mark.parametrize(
    ("city_slug", "expected_features"),
    (
        ("cottonwood-heights", 23),
        ("murray", 23),
        ("provo", 95),
        ("salt-lake-city", 45),
        ("lehi", 28),
    ),
)
def test_phase_a_real_datasets_are_clean_polygonal_layers(city_slug: str, expected_features: int) -> None:
    dataset_dir = ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / city_slug
    metadata = json.loads((dataset_dir / "metadata.json").read_text())
    report = dataset_validation_report(dataset_dir)
    zoning_rows = json.loads((dataset_dir / "normalized_zoning.json").read_text())

    assert metadata["geometry_validation"]["output_invalid_geometry_count"] == 0
    assert metadata["feature_count"] == expected_features
    assert report.accepted_feature_count == expected_features
    assert report.invalid_identifier_count == 0
    assert all(row["geometry"]["type"] in {"Polygon", "MultiPolygon"} for row in zoning_rows)


def test_phase_b_real_rule_store_covers_supported_real_districts_without_defaults() -> None:
    dataset_root = ROOT / "zoning_data_scraper" / "zoning_dataset_v8"
    normalized_root = ROOT / "zoning_data_scraper" / "data" / "normalized_rules"
    expected_supported = {
        "provo": {
            "LDR",
            "MDR",
            "R16",
            "R2",
        },
        "murray": {
            "R-1-8",
        },
        "cottonwood-heights": {
            "PDD-1 (Walsh)",
            "R-1-8",
            "RM",
        },
        "salt-lake-city": {
            "FR-1",
            "FR-2",
            "FR-3",
            "MU-3",
            "R-1-12000",
            "R-1-5000",
            "R-1-7000",
            "R-2",
            "RMF-30",
            "RMF-35",
            "RMF-45",
            "RMF-75",
            "SR-1",
            "SR-1A",
            "SR-3",
        },
        "lehi": {"R-1-8", "R-1-10", "R-1-12", "R-1-15", "R-1-22", "RA-1", "TH-5"},
    }

    for city_slug in ("provo", "murray", "cottonwood-heights", "salt-lake-city", "lehi"):
        zoning_rows = json.loads((dataset_root / city_slug / "normalized_zoning.json").read_text())
        supported_codes = {row["zoning_code"] for row in zoning_rows}
        rules = json.loads((normalized_root / f"{city_slug}.json").read_text())["districts"]
        matched_codes = sorted(supported_codes & set(rules))
        alias_backed_codes = set()
        for code in supported_codes:
            for district_code, record in rules.items():
                aliases = set(record.get("aliases") or [])
                if code == district_code or code in aliases:
                    alias_backed_codes.add(code)
        assert alias_backed_codes, f"{city_slug} should have real geometry for at least one normalized-rule district"
        assert alias_backed_codes == expected_supported[city_slug]
        for district_code, record in rules.items():
            if district_code not in {
                "LDR",
                "MDR",
                "R-1-6",
                "R-1-10",
                "R-1-15",
                "R-1-8",
                "R-2",
                "R-3",
            } and city_slug == "provo":
                continue
            assert record["min_lot_size_sqft"] > 0
            assert record["max_units_per_acre"] > 0
            assert record["setbacks"]["front"] > 0
            assert record["setbacks"]["side"] > 0
            assert record["setbacks"]["rear"] > 0


@pytest.mark.parametrize(
    "city_slug",
    ("provo", "salt-lake-city", "lehi"),
)
def test_phase_a_real_datasets_do_not_have_material_cross_district_overlap(city_slug: str) -> None:
    zoning_rows = json.loads(
        (ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / city_slug / "normalized_zoning.json").read_text()
    )
    geometries = [(row["zoning_code"], shape(row["geometry"])) for row in zoning_rows]
    max_overlap_area = 0.0

    for index, (district, geometry) in enumerate(geometries):
        for other_district, other_geometry in geometries[index + 1:]:
            if district == other_district or not geometry.intersects(other_geometry):
                continue
            max_overlap_area = max(max_overlap_area, geometry.intersection(other_geometry).area)

    assert max_overlap_area < 1e-6


def test_normalized_rule_store_covers_at_least_ten_layout_safe_districts() -> None:
    normalized_root = ROOT / "zoning_data_scraper" / "data" / "normalized_rules"
    total_districts = 0

    for path in sorted(normalized_root.glob("*.json")):
        payload = json.loads(path.read_text())
        jurisdiction = payload["jurisdiction"]
        districts = payload.get("districts") or {}
        assert districts, f"{path.name} should define districts"
        for district_code, record in districts.items():
            total_districts += 1
            assert record.get("district") == district_code
            assert "min_lot_size_sqft" in record
            assert "max_units_per_acre" in record
            assert "setbacks" in record
            assert "height_limit_ft" in record
            assert "lot_coverage_max" in record
            setbacks = record["setbacks"]
            assert set(setbacks) >= {"front", "side", "rear"}

            parcel = Parcel(
                parcel_id=f"normalized-{jurisdiction}-{district_code}",
                geometry=_geojson_polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]),
                area_sqft=1000,
                centroid=[0.5, 0.5],
                bounding_box=None,
                jurisdiction=jurisdiction,
                zoning_district=None,
                utilities=[],
                access_points=[],
                topography={},
                existing_structures=[],
            )
            normalized = canonicalize_rule_entry(record, parcel=parcel, jurisdiction=jurisdiction, district=district_code)
            assert normalized.district == district_code
            assert normalized.min_lot_size_sqft is not None
            assert normalized.max_units_per_acre is not None
            assert normalized.setbacks.front is not None
            assert normalized.setbacks.side is not None
            assert normalized.setbacks.rear is not None

    assert total_districts >= 10


def test_draper_real_rule_store_covers_layout_safe_residential_districts_without_defaults() -> None:
    payload = json.loads((ROOT / "zoning_data_scraper" / "data" / "normalized_rules" / "draper.json").read_text())
    districts = payload["districts"]

    for district_code in ("A5", "R3", "RA1", "RM", "RM1"):
        record = districts[district_code]
        assert record["min_lot_size_sqft"] > 0
        assert record["max_units_per_acre"] > 0
        assert record["setbacks"]["front"] > 0
        assert record["setbacks"]["side"] > 0
        assert record["setbacks"]["rear"] > 0


def test_rule_extraction_prioritizes_rules_file_and_builds_valid_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zoning_code_rules_module, "NORMALIZED_RULES_ROOT", tmp_path / "normalized_rules")
    dataset_root = tmp_path / "zoning_dataset_sample" / "draper"
    _write_json(
        dataset_root / "metadata.json",
        {
            "city": "Draper",
            "city_slug": "draper",
            "county_name": "Salt Lake",
            "layer_sources": [
                {
                    "name": "Zoning",
                    "category": "zoning",
                    "source_layer": "draper-zoning",
                    "feature_count": 1,
                }
            ],
        },
    )
    _write_json(
        dataset_root / "normalized_zoning.json",
        [
            {
                "city": "Draper",
                "zoning_code": "R3",
                "zoning_name": "Residential",
                "density": None,
                "overlay": None,
                "source_layer": "draper-zoning",
                "geometry": _geojson_polygon([(-111.88, 40.49), (-111.88, 40.51), (-111.85, 40.51), (-111.85, 40.49), (-111.88, 40.49)]),
            }
        ],
    )
    _write_json(
        dataset_root / "zoning_layers.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "ZONING": "R3",
                        "DESCRIPTION": "Residential",
                        "MIN_LOTSIZ": "12000",
                        "FRONT_YARD": 20,
                        "SIDE_YARD": 7,
                        "BACK_YARD": 18,
                    },
                    "geometry": _geojson_polygon([(-111.88, 40.49), (-111.88, 40.51), (-111.85, 40.51), (-111.85, 40.49), (-111.88, 40.49)]),
                }
            ],
        },
    )
    _write_json(
        dataset_root / "district_rules.json",
        {
            "R3": {
                "district": "R3",
                "min_lot_size_sqft": 13000,
                "max_units_per_acre": 4,
                "setbacks": {"front": 25, "side": 8, "rear": 20},
                "height_limit_ft": 35,
                "lot_coverage_max": 0.45,
            }
        },
    )

    zoning_overlay_module._dataset_info.cache_clear()
    zoning_overlay_module._zoning_features.cache_clear()
    zoning_overlay_module._build_strtree.cache_clear()
    zoning_overlay_module._filtered_zoning_rows.cache_clear()
    zoning_overlay_module._layer_source_index.cache_clear()
    rule_normalization_module._load_rule_index.cache_clear()
    rule_normalization_module._load_rule_index_with_sources.cache_clear()
    zoning_code_rules_module._load_normalized_rule_index.cache_clear()
    zoning_code_rules_module._load_normalized_rules_document.cache_clear()

    match = OverlayMatch(
        jurisdiction="Draper",
        jurisdiction_slug="draper",
        county_name="Salt Lake",
        dataset_dir=dataset_root,
        district="R3",
        district_name="Residential",
        overlays=(),
        density=None,
        source_layer="draper-zoning",
        intersection_area=1.0,
    )

    normalized = normalize_scraper_rules(match)

    assert normalized["rule_source"] == "rules_file"


def test_real_mixed_use_districts_can_be_promoted_to_complete_rules_when_normalized_rules_exist() -> None:
    dataset_dir = ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "salt-lake-city"
    zoning_rows = json.loads((dataset_dir / "normalized_zoning.json").read_text())
    mu3_row = next(row for row in zoning_rows if row.get("zoning_code") == "MU-3")

    normalized = normalize_scraper_rules(
        OverlayMatch(
            jurisdiction="Salt Lake City",
            jurisdiction_slug="salt-lake-city",
            county_name="Salt Lake",
            dataset_dir=dataset_dir,
            district="MU-3",
            district_name=mu3_row.get("zoning_name"),
            overlays=(),
            density=None,
            source_layer=mu3_row.get("source_layer"),
            intersection_area=1.0,
        )
    )

    assert normalized["usability_class"] == "layout_safe"
    assert normalized["missing_layout_fields"] == []
    assert normalized["min_lot_size_sqft"] == 7000.0
    assert normalized["max_units_per_acre"] == 6.22
    assert normalized["setbacks"] == {"front": 25.0, "side": 8.0, "rear": 20.0}


def test_artifact_only_dataset_fails_closed_with_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset_root = tmp_path
    _write_json(
        dataset_root / "zoning_dataset_sample" / "lehi" / "metadata.json",
        {
            "city": "Lehi",
            "city_slug": "lehi",
            "county_name": "Utah",
            "layer_sources": [
                {
                    "name": "Transportation Corridors",
                    "category": "transportation",
                    "source_layer": "lehi-transport",
                    "feature_count": 1,
                }
            ],
        },
    )
    _write_json(
        dataset_root / "zoning_dataset_sample" / "lehi" / "normalized_zoning.json",
        [
            {
                "city": "Lehi",
                "zoning_code": "101",
                "zoning_name": "Transportation Corridors",
                "source_layer": "lehi-transport",
                "geometry": _geojson_polygon([(-111.89, 40.38), (-111.89, 40.40), (-111.86, 40.40), (-111.86, 40.38), (-111.89, 40.38)]),
            }
        ],
    )
    zoning_overlay_module._dataset_info.cache_clear()
    zoning_overlay_module._zoning_features.cache_clear()
    zoning_overlay_module._build_strtree.cache_clear()
    zoning_overlay_module._filtered_zoning_rows.cache_clear()
    zoning_overlay_module._layer_source_index.cache_clear()

    parcel = Parcel(
        parcel_id="lehi-integrity-422",
        geometry=_geojson_polygon([(-111.95, 40.45), (-111.95, 40.46), (-111.94, 40.46), (-111.94, 40.45), (-111.95, 40.45)]),
        area_sqft=1000,
        centroid=[-111.945, 40.455],
        bounding_box=None,
        jurisdiction="Lehi",
        zoning_district=None,
        utilities=[],
        access_points=[],
        topography={},
        existing_structures=[],
    )

    service = ZoningService(dataset_root=dataset_root)
    monkeypatch.setattr("bedrock.api.zoning_api.ZoningService", lambda: service)
    client = TestClient(create_app())

    response = client.post("/zoning/lookup", json={"parcel": parcel.model_dump()})

    assert response.status_code == 422
    assert response.json()["error"] == "incomplete_zoning_rules"
    assert response.json()["missing_fields"]
    with pytest.raises(IncompleteZoningRulesError):
        service.lookup(parcel)
