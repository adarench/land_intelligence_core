from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))

from bedrock.api.runs_api import create_app
from bedrock.contracts.base import EngineMetadata
from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.schema_registry import CANONICAL_SERIALIZATION_FIELDS
from bedrock.contracts.zoning_rules import SetbackSet, ZoningRules
from bedrock.services.pipeline_run_store import PipelineRunStore
from bedrock.services.pipeline_service import PipelineRunRecord


def _geojson_polygon(coords: list[tuple[float, float]]) -> dict:
    return {"type": "Polygon", "coordinates": [[list(point) for point in coords]]}


def _parcel(parcel_id: str) -> Parcel:
    return Parcel(
        parcel_id=parcel_id,
        geometry=_geojson_polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]),
        area=1000,
        jurisdiction="Test City",
        zoning_district=None,
        utilities=[],
        access_points=[],
        topography={},
        existing_structures=[],
    )


def _zoning_rules(parcel_id: str) -> ZoningRules:
    return ZoningRules(
        parcel_id=parcel_id,
        jurisdiction="Test City",
        district="R-1",
        min_lot_size_sqft=8000,
        max_units_per_acre=4,
        setbacks=SetbackSet(front=25, side=8, rear=20),
        height_limit_ft=35,
        lot_coverage_max=0.45,
        metadata=EngineMetadata(source_engine="zoning_data_scraper", source_run_id="test"),
    )


def _layout_result(parcel_id: str, layout_id: str, score: float = 0.91) -> SubdivisionLayout:
    return SubdivisionLayout(
        layout_id=layout_id,
        parcel_id=parcel_id,
        unit_count=4,
        road_length_ft=120.0,
        lot_geometries=[_geojson_polygon([(0, 0), (0, 4), (4, 4), (4, 0), (0, 0)])],
        road_geometries=[_geojson_polygon([(4, 0), (4, 10), (5, 10), (5, 0), (4, 0)])],
        open_space_area_sqft=200.0,
        utility_length_ft=0.0,
        score=score,
        metadata=EngineMetadata(source_engine="bedrock.layout_service", source_run_id=None),
    )


def _feasibility_result(
    *,
    parcel_id: str,
    layout_id: str,
    units: int,
    projected_profit: float,
    roi: float,
) -> FeasibilityResult:
    return FeasibilityResult(
        scenario_id=f"scenario-{layout_id}",
        parcel_id=parcel_id,
        layout_id=layout_id,
        units=units,
        estimated_home_price=480000.0,
        construction_cost_per_home=260000.0,
        development_cost_total=500000.0,
        projected_revenue=1920000.0,
        projected_cost=1540000.0,
        projected_profit=projected_profit,
        ROI=roi,
        risk_score=0.18,
        confidence=0.9,
    )


def _persist_run(
    store: PipelineRunStore,
    *,
    run_id: str,
    timestamp: str,
    parcel_id: str,
    units: int,
    projected_profit: float,
    roi: float,
) -> dict:
    record = PipelineRunRecord(
        run_id=run_id,
        timestamp=datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc),
        parcel=_parcel(parcel_id),
        zoning=_zoning_rules(parcel_id),
        layout=_layout_result(parcel_id, f"layout-{run_id}"),
        feasibility=_feasibility_result(
            parcel_id=parcel_id,
            layout_id=f"layout-{run_id}",
            units=units,
            projected_profit=projected_profit,
            roi=roi,
        ),
    )
    path = store.save_run(run_id, record)
    return json.loads(path.read_text())


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    run_store = PipelineRunStore(log_path=tmp_path / "pipeline_runs.jsonl", runs_dir=tmp_path / "runs")
    monkeypatch.setattr("bedrock.api.runs_api.store", run_store)
    return TestClient(create_app())


