from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bedrock") not in sys.path:
    sys.path.insert(0, str(ROOT / "bedrock"))

from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.feasibility_result import FeasibilityScenario
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.parcel import Parcel
from bedrock.pipelines.parcel_feasibility_pipeline import ParcelFeasibilityPipeline


def _parcel() -> Parcel:
    return Parcel(
        parcel_id="parcel-route-001",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [0.0, 10.0], [10.0, 10.0], [10.0, 0.0], [0.0, 0.0]]],
        },
        area=1000.0,
        jurisdiction="Test City",
    )


def _layout(parcel_id: str) -> SubdivisionLayout:
    return SubdivisionLayout(
        layout_id="layout-route-001",
        parcel_id=parcel_id,
        lot_count=5,
        road_length=200.0,
        lot_geometries=[],
        road_geometries=[],
        open_space_area=0.0,
        utility_length=0.0,
        score=0.7,
    )


def _scenario(parcel_id: str, requested_units: int = 5) -> FeasibilityScenario:
    return FeasibilityScenario(
        scenario_id="scenario-route-001",
        parcel_id=parcel_id,
        requested_units=requested_units,
        assumptions={},
        constraints=[],
    )


def test_parcel_feasibility_pipeline_routes_to_canonical_service(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    parcel = _parcel()
    layout = _layout(parcel.parcel_id)
    scenario = _scenario(parcel.parcel_id)

    def _evaluate(*, parcel, layout, market_data):
        captured["parcel"] = parcel
        captured["layout"] = layout
        captured["market_data"] = market_data
        return FeasibilityResult(
            scenario_id="scenario-from-service",
            parcel_id=parcel.parcel_id,
            layout_id=layout.layout_id,
            units=layout.lot_count,
            estimated_home_price=480000.0,
            construction_cost_per_home=260000.0,
            development_cost_total=60000.0,
            projected_revenue=2400000.0,
            projected_cost=1360000.0,
            projected_profit=1040000.0,
            ROI=1040000.0 / 1360000.0,
            risk_score=0.2,
            confidence=0.9,
        )

    monkeypatch.setattr(
        ParcelFeasibilityPipeline.feasibility_service,
        "evaluate",
        _evaluate,
    )

    result = ParcelFeasibilityPipeline.score_layout(parcel, layout, [], scenario)

    assert captured["parcel"].parcel_id == parcel.parcel_id
    assert captured["layout"].layout_id == layout.layout_id
    assert captured["market_data"].estimated_home_price == 480000.0
    assert result.projected_revenue == 2400000.0
    assert result.projected_cost == 1360000.0


def test_parcel_feasibility_pipeline_preserves_constraint_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    parcel = _parcel()
    layout = _layout(parcel.parcel_id)
    scenario = _scenario(parcel.parcel_id, requested_units=7)

    def _evaluate(*, parcel, layout, market_data):
        return FeasibilityResult(
            scenario_id="scenario-from-service",
            parcel_id=parcel.parcel_id,
            layout_id=layout.layout_id,
            units=layout.lot_count,
            estimated_home_price=480000.0,
            construction_cost_per_home=260000.0,
            development_cost_total=60000.0,
            projected_revenue=2400000.0,
            projected_cost=1360000.0,
            projected_profit=1040000.0,
            ROI=1040000.0 / 1360000.0,
            risk_score=0.2,
            confidence=0.9,
            constraint_violations=[],
        )

    monkeypatch.setattr(
        ParcelFeasibilityPipeline.feasibility_service,
        "evaluate",
        _evaluate,
    )

    result = ParcelFeasibilityPipeline.score_layout(parcel, layout, [], scenario)

    assert "requested_units_exceed_layout_capacity" in result.constraint_violations
    assert "missing_development_standards" in result.constraint_violations
    assert result.status == "constrained"
    assert result.confidence == 0.45
