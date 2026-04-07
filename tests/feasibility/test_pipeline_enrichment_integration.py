"""Integration tests verifying enrichment services are actually invoked during pipeline execution.

These tests validate the PIPELINE-level behaviour — not just the service in isolation.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT, REPO_ROOT / "bedrock"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from bedrock.contracts.parcel import Parcel
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.market_data import MarketData
from bedrock.models.flood_models import FloodAssessment
from bedrock.services.feasibility_service import FeasibilityService
from bedrock.services.flood_enrichment_service import FloodEnrichmentService
from bedrock.services.slope_enrichment_service import SlopeEnrichmentService
from bedrock.services.pipeline_service import PipelineService


def _make_parcel(**kwargs) -> Parcel:
    defaults = dict(
        parcel_id="integ-001",
        geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
        area=435600.0,
        jurisdiction="Example City",
    )
    defaults.update(kwargs)
    return Parcel(**defaults)


def _make_layout(parcel_id: str = "integ-001") -> SubdivisionLayout:
    return SubdivisionLayout(
        layout_id="layout-integ",
        parcel_id=parcel_id,
        lot_count=20,
        open_space_area=0.0,
        road_length=1500.0,
        utility_length=0.0,
    )


def _make_market() -> MarketData:
    return MarketData(
        estimated_home_price=400000.0,
        construction_cost_per_home=250000.0,
        road_cost_per_ft=300.0,
        soft_cost_factor=0.0,
    )


# ---------------------------------------------------------------------------
# Test 1: Enrichment services are actually called during pipeline build
# ---------------------------------------------------------------------------
class TestEnrichmentServicesInvoked(unittest.TestCase):
    def test_build_enrichment_context_calls_flood_service(self) -> None:
        flood_service = MagicMock(spec=FloodEnrichmentService)
        flood_service.assess.return_value = FloodAssessment(
            flood_zone="AE", flood_area_ratio=0.4, cost_multiplier=1.10,
            is_high_risk=True, buildable_area_reduction_sqft=10000.0,
        )
        pipeline = PipelineService(flood_enrichment_service=flood_service)
        parcel = _make_parcel()

        ctx = pipeline._build_enrichment_context(parcel)

        flood_service.assess.assert_called_once_with(parcel)
        self.assertEqual(ctx["flood_zone"], "AE")
        self.assertAlmostEqual(ctx["flood_area_ratio"], 0.4)
        self.assertAlmostEqual(ctx["flood_cost_multiplier"], 1.10)
        self.assertTrue(ctx["flood_is_high_risk"])

    def test_build_enrichment_context_calls_slope_service(self) -> None:
        slope_service = MagicMock(spec=SlopeEnrichmentService)
        slope_service.compute_slope.return_value = 12.5

        pipeline = PipelineService(slope_enrichment_service=slope_service)
        parcel = _make_parcel()

        pipeline._build_enrichment_context(parcel)

        slope_service.compute_slope.assert_called_once_with(parcel)
        self.assertEqual(parcel.slope_percent, 12.5)

    def test_build_enrichment_context_handles_flood_service_failure(self) -> None:
        flood_service = MagicMock(spec=FloodEnrichmentService)
        flood_service.assess.side_effect = RuntimeError("NFHL data unavailable")

        pipeline = PipelineService(flood_enrichment_service=flood_service)
        parcel = _make_parcel()

        ctx = pipeline._build_enrichment_context(parcel)

        # Should not crash, flood keys should be absent
        self.assertNotIn("flood_zone", ctx)

    def test_build_enrichment_context_handles_slope_service_failure(self) -> None:
        slope_service = MagicMock(spec=SlopeEnrichmentService)
        slope_service.compute_slope.side_effect = RuntimeError("DEM unavailable")

        pipeline = PipelineService(slope_enrichment_service=slope_service)
        parcel = _make_parcel()

        ctx = pipeline._build_enrichment_context(parcel)

        # Should not crash
        self.assertIsNone(parcel.slope_percent)

    def test_no_services_produces_empty_context(self) -> None:
        pipeline = PipelineService()
        parcel = _make_parcel()

        ctx = pipeline._build_enrichment_context(parcel)

        self.assertNotIn("flood_zone", ctx)
        self.assertNotIn("include_carry_cost", ctx)


# ---------------------------------------------------------------------------
# Test 2: Cost impact — with vs without enrichment
# ---------------------------------------------------------------------------
class TestCostImpactValidation(unittest.TestCase):
    def test_flood_enrichment_increases_projected_cost(self) -> None:
        service = FeasibilityService()
        parcel = _make_parcel()
        layout = _make_layout()
        md = _make_market()

        result_no_flood = service.evaluate(parcel=parcel, layout=layout, market_data=md)
        # Use flood_cost_multiplier (the pipeline-native path)
        result_with_flood = service.evaluate(
            parcel=parcel, layout=layout, market_data=md,
            enrichment_context={
                "flood_zone": "AE",
                "flood_area_ratio": 0.5,
                "flood_cost_multiplier": 1.125,  # 12.5% cost premium
            },
        )

        self.assertGreater(
            result_with_flood.projected_cost,
            result_no_flood.projected_cost,
            "Flood enrichment should increase projected_cost",
        )
        flood_adj = result_with_flood.financial_summary["development_cost_breakdown"]["flood_cost_adjustment"]
        self.assertGreater(flood_adj, 0.0)

    def test_carry_cost_increases_projected_cost(self) -> None:
        service = FeasibilityService()
        parcel = _make_parcel()
        layout = _make_layout()
        md = _make_market()

        result_no_carry = service.evaluate(parcel=parcel, layout=layout, market_data=md)
        result_with_carry = service.evaluate(
            parcel=parcel, layout=layout, market_data=md,
            enrichment_context={"include_carry_cost": True},
        )

        self.assertGreater(
            result_with_carry.projected_cost,
            result_no_carry.projected_cost,
            "Carry cost should increase projected_cost",
        )
        carry = result_with_carry.financial_summary["development_cost_breakdown"]["carry_cost"]
        self.assertGreater(carry, 0.0)


# ---------------------------------------------------------------------------
# Test 3: Flood scenario with realistic numbers
# ---------------------------------------------------------------------------
class TestFloodScenario(unittest.TestCase):
    def test_high_risk_flood_zone_produces_cost_adjustment(self) -> None:
        service = FeasibilityService()
        parcel = _make_parcel(flood_zone="AE")
        layout = _make_layout()
        md = _make_market()

        # Simulate what the pipeline would compute via FloodEnrichmentService
        from bedrock.models.flood_models import assess_flood_risk, calculate_flood_cost_adjustment

        assessment = assess_flood_risk(
            flood_zone="AE", flood_area_ratio=0.5, parcel_area_sqft=435600.0,
        )
        self.assertTrue(assessment.is_high_risk)
        self.assertGreater(assessment.cost_multiplier, 1.0)

        # Feed through feasibility
        adjustment = calculate_flood_cost_adjustment(
            development_cost=500000.0,  # approx development cost
            assessment=assessment,
        )
        self.assertGreater(adjustment, 0.0)

        result = service.evaluate(
            parcel=parcel, layout=layout, market_data=md,
            enrichment_context={
                "flood_zone": "AE",
                "flood_area_ratio": 0.5,
                "flood_cost_multiplier": assessment.cost_multiplier,
            },
        )
        self.assertEqual(result.assumptions["flood_zone"], "AE")
        self.assertAlmostEqual(result.assumptions["flood_area_ratio"], 0.5)


# ---------------------------------------------------------------------------
# Test 4: Slope scenario
# ---------------------------------------------------------------------------
class TestSlopeScenario(unittest.TestCase):
    def test_steep_slope_increases_grading_cost(self) -> None:
        service = FeasibilityService()
        md = _make_market()

        # Flat parcel
        flat = _make_parcel(parcel_id="flat-001", slope_percent=2.0)
        layout_flat = SubdivisionLayout(
            layout_id="l-flat", parcel_id="flat-001",
            lot_count=20, open_space_area=0.0, road_length=1500.0, utility_length=0.0,
        )
        result_flat = service.evaluate(parcel=flat, layout=layout_flat, market_data=md)

        # Steep parcel
        steep = _make_parcel(parcel_id="steep-001", slope_percent=20.0)
        layout_steep = SubdivisionLayout(
            layout_id="l-steep", parcel_id="steep-001",
            lot_count=20, open_space_area=0.0, road_length=1500.0, utility_length=0.0,
        )
        result_steep = service.evaluate(parcel=steep, layout=layout_steep, market_data=md)

        flat_grading = result_flat.financial_summary["development_cost_breakdown"]["grading"]
        steep_grading = result_steep.financial_summary["development_cost_breakdown"]["grading"]

        self.assertGreater(steep_grading, flat_grading)
        # 20% slope -> factor 0.32 vs 2% -> factor 0.08 (4x difference)
        self.assertGreater(steep_grading / max(flat_grading, 1), 3.0)


# ---------------------------------------------------------------------------
# Test 5: Carry cost toggle
# ---------------------------------------------------------------------------
class TestCarryCostToggle(unittest.TestCase):
    def test_carry_cost_zero_when_disabled(self) -> None:
        service = FeasibilityService()
        parcel = _make_parcel()
        layout = _make_layout()
        md = _make_market()

        result = service.evaluate(parcel=parcel, layout=layout, market_data=md)
        carry = result.financial_summary["development_cost_breakdown"]["carry_cost"]
        self.assertEqual(carry, 0.0)

    def test_carry_cost_positive_when_enabled(self) -> None:
        service = FeasibilityService()
        parcel = _make_parcel()
        layout = _make_layout()
        md = _make_market()

        result = service.evaluate(
            parcel=parcel, layout=layout, market_data=md,
            enrichment_context={"include_carry_cost": True},
        )
        carry = result.financial_summary["development_cost_breakdown"]["carry_cost"]
        self.assertGreater(carry, 0.0)

    def test_carry_cost_scales_with_units(self) -> None:
        service = FeasibilityService()
        md = _make_market()

        small = _make_parcel(parcel_id="small")
        layout_small = SubdivisionLayout(
            layout_id="l-sm", parcel_id="small",
            lot_count=10, open_space_area=0.0, road_length=500.0, utility_length=0.0,
        )
        r_small = service.evaluate(
            parcel=small, layout=layout_small, market_data=md,
            enrichment_context={"include_carry_cost": True},
        )

        large = _make_parcel(parcel_id="large", area=1000000.0)
        layout_large = SubdivisionLayout(
            layout_id="l-lg", parcel_id="large",
            lot_count=100, open_space_area=0.0, road_length=8000.0, utility_length=0.0,
        )
        r_large = service.evaluate(
            parcel=large, layout=layout_large, market_data=md,
            enrichment_context={"include_carry_cost": True},
        )

        carry_small = r_small.financial_summary["development_cost_breakdown"]["carry_cost"]
        carry_large = r_large.financial_summary["development_cost_breakdown"]["carry_cost"]
        self.assertGreater(carry_large, carry_small)


# ---------------------------------------------------------------------------
# Test 6: Risk score no longer double-counts soft_cost_factor
# ---------------------------------------------------------------------------
class TestRiskScoreNoDoubleCounting(unittest.TestCase):
    def test_risk_score_independent_of_soft_cost_factor(self) -> None:
        service = FeasibilityService()
        parcel = _make_parcel()
        layout = _make_layout()

        md_low = MarketData(
            estimated_home_price=400000.0, cost_per_home=250000.0,
            road_cost_per_ft=300.0, soft_cost_factor=0.04,
        )
        md_high = MarketData(
            estimated_home_price=400000.0, cost_per_home=250000.0,
            road_cost_per_ft=300.0, soft_cost_factor=0.18,
        )

        r_low = service.evaluate(parcel=parcel, layout=layout, market_data=md_low)
        r_high = service.evaluate(parcel=parcel, layout=layout, market_data=md_high)

        # Costs should differ (soft costs applied)
        self.assertGreater(r_high.projected_cost, r_low.projected_cost)

        # Risk scores should NOT differ due to soft_cost_factor alone.
        # They may still differ slightly due to ROI/profit differences,
        # but the gap should be small (not the 0.14 delta we'd see with double-counting).
        risk_delta = abs(r_high.risk_score - r_low.risk_score)
        self.assertLess(
            risk_delta, 0.10,
            f"Risk scores should not diverge by >=0.10 from soft_cost_factor alone "
            f"(low={r_low.risk_score:.3f}, high={r_high.risk_score:.3f}, delta={risk_delta:.3f})",
        )


# ---------------------------------------------------------------------------
# Test 7: Pipeline _build_enrichment_context with carry cost flag
# ---------------------------------------------------------------------------
class TestPipelineCarryCostFlag(unittest.TestCase):
    def test_carry_cost_flag_in_enrichment_context(self) -> None:
        pipeline = PipelineService()
        pipeline._include_carry_cost = True
        parcel = _make_parcel()

        ctx = pipeline._build_enrichment_context(parcel)
        self.assertTrue(ctx.get("include_carry_cost"))

    def test_carry_cost_flag_absent_by_default(self) -> None:
        pipeline = PipelineService()
        parcel = _make_parcel()

        ctx = pipeline._build_enrichment_context(parcel)
        self.assertNotIn("include_carry_cost", ctx)


if __name__ == "__main__":
    unittest.main()