def test_list_runs_returns_metadata_only(client: TestClient) -> None:
    store = client.app.dependency_overrides.get("store")  # type: ignore[attr-defined]
    if store is None:
        # FastAPI app does not track monkeypatched module globals; import the module store directly.
        from bedrock.api import runs_api

        store = runs_api.store

    _persist_run(
        store,
        run_id="run-001",
        timestamp="2026-03-19T12:00:00Z",
        parcel_id="parcel-001",
        units=4,
        projected_profit=380000.0,
        roi=0.2468,
    )

    response = client.get("/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "run_id": "run-001",
            "timestamp": "2026-03-19T12:00:00Z",
            "parcel_id": "parcel-001",
            "units": 4,
            "projected_profit": 380000.0,
            "ROI": 0.2468,
        }
    ]
    assert "geometry" not in payload[0]
    assert "parcel" not in payload[0]
    assert "feasibility" not in payload[0]


def test_get_run_returns_exact_stored_json(client: TestClient) -> None:
    from bedrock.api import runs_api

    expected = _persist_run(
        runs_api.store,
        run_id="run-002",
        timestamp="2026-03-19T12:05:00Z",
        parcel_id="parcel-002",
        units=6,
        projected_profit=510000.0,
        roi=0.3125,
    )

    response = client.get("/runs/run-002")

    assert response.status_code == 200
    assert set(response.json().keys()) == set(CANONICAL_SERIALIZATION_FIELDS["PipelineRun"])
    assert response.json()["schema_name"] == "PipelineRun"
    assert response.json()["schema_version"] == "1.0.0"
    assert response.json() == expected


def test_get_run_adapts_legacy_stored_shape(client: TestClient) -> None:
    from bedrock.api import runs_api

    parcel = _parcel("parcel-legacy")
    zoning = _zoning_rules(parcel.parcel_id)
    layout = _layout_result(parcel.parcel_id, "layout-legacy")
    feasibility = _feasibility_result(
        parcel_id=parcel.parcel_id,
        layout_id=layout.layout_id,
        units=7,
        projected_profit=620000.0,
        roi=0.41,
    )
    legacy_payload = {
        "run_id": "run-legacy",
        "timestamp": "2026-03-19T12:15:00Z",
        "parcel": parcel.model_dump(mode="json"),
        "zoning": zoning.model_dump(mode="json"),
        "layout": layout.model_dump(mode="json"),
        "feasibility": feasibility.model_dump(mode="json"),
    }
    path = runs_api.store.runs_dir / "run-legacy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    response = client.get("/runs/run-legacy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_name"] == "PipelineRun"
    assert payload["run_id"] == "run-legacy"
    assert payload["parcel_id"] == "parcel-legacy"
    assert payload["zoning_result"]["district"] == "R-1"
    assert payload["layout_result"]["layout_id"] == "layout-legacy"
    assert payload["feasibility_result"]["layout_id"] == "layout-legacy"


def test_list_runs_supports_sorting(client: TestClient) -> None:
    from bedrock.api import runs_api

    _persist_run(
        runs_api.store,
        run_id="run-older",
        timestamp="2026-03-19T11:00:00Z",
        parcel_id="parcel-001",
        units=4,
        projected_profit=300000.0,
        roi=0.20,
    )
    _persist_run(
        runs_api.store,
        run_id="run-mid",
        timestamp="2026-03-19T12:00:00Z",
        parcel_id="parcel-002",
        units=6,
        projected_profit=450000.0,
        roi=0.30,
    )
    _persist_run(
        runs_api.store,
        run_id="run-newer",
        timestamp="2026-03-19T13:00:00Z",
        parcel_id="parcel-003",
        units=5,
        projected_profit=400000.0,
        roi=0.25,
    )

    roi_response = client.get("/runs?sort=ROI&order=desc")
    assert [item["run_id"] for item in roi_response.json()] == ["run-mid", "run-newer", "run-older"]

    profit_response = client.get("/runs?sort=projected_profit&order=asc")
    assert [item["run_id"] for item in profit_response.json()] == ["run-older", "run-newer", "run-mid"]

    units_response = client.get("/runs?sort=units&order=desc")
    assert [item["run_id"] for item in units_response.json()] == ["run-mid", "run-newer", "run-older"]

    timestamp_response = client.get("/runs?sort=timestamp&order=desc")
    assert [item["run_id"] for item in timestamp_response.json()] == ["run-newer", "run-mid", "run-older"]


