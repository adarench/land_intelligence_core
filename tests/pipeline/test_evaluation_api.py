from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))

from bedrock.api.evaluation_api import create_app
from bedrock.contracts.base import EngineMetadata
from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.parcel import Parcel
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
    from bedrock.api import evaluation_api

    run_store = PipelineRunStore(log_path=tmp_path / "pipeline_runs.jsonl", runs_dir=tmp_path / "runs")
    monkeypatch.setattr(
        "bedrock.api.evaluation_api.service",
        evaluation_api.PipelineRunEvaluationService(runs_dir=run_store.runs_dir),
    )
    return TestClient(create_app())


def test_benchmark_aggregates_metrics_from_stored_runs(client: TestClient) -> None:
    from bedrock.api import evaluation_api

    store = PipelineRunStore(runs_dir=evaluation_api.service.runs_dir)

    _persist_run(store, run_id="run-1", timestamp="2026-03-19T10:00:00Z", parcel_id="p1", units=4, projected_profit=300000.0, roi=0.20)
    _persist_run(store, run_id="run-2", timestamp="2026-03-19T10:05:00Z", parcel_id="p2", units=6, projected_profit=500000.0, roi=0.40)
    _persist_run(store, run_id="run-3", timestamp="2026-03-19T10:10:00Z", parcel_id="p3", units=5, projected_profit=400000.0, roi=0.30)

    response = client.post("/evaluation/benchmark", json={})

    assert response.status_code == 200
    payload = response.json()["candidate"]
    assert payload["run_count"] == 3
    assert payload["avg_ROI"] == pytest.approx(0.30)
    assert payload["avg_projected_profit"] == pytest.approx(400000.0)
    assert payload["avg_units"] == pytest.approx(5.0)
    assert payload["min_ROI"] == pytest.approx(0.20)
    assert payload["max_ROI"] == pytest.approx(0.40)


def test_benchmark_filters_min_roi_and_min_units(client: TestClient) -> None:
    from bedrock.api import evaluation_api

    store = PipelineRunStore(runs_dir=evaluation_api.service.runs_dir)

    _persist_run(store, run_id="run-a", timestamp="2026-03-19T10:00:00Z", parcel_id="pa", units=4, projected_profit=300000.0, roi=0.20)
    _persist_run(store, run_id="run-b", timestamp="2026-03-19T10:05:00Z", parcel_id="pb", units=6, projected_profit=500000.0, roi=0.40)
    _persist_run(store, run_id="run-c", timestamp="2026-03-19T10:10:00Z", parcel_id="pc", units=8, projected_profit=700000.0, roi=0.55)

    response = client.post(
        "/evaluation/benchmark",
        json={"candidate": {"filters": {"min_ROI": 0.4, "min_units": 7}}},
    )

    assert response.status_code == 200
    payload = response.json()["candidate"]
    assert payload["run_count"] == 1
    assert payload["avg_ROI"] == pytest.approx(0.55)
    assert payload["avg_projected_profit"] == pytest.approx(700000.0)
    assert payload["avg_units"] == pytest.approx(8.0)
    assert payload["min_ROI"] == pytest.approx(0.55)
    assert payload["max_ROI"] == pytest.approx(0.55)


def test_benchmark_comparison_returns_deterministic_delta(client: TestClient) -> None:
    from bedrock.api import evaluation_api

    store = PipelineRunStore(runs_dir=evaluation_api.service.runs_dir)

    _persist_run(store, run_id="base-1", timestamp="2026-03-19T10:00:00Z", parcel_id="b1", units=4, projected_profit=200000.0, roi=0.20)
    _persist_run(store, run_id="base-2", timestamp="2026-03-19T10:05:00Z", parcel_id="b2", units=5, projected_profit=300000.0, roi=0.30)
    _persist_run(store, run_id="new-1", timestamp="2026-03-19T10:10:00Z", parcel_id="n1", units=7, projected_profit=600000.0, roi=0.50)
    _persist_run(store, run_id="new-2", timestamp="2026-03-19T10:15:00Z", parcel_id="n2", units=8, projected_profit=700000.0, roi=0.60)

    request_payload = {
        "candidate": {"run_ids": ["new-2", "new-1"]},
        "baseline": {"run_ids": ["base-2", "base-1"]},
    }

    first = client.post("/evaluation/benchmark", json=request_payload)
    second = client.post("/evaluation/benchmark", json=request_payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    payload = first.json()
    assert payload["candidate"]["avg_ROI"] == pytest.approx(0.55)
    assert payload["baseline"]["avg_ROI"] == pytest.approx(0.25)
    assert payload["delta"]["avg_ROI"] == pytest.approx(0.30)
    assert payload["delta"]["avg_projected_profit"] == pytest.approx(400000.0)
    assert payload["delta"]["avg_units"] == pytest.approx(3.0)
    assert payload["delta"]["run_count"] == 0
