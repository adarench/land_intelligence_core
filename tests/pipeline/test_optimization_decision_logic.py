"""Tests for optimization decision classification and pipeline calibration fixes."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout_candidate_batch import LayoutSearchPlan
from bedrock.contracts.layout_result import LayoutResult
from bedrock.contracts.optimization_run import (
    CandidateSensitivity,
    EconomicScenario,
    OptimizationCandidate,
    OptimizationDecision,
    OptimizationObjective,
)
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.pipeline_service import PipelineService


def _make_feasibility(*, roi: float, confidence: float, risk_score: float, profit: float = 100000, units: int = 10) -> FeasibilityResult:
    return FeasibilityResult(
        scenario_id="test-scenario",
        parcel_id="test-parcel",
        layout_id="test-layout",
        units=units,
        estimated_home_price=400000,
        price_per_sqft=200,
        estimated_home_size_sqft=2000,
        construction_cost_per_sqft=130,
        construction_cost_per_home=260000,
        development_cost_total=50000,
        projected_revenue=units * 400000,
        projected_cost=units * 260000 + 50000,
        projected_profit=profit,
        ROI=roi,
        ROI_base=roi,
        ROI_best_case=roi * 1.3,
        ROI_worst_case=roi * 0.6,
        break_even_price=260000,
        profit_margin=0.2,
        revenue_per_unit=400000,
        cost_per_unit=260000,
        risk_score=risk_score,
        confidence=confidence,
        confidence_score=confidence,
        key_risk_factors=[],
        status="feasible",
    )


def _make_candidate(*, roi: float, confidence: float, risk_score: float, profit: float = 100000, density_factor: float = 0.8) -> OptimizationCandidate:
    return OptimizationCandidate(
        layout_result=LayoutResult(
            schema_name="LayoutResult",
            schema_version="1.0.0",
            layout_id="test-layout",
            parcel_id="test-parcel",
            unit_count=10,
            road_length_ft=500,
            lot_geometries=[],
            road_geometries=[],
        ),
        feasibility_result=_make_feasibility(roi=roi, confidence=confidence, risk_score=risk_score, profit=profit),
        strategy_parameters=LayoutSearchPlan(
            label="test",
            density_factor=density_factor,
            lot_depth_factor=1.0,
            max_candidates=10,
        ),
        objective_score=0.5,
        optimization_rank=1,
    )


def _make_zoning() -> ZoningRules:
    return ZoningRules(
        schema_name="ZoningRules",
        schema_version="1.0.0",
        parcel_id="test-parcel",
        district="R-1",
        overlays=[],
        setbacks={"front": 25, "side": 8, "rear": 20},
        min_lot_size_sqft=6000,
        max_units_per_acre=5,
    )


class TestOptimizationDecisionClassification:
    """Verify _build_optimization_decision produces correct recommendations."""

    def test_acquire_when_roi_confidence_risk_all_pass(self):
        candidate = _make_candidate(roi=0.20, confidence=0.80, risk_score=0.30)
        decision = PipelineService._build_optimization_decision(
            best_candidate=candidate,
            zoning_rules=_make_zoning(),
            ranking_metrics={"candidate_count": 5},
            sensitivity_analysis=[],
            economic_scenarios=[],
        )
        assert decision is not None
        assert decision.recommendation == "acquire"

    def test_renegotiate_when_roi_positive_but_below_threshold(self):
        candidate = _make_candidate(roi=0.08, confidence=0.80, risk_score=0.30, profit=50000)
        decision = PipelineService._build_optimization_decision(
            best_candidate=candidate,
            zoning_rules=_make_zoning(),
            ranking_metrics={"candidate_count": 5},
            sensitivity_analysis=[],
            economic_scenarios=[],
        )
        assert decision is not None
        assert decision.recommendation == "renegotiate_price"

    def test_renegotiate_when_risk_too_high(self):
        candidate = _make_candidate(roi=0.25, confidence=0.80, risk_score=0.50)
        decision = PipelineService._build_optimization_decision(
            best_candidate=candidate,
            zoning_rules=_make_zoning(),
            ranking_metrics={"candidate_count": 5},
            sensitivity_analysis=[],
            economic_scenarios=[],
        )
        assert decision is not None
        assert decision.recommendation == "renegotiate_price"

    def test_renegotiate_when_confidence_too_low(self):
        candidate = _make_candidate(roi=0.25, confidence=0.50, risk_score=0.30)
        decision = PipelineService._build_optimization_decision(
            best_candidate=candidate,
            zoning_rules=_make_zoning(),
            ranking_metrics={"candidate_count": 5},
            sensitivity_analysis=[],
            economic_scenarios=[],
        )
        assert decision is not None
        assert decision.recommendation == "renegotiate_price"

    def test_pursue_rezoning_when_negative_roi_and_density_maxed(self):
        candidate = _make_candidate(roi=-0.10, confidence=0.70, risk_score=0.60, profit=-50000, density_factor=0.98)
        decision = PipelineService._build_optimization_decision(
            best_candidate=candidate,
            zoning_rules=_make_zoning(),
            ranking_metrics={"candidate_count": 5},
            sensitivity_analysis=[],
            economic_scenarios=[],
        )
        assert decision is not None
        assert decision.recommendation == "pursue_rezoning"

    def test_abandon_when_negative_roi_and_density_not_maxed(self):
        candidate = _make_candidate(roi=-0.10, confidence=0.70, risk_score=0.60, profit=-50000, density_factor=0.70)
        decision = PipelineService._build_optimization_decision(
            best_candidate=candidate,
            zoning_rules=_make_zoning(),
            ranking_metrics={"candidate_count": 5},
            sensitivity_analysis=[],
            economic_scenarios=[],
        )
        assert decision is not None
        assert decision.recommendation == "abandon"

    def test_abandon_when_no_candidate(self):
        decision = PipelineService._build_optimization_decision(
            best_candidate=None,
            zoning_rules=_make_zoning(),
            ranking_metrics={"candidate_count": 0},
            sensitivity_analysis=[],
            economic_scenarios=[],
        )
        assert decision is not None
        assert decision.recommendation == "abandon"
        assert "no_viable_candidate_found" in decision.sensitivity

    def test_acquire_threshold_is_15_percent(self):
        """Verify the acquire threshold matches the agreed 15% hurdle rate."""
        just_above = _make_candidate(roi=0.15, confidence=0.65, risk_score=0.45)
        decision = PipelineService._build_optimization_decision(
            best_candidate=just_above,
            zoning_rules=_make_zoning(),
            ranking_metrics={"candidate_count": 1},
            sensitivity_analysis=[],
            economic_scenarios=[],
        )
        assert decision.recommendation == "acquire"

        just_below = _make_candidate(roi=0.149, confidence=0.65, risk_score=0.45)
        decision = PipelineService._build_optimization_decision(
            best_candidate=just_below,
            zoning_rules=_make_zoning(),
            ranking_metrics={"candidate_count": 1},
            sensitivity_analysis=[],
            economic_scenarios=[],
        )
        assert decision.recommendation == "renegotiate_price"


class TestDecisionStore:
    """Verify decision CRUD operations."""

    def test_create_and_load(self, tmp_path):
        from bedrock.contracts.decision import DecisionRecord
        from bedrock.services.decision_store import DecisionStore
        from datetime import datetime, timezone

        store = DecisionStore(decisions_dir=tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        record = DecisionRecord(
            decision_id="test-1",
            parcel_id="parcel-1",
            system_recommendation="acquire",
            user_action="acquire",
            status="decided",
            created_at=now,
            updated_at=now,
        )
        store.save(record)
        loaded = store.load("test-1")
        assert loaded.decision_id == "test-1"
        assert loaded.user_action == "acquire"
        assert loaded.status == "decided"

    def test_list_filter_by_parcel(self, tmp_path):
        from bedrock.contracts.decision import DecisionRecord
        from bedrock.services.decision_store import DecisionStore
        from datetime import datetime, timezone

        store = DecisionStore(decisions_dir=tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        for i, pid in enumerate(["p1", "p1", "p2"]):
            store.save(DecisionRecord(
                decision_id=f"d-{i}",
                parcel_id=pid,
                status="new",
                created_at=now,
                updated_at=now,
            ))
        assert len(store.list_decisions(parcel_id="p1")) == 2
        assert len(store.list_decisions(parcel_id="p2")) == 1

    def test_update_fields(self, tmp_path):
        from bedrock.contracts.decision import DecisionRecord
        from bedrock.services.decision_store import DecisionStore
        from datetime import datetime, timezone

        store = DecisionStore(decisions_dir=tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        store.save(DecisionRecord(
            decision_id="d-update",
            parcel_id="p1",
            status="new",
            created_at=now,
            updated_at=now,
        ))
        updated = store.update("d-update", status="decided", user_action="pass", notes="Not viable")
        assert updated.status == "decided"
        assert updated.user_action == "pass"
        assert updated.notes == "Not viable"

    def test_load_missing_raises(self, tmp_path):
        from bedrock.services.decision_store import DecisionStore

        store = DecisionStore(decisions_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            store.load("nonexistent")
