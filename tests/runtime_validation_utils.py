from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
BEDROCK_ROOT = ROOT / "bedrock"
RUNTIME_DATASET_PATH = ROOT / "test_data" / "runtime_viable_parcels.json"
for candidate in (ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.api.pipeline_api import create_app as create_pipeline_app
from bedrock.api.zoning_api import create_app as create_zoning_app
from bedrock.services.zoning_service import reset_zoning_lookup_metrics, snapshot_zoning_lookup_metrics


REQUIRED_LAYOUT_FIELDS = (
    "min_lot_size_sqft",
    "max_units_per_acre",
    "setbacks.front",
    "setbacks.side",
    "setbacks.rear",
)


@dataclass(frozen=True)
class RuntimeJurisdictionCase:
    jurisdiction: str
    parcel_id: str
    expected_district: str
    ring: tuple[tuple[float, float], ...]

    def parcel_payload(self) -> dict[str, Any]:
        xs = [x for x, _ in self.ring[:-1]]
        ys = [y for _, y in self.ring[:-1]]
        return {
            "parcel_id": self.parcel_id,
            "geometry": {"type": "Polygon", "coordinates": [[list(point) for point in self.ring]]},
            "jurisdiction": self.jurisdiction,
            "area_sqft": 100000.0,
            "centroid": [sum(xs) / len(xs), sum(ys) / len(ys)],
            "bounding_box": [min(xs), min(ys), max(xs), max(ys)],
            "utilities": [],
            "access_points": [],
            "topography": {},
            "existing_structures": [],
        }


def _component_geometries(geometry: Any) -> list[Any]:
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    return []


def _square_ring(x: float, y: float, *, delta: float = 0.0002) -> tuple[tuple[float, float], ...]:
    return (
        (x - delta, y - delta),
        (x - delta, y + delta),
        (x + delta, y + delta),
        (x + delta, y - delta),
        (x - delta, y - delta),
    )


def _runtime_case_from_dataset(
    *,
    jurisdiction: str,
    parcel_id: str,
    expected_district: str,
    dataset_path: Path,
    component_index: int = 0,
    delta: float = 0.0002,
) -> RuntimeJurisdictionCase:
    rows = json.loads(dataset_path.read_text())
    district_rows = [row for row in rows if row.get("zoning_code") == expected_district]
    if not district_rows:
        raise AssertionError(f"Missing runtime district {expected_district!r} in {dataset_path}")

    components: list[Any] = []
    for row in district_rows:
        components.extend(_component_geometries(shape(row["geometry"])))
    components = [component for component in components if not component.is_empty]
    components.sort(key=lambda item: item.area, reverse=True)
    if not components:
        raise AssertionError(f"No polygon components available for {expected_district!r} in {dataset_path}")
    index = min(component_index, len(components) - 1)
    point = components[index].representative_point()
    return RuntimeJurisdictionCase(
        jurisdiction=jurisdiction,
        parcel_id=parcel_id,
        expected_district=expected_district,
        ring=_square_ring(float(point.x), float(point.y), delta=delta),
    )


RUNTIME_CASES = tuple(
    RuntimeJurisdictionCase(
        jurisdiction=str(item["jurisdiction"]),
        parcel_id=str(item["parcel_id"]),
        expected_district=str(item["expected_district"]),
        ring=tuple(
            (float(point[0]), float(point[1]))
            for point in item["geometry"]["coordinates"][0]
        ),
    )
    for item in json.loads(RUNTIME_DATASET_PATH.read_text()).get("records", [])
)


def _get_nested(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _missing_layout_fields(zoning_payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field_name in REQUIRED_LAYOUT_FIELDS:
        value = _get_nested(zoning_payload, field_name)
        if value is None:
            missing.append(field_name)
    return missing


def _response_payload(response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return response.text


@lru_cache(maxsize=1)
def run_runtime_validation() -> dict[str, Any]:
    reset_zoning_lookup_metrics()
    zoning_client = TestClient(create_zoning_app(), raise_server_exceptions=False)
    pipeline_client = TestClient(create_pipeline_app(), raise_server_exceptions=False)

    zoning_results: list[dict[str, Any]] = []
    pipeline_results: list[dict[str, Any]] = []

    for case in RUNTIME_CASES:
        parcel_payload = case.parcel_payload()

        zoning_started = time.perf_counter()
        zoning_response = zoning_client.post("/zoning/lookup", json={"parcel": parcel_payload})
        zoning_runtime = time.perf_counter() - zoning_started
        zoning_body = _response_payload(zoning_response)
        zoning_missing = _missing_layout_fields(zoning_body) if isinstance(zoning_body, dict) else list(REQUIRED_LAYOUT_FIELDS)
        district = zoning_body.get("district") if isinstance(zoning_body, dict) else None
        district_correct = zoning_response.status_code == 200 and district == case.expected_district
        rule_complete = zoning_response.status_code == 200 and not zoning_missing

        zoning_results.append(
            {
                "jurisdiction": case.jurisdiction,
                "parcel_id": case.parcel_id,
                "expected_district": case.expected_district,
                "status_code": zoning_response.status_code,
                "district": district,
                "district_correct": district_correct,
                "rule_complete": rule_complete,
                "missing_layout_fields": zoning_missing,
                "runtime": zoning_runtime,
                "body": zoning_body,
            }
        )

        pipeline_started = time.perf_counter()
        pipeline_response = pipeline_client.post(
            "/pipeline/run",
            json={
                "parcel_geometry": parcel_payload["geometry"],
                "parcel_id": parcel_payload.get("parcel_id"),
                "jurisdiction": parcel_payload.get("jurisdiction"),
                "max_candidates": 5,
            },
        )
        pipeline_runtime = time.perf_counter() - pipeline_started
        pipeline_body = _response_payload(pipeline_response)
        pipeline_results.append(
            {
                "jurisdiction": case.jurisdiction,
                "parcel_id": case.parcel_id,
                "status_code": pipeline_response.status_code,
                "success": pipeline_response.status_code == 200,
                "runtime": pipeline_runtime,
                "body": pipeline_body,
            }
        )

    total = len(RUNTIME_CASES)
    district_hits = sum(item["district_correct"] for item in zoning_results)
    rule_hits = sum(item["rule_complete"] for item in zoning_results)
    pipeline_hits = sum(item["success"] for item in pipeline_results)
    usability_counts = {"layout_safe": 0, "partially_usable": 0, "non_usable": 0}
    for item in zoning_results:
        body = item["body"]
        if item["status_code"] == 200:
            usability_counts["layout_safe"] += 1
            continue
        if isinstance(body, dict) and body.get("error") == "incomplete_zoning_rules":
            usability = str(body.get("usability") or "non_usable")
            if usability not in usability_counts:
                usability = "non_usable"
            usability_counts[usability] += 1
            continue
        usability_counts["non_usable"] += 1

    return {
        "metrics": {
            "district_identification_accuracy": district_hits / total,
            "rule_completeness": rule_hits / total,
            "pipeline_success_rate": pipeline_hits / total,
            "total_cases": total,
        },
        "usability_counts": usability_counts,
        "zoning_lookup_summary": snapshot_zoning_lookup_metrics(),
        "zoning_cases": zoning_results,
        "pipeline_cases": pipeline_results,
    }


def format_runtime_report(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