def test_list_runs_supports_filter_combinations(client: TestClient) -> None:
    from bedrock.api import runs_api

    _persist_run(
        runs_api.store,
        run_id="run-low",
        timestamp="2026-03-19T10:00:00Z",
        parcel_id="parcel-low",
        units=3,
        projected_profit=200000.0,
        roi=0.15,
    )
    _persist_run(
        runs_api.store,
        run_id="run-target-a",
        timestamp="2026-03-19T11:00:00Z",
        parcel_id="parcel-a",
        units=5,
        projected_profit=350000.0,
        roi=0.25,
    )
    _persist_run(
        runs_api.store,
        run_id="run-target-b",
        timestamp="2026-03-19T12:00:00Z",
        parcel_id="parcel-b",
        units=6,
        projected_profit=500000.0,
        roi=0.35,
    )
    _persist_run(
        runs_api.store,
        run_id="run-high",
        timestamp="2026-03-19T13:00:00Z",
        parcel_id="parcel-high",
        units=8,
        projected_profit=700000.0,
        roi=0.55,
    )

    response = client.get("/runs?min_ROI=0.20&max_ROI=0.40&min_units=5&max_units=6&sort=ROI&order=asc")

    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()] == ["run-target-a", "run-target-b"]


def test_list_runs_supports_pagination(client: TestClient) -> None:
    from bedrock.api import runs_api

    _persist_run(
        runs_api.store,
        run_id="run-1",
        timestamp="2026-03-19T10:00:00Z",
        parcel_id="parcel-1",
        units=4,
        projected_profit=300000.0,
        roi=0.20,
    )
    _persist_run(
        runs_api.store,
        run_id="run-2",
        timestamp="2026-03-19T11:00:00Z",
        parcel_id="parcel-2",
        units=5,
        projected_profit=350000.0,
        roi=0.25,
    )
    _persist_run(
        runs_api.store,
        run_id="run-3",
        timestamp="2026-03-19T12:00:00Z",
        parcel_id="parcel-3",
        units=6,
        projected_profit=400000.0,
        roi=0.30,
    )
    _persist_run(
        runs_api.store,
        run_id="run-4",
        timestamp="2026-03-19T13:00:00Z",
        parcel_id="parcel-4",
        units=7,
        projected_profit=450000.0,
        roi=0.35,
    )

    response = client.get("/runs?sort=timestamp&order=asc&limit=2&offset=1")

    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()] == ["run-2", "run-3"]


def test_list_runs_supports_sorting_filtering_and_pagination_together(client: TestClient) -> None:
    from bedrock.api import runs_api

    _persist_run(
        runs_api.store,
        run_id="run-a",
        timestamp="2026-03-19T10:00:00Z",
        parcel_id="parcel-a",
        units=4,
        projected_profit=310000.0,
        roi=0.22,
    )
    _persist_run(
        runs_api.store,
        run_id="run-b",
        timestamp="2026-03-19T11:00:00Z",
        parcel_id="parcel-b",
        units=6,
        projected_profit=470000.0,
        roi=0.34,
    )
    _persist_run(
        runs_api.store,
        run_id="run-c",
        timestamp="2026-03-19T12:00:00Z",
        parcel_id="parcel-c",
        units=8,
        projected_profit=720000.0,
        roi=0.58,
    )
    _persist_run(
        runs_api.store,
        run_id="run-d",
        timestamp="2026-03-19T13:00:00Z",
        parcel_id="parcel-d",
        units=7,
        projected_profit=530000.0,
        roi=0.41,
    )

    response = client.get("/runs?min_units=5&max_ROI=0.50&sort=projected_profit&order=desc&limit=1&offset=1")

    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()] == ["run-b"]


def test_get_run_404_when_missing(client: TestClient) -> None:
    response = client.get("/runs/missing-run")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "run_not_found"
