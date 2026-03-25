from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))

from bedrock.api.zoning_api import create_app
from bedrock.contracts.parcel import Parcel
from bedrock.services.zoning_service import ZoningService
from tests.runtime_validation_utils import format_runtime_report, run_runtime_validation


def _parcel_polygon_for_point(x: float, y: float, delta: float = 0.0001) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [x - delta, y - delta],
            [x - delta, y + delta],
            [x + delta, y + delta],
            [x + delta, y - delta],
            [x - delta, y - delta],
        ]],
    }


def _component_geometries(geometry) -> list:
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    return []


def _build_real_labeled_cases(
    *,
    jurisdiction: str,
    dataset_path: Path,
    rules_path: Path,
    preferred_districts: tuple[str, ...],
    total_cases: int,
    delta: float = 0.00008,
) -> list[tuple[str, dict]]:
    zoning_rows = json.loads(dataset_path.read_text())
    normalized_rules = json.loads(rules_path.read_text())["districts"]
    cases: list[tuple[str, dict]] = []

    def _resolve_rule(dataset_code: str) -> tuple[str, dict] | None:
        if dataset_code in normalized_rules:
            return dataset_code, normalized_rules[dataset_code]
        for canonical_district, record in normalized_rules.items():
            aliases = set(record.get("aliases") or [])
            if dataset_code in aliases:
                return canonical_district, record
        return None

    for dataset_code in preferred_districts:
        resolved = _resolve_rule(dataset_code)
        if resolved is None:
            continue
        canonical_district, rule = resolved
        district_rows = [row for row in zoning_rows if row.get("zoning_code") == dataset_code]
        components = []
        for row in district_rows:
            geometry = shape(row["geometry"])
            components.extend(_component_geometries(geometry))
        components = [component for component in components if not component.is_empty]
        components.sort(key=lambda item: item.area, reverse=True)
        for component_index, component in enumerate(components):
            point = component.representative_point()
            cases.append(
                (
                    canonical_district,
                    {
                        "parcel_id": f"{jurisdiction.lower().replace(' ', '-')}-{dataset_code.lower().replace(' ', '-').replace('(', '').replace(')', '')}-{component_index}",
                        "geometry": _parcel_polygon_for_point(point.x, point.y, delta=delta),
                        "jurisdiction": jurisdiction,
                        "area_sqft": max(100000.0, float(rule["min_lot_size_sqft"]) * 1.1),
                    },
                )
            )
            if len(cases) >= total_cases:
                return cases

    if not cases:
        raise AssertionError(f"No labeled cases could be built for {jurisdiction}")
    while len(cases) < total_cases:
        district, parcel = cases[len(cases) % len(cases)]
        duplicated = dict(parcel)
        duplicated["parcel_id"] = f"{parcel['parcel_id']}-dup{len(cases)}"
        cases.append((district, duplicated))
    return cases


def _assert_real_runtime_thresholds(
    *,
    jurisdiction: str,
    dataset_path: Path,
    rules_path: Path,
    preferred_districts: tuple[str, ...],
    total_cases: int = 5,
) -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    service = ZoningService(dataset_root=ROOT / "zoning_data_scraper")
    normalized_rules = json.loads(rules_path.read_text())["districts"]
    labeled_cases = _build_real_labeled_cases(
        jurisdiction=jurisdiction,
        dataset_path=dataset_path,
        rules_path=rules_path,
        preferred_districts=preferred_districts,
        total_cases=total_cases,
    )

    district_hits = 0
    rule_hits = 0
    layout_field_hits = 0
    fallback_free_hits = 0
    failures: list[dict[str, object]] = []

    for expected_district, parcel in labeled_cases:
        response = client.post("/zoning/lookup", json={"parcel": parcel})
        body = response.json()
        if response.status_code == 200 and body.get("district") == expected_district:
            district_hits += 1
        else:
            failures.append(
                {
                    "parcel_id": parcel["parcel_id"],
                    "expected_district": expected_district,
                    "status_code": response.status_code,
                    "body": body,
                }
            )
            continue

        record = normalized_rules[expected_district]
        if (
            math.isclose(body["min_lot_size_sqft"], record["min_lot_size_sqft"])
            and math.isclose(body["max_units_per_acre"], record["max_units_per_acre"])
            and math.isclose(body["setbacks"]["front"], record["setbacks"]["front"])
            and math.isclose(body["setbacks"]["side"], record["setbacks"]["side"])
            and math.isclose(body["setbacks"]["rear"], record["setbacks"]["rear"])
        ):
            rule_hits += 1

        if (
            body["min_lot_size_sqft"] > 0
            and body["max_units_per_acre"] > 0
            and body["setbacks"]["front"] > 0
            and body["setbacks"]["side"] > 0
            and body["setbacks"]["rear"] > 0
            and "zoning_dataset_v8" in str(body.get("metadata", {}).get("source_run_id", ""))
        ):
            layout_field_hits += 1

        parcel_model = Parcel(**parcel)
        parcel_geometry = shape(parcel_model.geometry)
        raw = service._resolve_raw_rules(parcel_model, parcel_geometry)
        normalized_raw = service._normalize_raw_input(parcel_geometry, raw)
        enriched_raw = service._apply_rule_fallbacks(normalized_raw)
        if (
            raw.get("source_layer") != "precomputed_district_index"
            and enriched_raw.get("rule_source") != "jurisdiction_fallback"
        ):
            fallback_free_hits += 1

    total = len(labeled_cases)
    assert district_hits / total == 1.0, failures
    assert rule_hits / total == 1.0, failures
    assert layout_field_hits / total == 1.0, failures
    assert fallback_free_hits / total == 1.0, failures


