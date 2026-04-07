from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT, REPO_ROOT / "bedrock"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from bedrock.models.slope_models import assess_slope, compute_grading_multiplier
from bedrock.models.cost_models import DEFAULT_GRADING_COST_FACTOR
from bedrock.services.slope_enrichment_service import SlopeEnrichmentService
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.market_data import MarketData
from bedrock.services.feasibility_service import FeasibilityService


class SlopeModelsTest(unittest.TestCase):
    def test_flat_slope_lower_than_default(self) -> None:
        factor = compute_grading_multiplier(mean_slope_pct=2.0)
        self.assertEqual(factor, 0.08)
        self.assertLess(factor, DEFAULT_GRADING_COST_FACTOR)

    def test_moderate_slope(self) -> None:
        factor = compute_grading_multiplier(mean_slope_pct=7.0)
        self.assertEqual(factor, 0.15)

    def test_steep_slope(self) -> None:
        factor = compute_grading_multiplier(mean_slope_pct=18.0)
        self.assertEqual(factor, 0.32)

    def test_very_steep_slope(self) -> None:
        factor = compute_grading_multiplier(mean_slope_pct=30.0)
        self.assertEqual(factor, 0.45)

    def test_zero_slope(self) -> None:
        factor = compute_grading_multiplier(mean_slope_pct=0.0)
        self.assertEqual(factor, 0.08)

    def test_assess_slope_none_returns_default(self) -> None:
        factor = assess_slope(slope_percent=None)
        self.assertEqual(factor, DEFAULT_GRADING_COST_FACTOR)

    def test_assess_slope_with_value_returns_band(self) -> None:
        factor = assess_slope(slope_percent=12.0)
        self.assertEqual(factor, 0.22)


class SlopeEnrichmentServiceTest(unittest.TestCase):
    def test_parcel_with_slope_returns_it(self) -> None:
        parcel = Parcel(
            parcel_id="slope-001",
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            area=43560.0,
            jurisdiction="Test City",
            slope_percent=8.5,
        )
        service = SlopeEnrichmentService()
        result = service.compute_slope(parcel)
        self.assertEqual(result, 8.5)

    def test_parcel_without_slope_returns_none(self) -> None:
        parcel = Parcel(
            parcel_id="slope-002",
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            area=43560.0,
            jurisdiction="Test City",
        )
        service = SlopeEnrichmentService()
        result = service.compute_slope(parcel)
        self.assertIsNone(result)


class SlopeIntegrationTest(unittest.TestCase):
    def test_steep_parcel_has_higher_grading_cost(self) -> None:
        flat_parcel = Parcel(
            parcel_id="slope-flat",
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            area=43560.0,
            jurisdiction="Example City",
            slope_percent=2.0,
        )
        steep_parcel = Parcel(
            parcel_id="slope-steep",
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            area=43560.0,
            jurisdiction="Example City",
            slope_percent=20.0,
        )
        layout_flat = SubdivisionLayout(
            layout_id="layout-flat",
            parcel_id="slope-flat",
            lot_count=10,
            open_space_area=0.0,
            road_length=500.0,
            utility_length=0.0,
        )
        layout_steep = SubdivisionLayout(
            layout_id="layout-steep",
            parcel_id="slope-steep",
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
        result_flat = service.evaluate(parcel=flat_parcel, layout=layout_flat, market_data=market_data)
        result_steep = service.evaluate(parcel=steep_parcel, layout=layout_steep, market_data=market_data)

        # Steep parcel should have higher grading cost and therefore higher projected cost
        flat_grading = result_flat.financial_summary["development_cost_breakdown"]["grading"]
        steep_grading = result_steep.financial_summary["development_cost_breakdown"]["grading"]
        self.assertGreater(steep_grading, flat_grading)
        self.assertGreater(result_steep.projected_cost, result_flat.projected_cost)


if __name__ == "__main__":
    unittest.main()
