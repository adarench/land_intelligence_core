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

from bedrock.api.experiments_api import create_app
from bedrock.contracts.base import EngineMetadata
from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.zoning_rules import SetbackSet, ZoningRules
from bedrock.services.experiment_run_service import ExperimentRunService
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


def _layout_result(parcel_id: str, layout_id: str) -> SubdivisionLayout:
    return SubdivisionLayout(
        layout_id=layout_id,
        parcel_id=parcel_id,
        unit_count=4,
        road_length_ft=120.0,
        lot_geometries=[_geojson_polygon([(0, 0), (0, 4), (4, 4), (4, 0), (0, 0)])],
        road_geometries=[_geojson_polygon([(4, 0), (4, 10), (5, 10), (5, 0), (4, 0)])],
        open_space_area_sqft=200.0,
        utility_length_ft=0.0,
        score=0.91,
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
    experiments_dir = tmp_path / "experiments"
    runs_dir = tmp_path / "runs"
    service = ExperimentRunService(runs_dir=runs_dir, experiments_dir=experiments_dir)
    monkeypatch.setattr("bedrock.api.experiments_api.service", service)
    return TestClient(create_app())


def test_create_experiment_persists_metadata_and_run_grouping(client: TestClient) -> None:
    from bedrock.api import experiments_api

    store = PipelineRunStore(runs_dir=experiments_api.service.run_store.runs_dir)
    _persist_run(store, run_id="run-1", timestamp="2026-03-19T10:00:00Z", parcel_id="p1", units=4, projected_profit=300000.0, roi=0.20)
    _persist_run(store, run_id="run-2", timestamp="2026-03-19T10:05:00Z", parcel_id="p2", units=6, projected_profit=500000.0, roi=0.40)

    response = client.post(
        "/experiments/create",
        json={"run_ids": ["run-1", "run-2"], "config": {"label": "baseline", "filters": {"min_units": 4}}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_name"] == "ExperimentRun"
    assert payload["schema_version"] == "1.0.0"
    assert payload["experiment_id"]
    assert payload["run_ids"] == ["run-1", "run-2"]
    assert payload["config"] == {"label": "baseline", "filters": {"min_units": 4}}
    assert payload["timestamp"]

    persisted_path = experiments_api.service.experiment_store.experiments_dir / f"{payload['experiment_id']}.json"
    assert persisted_path.exists()
    assert json.loads(persisted_path.read_text()) == payload


def test_create_experiment_aggregates_metrics_from_selected_runs(client: TestClient) -> None:
    from bedrock.api import experiments_api

    store = PipelineRunStore(runs_dir=experiments_api.service.run_store.runs_dir)
    _persist_run(store, run_id="run-a", timestamp="2026-03-19T10:00:00Z", parcel_id="pa", units=4, projected_profit=300000.0, roi=0.20)
    _persist_run(store, run_id="run-b", timestamp="2026-03-19T10:05:00Z", parcel_id="pb", units=6, projected_profit=500000.0, roi=0.40)
    _persist_run(store, run_id="run-c", timestamp="2026-03-19T10:10:00Z", parcel_id="pc", units=8, projected_profit=700000.0, roi=0.55)

    response = client.post("/experiments/create", json={"run_ids": ["run-b", "run-c"], "config": {"label": "candidate"}})

    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["run_count"] == 2
    assert metrics["avg_ROI"] == pytest.approx(0.475)
    assert metrics["avg_projected_profit"] == pytest.approx(600000.0)
    assert metrics["avg_units"] == pytest.approx(7.0)
    assert metrics["min_ROI"] == pytest.approx(0.40)
    assert metrics["max_ROI"] == pytest.approx(0.55)


def test_experiment_get_is_reproducible_and_matches_stored_json(client: TestClient) -> None:
    from bedrock.api import experiments_api

    store = PipelineRunStore(runs_dir=experiments_api.service.run_store.runs_dir)
    _persist_run(store, run_id="run-x", timestamp="2026-03-19T10:00:00Z", parcel_id="px", units=5, projected_profit=400000.0, roi=0.30)
    _persist_run(store, run_id="run-y", timestamp="2026-03-19T10:05:00Z", parcel_id="py", units=7, projected_profit=650000.0, roi=0.50)

    created = client.post("/experiments/create", json={"run_ids": ["run-x", "run-y"], "config": {"label": "repro"}})
    assert created.status_code == 200
    experiment_id = created.json()["experiment_id"]

    first = client.get(f"/experiments/{experiment_id}")
    second = client.get(f"/experiments/{experiment_id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["schema_name"] == "ExperimentRun"
    assert first.json()["schema_version"] == "1.0.0"
    assert first.json() == second.json()

    persisted_path = experiments_api.service.experiment_store.experiments_dir / f"{experiment_id}.json"
    assert first.json() == json.loads(persisted_path.read_text())


def test_get_experiment_validates_and_adapts_support_contract_defaults(client: TestClient) -> None:
    from bedrock.api import experiments_api

    experiment_id = "exp-legacy"
    persisted_path = experiments_api.service.experiment_store.experiments_dir / f"{experiment_id}.json"
    persisted_path.parent.mkdir(parents=True, exist_ok=True)
    persisted_path.write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "run_ids": ["run-a", "run-b"],
                "config": {"label": "legacy"},
                "metrics": {"run_count": 2, "avg_ROI": 0.5},
            }
        ),
        encoding="utf-8",
    )

    response = client.get(f"/experiments/{experiment_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_name"] == "ExperimentRun"
    assert payload["schema_version"] == "1.0.0"
    assert payload["experiment_id"] == experiment_id
    assert payload["run_ids"] == ["run-a", "run-b"]