def test_live_zoning_runtime_meets_milestone_2_accuracy_thresholds() -> None:
    report = run_runtime_validation()
    metrics = report["metrics"]

    assert metrics["district_identification_accuracy"] >= 0.95, format_runtime_report(report)
    assert metrics["rule_completeness"] >= 0.90, format_runtime_report(report)
    assert sum(report["usability_counts"].values()) == metrics["total_cases"], format_runtime_report(report)
    assert set(report["usability_counts"]) == {"layout_safe", "partially_usable", "non_usable"}


def test_phase_a_real_draper_runtime_meets_real_data_thresholds() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    zoning_rows = json.loads((ROOT / "zoning_data_scraper" / "zoning_dataset_v4" / "draper" / "normalized_zoning.json").read_text())
    normalized_rules = json.loads((ROOT / "zoning_data_scraper" / "data" / "normalized_rules" / "draper.json").read_text())["districts"]
    target_districts = ("A5", "R3", "R4", "R5", "RA1", "RA2", "RM", "RM1", "RR-22", "RR-43")

    labeled_cases: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for row in zoning_rows:
        district = row.get("zoning_code")
        if district not in target_districts or district in seen:
            continue
        geom = shape(row["geometry"])
        point = geom.representative_point()
        record = normalized_rules[district]
        labeled_cases.append(
            (
                district,
                {
                    "parcel_id": f"phase-a-{district.lower()}",
                    "geometry": _parcel_polygon_for_point(point.x, point.y),
                    "jurisdiction": "Draper",
                    "area_sqft": max(300000.0, float(record["min_lot_size_sqft"]) * 1.1),
                },
            )
        )
        seen.add(district)

    assert len(labeled_cases) == len(target_districts)

    district_hits = 0
    rule_hits = 0
    layout_field_hits = 0
    failures: list[dict[str, object]] = []

    for expected_district, parcel in labeled_cases:
        response = client.post("/zoning/lookup", json={"parcel": parcel})
        body = response.json()
        if response.status_code == 200 and body.get("district") == expected_district:
            district_hits += 1
        else:
            failures.append(
                {
                    "parcel_id": parcel["parcel_id"],
                    "expected_district": expected_district,
                    "status_code": response.status_code,
                    "body": body,
                }
            )
            continue

        record = normalized_rules[expected_district]
        if (
            math.isclose(body["min_lot_size_sqft"], record["min_lot_size_sqft"])
            and math.isclose(body["max_units_per_acre"], record["max_units_per_acre"])
            and math.isclose(body["setbacks"]["front"], record["setbacks"]["front"])
            and math.isclose(body["setbacks"]["side"], record["setbacks"]["side"])
            and math.isclose(body["setbacks"]["rear"], record["setbacks"]["rear"])
        ):
            rule_hits += 1

        if (
            body["min_lot_size_sqft"] > 0
            and body["max_units_per_acre"] > 0
            and body["setbacks"]["front"] > 0
            and body["setbacks"]["side"] > 0
            and body["setbacks"]["rear"] > 0
            and "zoning_stub_districts" not in str(body.get("metadata", {}).get("source_run_id", ""))
        ):
            layout_field_hits += 1

    total = len(labeled_cases)
    district_accuracy = district_hits / total
    rule_correctness = rule_hits / total
    layout_presence = layout_field_hits / total

    assert district_accuracy >= 0.95, failures
    assert rule_correctness >= 0.90, failures
    assert layout_presence >= 0.90, failures


def test_phase_a_real_salt_lake_city_runtime_meets_real_data_thresholds() -> None:
    _assert_real_runtime_thresholds(
        jurisdiction="Salt Lake City",
        dataset_path=ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "salt-lake-city" / "normalized_zoning.json",
        rules_path=ROOT / "zoning_data_scraper" / "data" / "normalized_rules" / "salt-lake-city.json",
        preferred_districts=(
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
        ),
        total_cases=15,
    )


