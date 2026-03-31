from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))

from bedrock.api.optimization_api import create_app
from bedrock.contracts.base import EngineMetadata
from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.optimization_run import (
    OptimizationCandidate,
    OptimizationDecision,
    OptimizationObjective,
    OptimizationRun,
)
from bedrock.contracts.zoning_rules import SetbackSet, ZoningRules
from bedrock.services.pipeline_run_store import PipelineRunStore


def _geojson_polygon(coords: list[tuple[float, float]]) -> dict:
    return {"type": "Polygon", "coordinates": [[list(point) for point in coords]]}


def _zoning_rules(parcel_id: str) -> ZoningRules:
    return ZoningRules(
        parcel_id=parcel_id,
        jurisdiction="Test City",
        district="R-1",
        min_lot_size_sqft=8000,
        max_units_per_acre=4,
        setbacks=SetbackSet(front=25, side=8, rear=20),
        metadata=EngineMetadata(
            source_engine="zoning_data_scraper",
            source_run_id="test",
            source_type="real_lookup",
            legal_reliability=True,
        ),
    )


def _layout(parcel_id: str, layout_id: str) -> SubdivisionLayout:
    return SubdivisionLayout(
        layout_id=layout_id,
        parcel_id=parcel_id,
        unit_count=4,
        road_length_ft=120.0,
        lot_geometries=[_geojson_polygon([(0, 0), (0, 4), (4, 4), (4, 0), (0, 0)])],
        road_geometries=[{"type": "LineString", "coordinates": [[4, 0], [4, 10]]}],
        open_space_area_sqft=200.0,
        utility_length_ft=0.0,
        score=0.91,
        metadata=EngineMetadata(source_engine="bedrock.layout_service"),
    )


def _feasibility(parcel_id: str, layout_id: str, roi: float, profit: float) -> FeasibilityResult:
    return FeasibilityResult(
        scenario_id=f"scenario-{layout_id}",
        parcel_id=parcel_id,
        layout_id=layout_id,
        units=4,
        estimated_home_price=480000.0,
        construction_cost_per_home=260000.0,
        development_cost_total=500000.0,
        projected_revenue=1920000.0,
        projected_cost=1540000.0,
        projected_profit=profit,
        ROI=roi,
        risk_score=0.18,
        confidence=0.9,
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    store = PipelineRunStore(
        log_path=tmp_path / "pipeline_runs.jsonl",
        runs_dir=tmp_path / "runs",
        optimization_runs_dir=tmp_path / "optimization_runs",
    )
    monkeypatch.setattr("bedrock.api.optimization_api.store", store)
    return TestClient(create_app())


def _persist_optimization_run(store: PipelineRunStore, optimization_run_id: str) -> OptimizationRun:
    parcel_id = "parcel-001"
    layout = _layout(parcel_id, "layout-001")
    feasibility = _feasibility(parcel_id, layout.layout_id, 0.24, 380000.0)
    candidate = OptimizationCandidate(
        layout_result=layout,
        feasibility_result=feasibility,
        strategy_parameters={
            "label": "broad_sampling",
            "strategies": ["grid", "spine-road"],
            "max_candidates": 24,
            "max_layouts": 3,
            "density_factor": 1.0,
            "lot_depth_factor": 1.0,
            "frontage_hint_factor": 1.0,
            "road_width_factor": 1.0,
            "runtime_budget_factor": 1.0,
        },
        objective_score=0.61,
        optimization_rank=1,
    )
    run = OptimizationRun(
        optimization_run_id=optimization_run_id,
        parcel_id=parcel_id,
        zoning_result=_zoning_rules(parcel_id),
        layout_candidates=[candidate],
        best_candidate=candidate,
        ranking_metrics={"candidate_count": 1, "best_objective_score": 0.61},
        objective=OptimizationObjective(),
        scenario_evaluation={
            "parcel_id": parcel_id,
            "layout_count": 1,
            "best_layout_id": layout.layout_id,
            "best_roi": feasibility.ROI,
            "best_profit": feasibility.projected_profit,
            "best_units": feasibility.units,
            "layouts_ranked": [feasibility.model_dump(mode="json")],
        },
        decision=OptimizationDecision(
            recommendation="acquire",
            best_layout_id=layout.layout_id,
            expected_roi_base=feasibility.ROI,
            expected_roi_best_case=feasibility.ROI_best_case,
            expected_roi_worst_case=feasibility.ROI_worst_case,
            sensitivity=[],
            rationale="test",
        ),
        selected_pipeline_run_id="run-001",
        timestamp="2026-03-30T00:00:00Z",
    )
    store.save_optimization_run(optimization_run_id, run)
    return run


def test_list_optimization_runs_returns_summary(client: TestClient) -> None:
    from bedrock.api import optimization_api

    _persist_optimization_run(optimization_api.store, "opt-001")

    response = client.get("/optimization/runs")

    assert response.status_code == 200
    assert response.json() == [
        {
            "optimization_run_id": "opt-001",
            "timestamp": "2026-03-30T00:00:00Z",
            "parcel_id": "parcel-001",
            "candidate_count": 1,
            "best_layout_id": "layout-001",
            "best_roi": 0.24,
            "best_projected_profit": 380000.0,
            "selected_pipeline_run_id": "run-001",
        }
    ]


def test_get_optimization_run_returns_exact_payload(client: TestClient) -> None:
    from bedrock.api import optimization_api

    expected = _persist_optimization_run(optimization_api.store, "opt-002")

    response = client.get("/optimization/runs/opt-002")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_name"] == "OptimizationRun"
    assert payload["optimization_run_id"] == "opt-002"
    assert payload == expected.model_dump(mode="json")
