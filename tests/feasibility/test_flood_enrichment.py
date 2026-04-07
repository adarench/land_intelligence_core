from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT, REPO_ROOT / "bedrock"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from bedrock.models.flood_models import (
    FloodAssessment,
    assess_flood_risk,
    calculate_flood_cost_adjustment,
)
from bedrock.services.flood_enrichment_service import FloodEnrichmentService
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.market_data import MarketData
from bedrock.services.feasibility_service import FeasibilityService


class FloodModelsTest(unittest.TestCase):
    def test_no_flood_zone_returns_no_risk(self) -> None:
        result = assess_flood_risk(flood_zone=None, flood_area_ratio=0.0, parcel_area_sqft=43560.0)
        self.assertEqual(result.cost_multiplier, 1.0)
        self.assertFalse(result.is_high_risk)
        self.assertEqual(result.buildable_area_reduction_sqft, 0.0)

    def test_zone_x_returns_no_risk(self) -> None:
        result = assess_flood_risk(flood_zone="X", flood_area_ratio=0.0, parcel_area_sqft=43560.0)
        self.assertEqual(result.cost_multiplier, 1.0)
        self.assertFalse(result.is_high_risk)

    def test_zone_ae_is_high_risk(self) -> None:
        result = assess_flood_risk(flood_zone="AE", flood_area_ratio=0.5, parcel_area_sqft=43560.0)
        self.assertTrue(result.is_high_risk)
        self.assertGreater(result.cost_multiplier, 1.0)
        self.assertGreater(result.buildable_area_reduction_sqft, 0.0)

    def test_full_ae_overlap_max_multiplier(self) -> None:
        result = assess_flood_risk(flood_zone="AE", flood_area_ratio=1.0, parcel_area_sqft=100000.0)
        self.assertTrue(result.is_high_risk)
        self.assertAlmostEqual(result.cost_multiplier, 1.25)

    def test_moderate_risk_zone(self) -> None:
        result = assess_flood_risk(flood_zone="X500", flood_area_ratio=0.5, parcel_area_sqft=43560.0)
        self.assertFalse(result.is_high_risk)
        self.assertGreater(result.cost_multiplier, 1.0)
        self.assertLess(result.cost_multiplier, 1.10)

    def test_cost_adjustment_adds_delta(self) -> None:
        assessment = assess_flood_risk(flood_zone="AE", flood_area_ratio=0.5, parcel_area_sqft=43560.0)
        adjustment = calculate_flood_cost_adjustment(development_cost=100000.0, assessment=assessment)
        self.assertGreater(adjustment, 0.0)
        expected = 100000.0 * (assessment.cost_multiplier - 1.0)
        self.assertAlmostEqual(adjustment, expected, places=2)

    def test_cost_adjustment_zero_for_no_risk(self) -> None:
        assessment = assess_flood_risk(flood_zone="X", flood_area_ratio=0.0, parcel_area_sqft=43560.0)
        adjustment = calculate_flood_cost_adjustment(development_cost=100000.0, assessment=assessment)
        self.assertEqual(adjustment, 0.0)


class FloodEnrichmentServiceTest(unittest.TestCase):
    def test_parcel_with_flood_zone_uses_it(self) -> None:
        parcel = Parcel(
            parcel_id="flood-test-001",
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            area=43560.0,
            jurisdiction="Test City",
            flood_zone="AE",
        )
        service = FloodEnrichmentService()
        result = service.assess(parcel)
        self.assertTrue(result.is_high_risk)
        self.assertEqual(result.flood_zone, "AE")

    def test_parcel_without_flood_zone_returns_no_risk(self) -> None:
        parcel = Parcel(
            parcel_id="flood-test-002",
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            area=43560.0,
            jurisdiction="Test City",
        )
        service = FloodEnrichmentService()
        result = service.assess(parcel)
        self.assertFalse(result.is_high_risk)
        self.assertEqual(result.flood_area_ratio, 0.0)


class FloodIntegrationTest(unittest.TestCase):
    def test_flood_cost_flows_through_enrichment_context(self) -> None:
        parcel = Parcel(
            parcel_id="flood-integ-001",
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            area=43560.0,
            jurisdiction="Example City",
        )
        layout = SubdivisionLayout(
            layout_id="layout-flood",
            parcel_id="flood-integ-001",
            lot_count=10,
            open_space_area=0.0,
            road_length=500.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=450000.0,
            cost_per_home=250000.0,
            road_cost_per_ft=300.0,
            soft_cost_factor=0.0,
        )

        service = FeasibilityService()

        # Without flood enrichment
        result_no_flood = service.evaluate(parcel=parcel, layout=layout, market_data=market_data)

        # With flood enrichment context
        enrichment = {
            "flood_zone": "AE",
            "flood_area_ratio": 0.5,
            "flood_cost_adjustment": 50000.0,
        }
        result_with_flood = service.evaluate(
            parcel=parcel, layout=layout, market_data=market_data,
            enrichment_context=enrichment,
        )

        self.assertGreater(result_with_flood.projected_cost, result_no_flood.projected_cost)
        self.assertAlmostEqual(
            result_with_flood.projected_cost - result_no_flood.projected_cost,
            50000.0,
            places=2,
        )
        self.assertEqual(result_with_flood.assumptions["flood_zone"], "AE")
        self.assertEqual(result_with_flood.assumptions["flood_area_ratio"], 0.5)
        self.assertEqual(
            result_with_flood.financial_summary["development_cost_breakdown"]["flood_cost_adjustment"],
            50000.0,
        )


if __name__ == "__main__":
    unittest.main()