def test_phase_a_real_lehi_runtime_meets_real_data_thresholds() -> None:
    _assert_real_runtime_thresholds(
        jurisdiction="Lehi",
        dataset_path=ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "lehi" / "normalized_zoning.json",
        rules_path=ROOT / "zoning_data_scraper" / "data" / "normalized_rules" / "lehi.json",
        preferred_districts=("R-1-8", "R-1-10", "R-1-12", "R-1-15", "R-1-22", "RA-1", "TH-5"),
        total_cases=7,
    )


def test_phase_b_real_provo_runtime_meets_real_data_thresholds_for_supported_residential_districts() -> None:
    _assert_real_runtime_thresholds(
        jurisdiction="Provo",
        dataset_path=ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "provo" / "normalized_zoning.json",
        rules_path=ROOT / "zoning_data_scraper" / "data" / "normalized_rules" / "provo.json",
        preferred_districts=("LDR", "MDR", "R16", "R2"),
        total_cases=6,
    )


def test_phase_b_real_murray_runtime_meets_real_data_thresholds_for_supported_residential_districts() -> None:
    _assert_real_runtime_thresholds(
        jurisdiction="Murray",
        dataset_path=ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "murray" / "normalized_zoning.json",
        rules_path=ROOT / "zoning_data_scraper" / "data" / "normalized_rules" / "murray.json",
        preferred_districts=("R-1-8",),
        total_cases=3,
    )


def test_phase_b_real_cottonwood_heights_runtime_meets_real_data_thresholds_for_supported_residential_districts() -> None:
    _assert_real_runtime_thresholds(
        jurisdiction="Cottonwood Heights",
        dataset_path=ROOT / "zoning_data_scraper" / "zoning_dataset_v8" / "cottonwood-heights" / "normalized_zoning.json",
        rules_path=ROOT / "zoning_data_scraper" / "data" / "normalized_rules" / "cottonwood-heights.json",
        preferred_districts=("R-1-8", "RM", "PDD-1 (Walsh)"),
        total_cases=5,
    )


def test_real_ui_parcels_resolve_real_districts_without_stub_fallback() -> None:
    zoning_client = TestClient(create_app(), raise_server_exceptions=False)
    service = ZoningService(dataset_root=ROOT / "zoning_data_scraper")
    con = sqlite3.connect(ROOT / "bedrock" / "data" / "parcels.db")
    samples = {
        "Provo": con.execute(
            "SELECT parcel_id, jurisdiction, area_sqft, geometry_json FROM parcels WHERE parcel_id LIKE 'ui-provo-%' ORDER BY parcel_id LIMIT 10"
        ).fetchall(),
        "Murray": con.execute(
            "SELECT parcel_id, jurisdiction, area_sqft, geometry_json FROM parcels WHERE parcel_id LIKE 'ui-murray-%' ORDER BY parcel_id LIMIT 10"
        ).fetchall(),
        "Cottonwood Heights": con.execute(
            "SELECT parcel_id, jurisdiction, area_sqft, geometry_json FROM parcels WHERE parcel_id LIKE 'ui-cottonwood-heights-%' ORDER BY parcel_id LIMIT 10"
        ).fetchall(),
    }
    con.close()

    failures: list[dict[str, object]] = []
    total = 0
    resolved = 0

    for jurisdiction, rows in samples.items():
        assert len(rows) >= 9
        for parcel_id, parcel_jurisdiction, area_sqft, geometry_json in rows:
            total += 1
            geometry = json.loads(geometry_json)
            xs = [point[0] for point in geometry["coordinates"][0][:-1]]
            ys = [point[1] for point in geometry["coordinates"][0][:-1]]
            parcel = {
                "parcel_id": parcel_id,
                "geometry": geometry,
                "jurisdiction": parcel_jurisdiction,
                "area_sqft": float(area_sqft),
                "centroid": [sum(xs) / len(xs), sum(ys) / len(ys)],
                "bounding_box": [min(xs), min(ys), max(xs), max(ys)],
                "utilities": [],
                "access_points": [],
                "topography": {},
                "existing_structures": [],
            }

            parcel_model = Parcel(**parcel)
            parcel_geometry = shape(parcel_model.geometry)
            raw = service._resolve_raw_rules(parcel_model, parcel_geometry)
            normalized = service._normalize_raw_input(parcel_geometry, raw)
            enriched = service._apply_rule_fallbacks(normalized)
            response = zoning_client.post("/zoning/lookup", json={"parcel": parcel})

            assert raw["source_layer"] != "precomputed_district_index"
            assert raw["district"]
            assert normalized["district"]
            assert enriched.get("rule_source") != "jurisdiction_fallback"
            assert enriched.get("rule_source") != "safe_minimum_viable"
            assert response.status_code in {200, 422}
            resolved += 1

    assert resolved / total == 1.0, failures
