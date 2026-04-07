from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT, REPO_ROOT / "bedrock"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from bedrock.models.carry_cost_models import (
    CarryCostAssessment,
    calculate_carry_cost,
    resolve_absorption_rate,
    DEFAULT_ABSORPTION_RATE,
    DEFAULT_INTEREST_RATE,
)
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.market_data import MarketData
from bedrock.services.feasibility_service import FeasibilityService


class CarryCostModelsTest(unittest.TestCase):
    def test_basic_carry_cost(self) -> None:
        result = calculate_carry_cost(
            units=30,
            total_capital=3000000.0,
            absorption_rate=3.0,
            interest_rate=0.075,
        )
        # 30 units / 3 per month = 10 months = 0.833 years
        # avg outstanding = 3000000 * 0.6 = 1800000
        # carry = 1800000 * 0.075 * 0.833 = 112500
        self.assertEqual(result.project_duration_months, 10.0)
        self.assertAlmostEqual(result.carry_cost, 1800000 * 0.075 * (10 / 12), places=0)

    def test_zero_units_no_carry(self) -> None:
        result = calculate_carry_cost(units=0, total_capital=1000000.0)
        self.assertEqual(result.carry_cost, 0.0)

    def test_zero_capital_no_carry(self) -> None:
        result = calculate_carry_cost(units=10, total_capital=0.0)
        self.assertEqual(result.carry_cost, 0.0)

    def test_large_project_significant_carry(self) -> None:
        result = calculate_carry_cost(
            units=150,
            total_capital=20000000.0,
            absorption_rate=3.0,
            interest_rate=0.075,
        )
        # 150 units / 3 = 50 months = 4.17 years
        self.assertEqual(result.project_duration_months, 50.0)
        self.assertGreater(result.carry_cost, 500000.0)

    def test_absorption_rate_lookup(self) -> None:
        rate_known = resolve_absorption_rate(jurisdiction="Eagle Mountain")
        rate_unknown = resolve_absorption_rate(jurisdiction="Nonexistent City")
        self.assertGreater(rate_known, DEFAULT_ABSORPTION_RATE)
        self.assertEqual(rate_unknown, DEFAULT_ABSORPTION_RATE)

    def test_none_jurisdiction_returns_default(self) -> None:
        rate = resolve_absorption_rate(jurisdiction=None)
        self.assertEqual(rate, DEFAULT_ABSORPTION_RATE)


class CarryCostIntegrationTest(unittest.TestCase):
    def test_carry_cost_opt_in_via_enrichment_context(self) -> None:
        parcel = Parcel(
            parcel_id="carry-001",
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            area=43560.0,
            jurisdiction="Example City",
        )
        layout = SubdivisionLayout(
            layout_id="layout-carry",
            parcel_id="carry-001",
            lot_count=30,
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

        # Without carry cost
        result_no_carry = service.evaluate(parcel=parcel, layout=layout, market_data=market_data)

        # With carry cost enabled
        result_with_carry = service.evaluate(
            parcel=parcel, layout=layout, market_data=market_data,
            enrichment_context={"include_carry_cost": True},
        )

        self.assertGreater(result_with_carry.projected_cost, result_no_carry.projected_cost)
        carry_delta = result_with_carry.projected_cost - result_no_carry.projected_cost
        self.assertGreater(carry_delta, 0.0)

        # Carry cost should be in breakdown
        self.assertGreater(
            result_with_carry.financial_summary["development_cost_breakdown"]["carry_cost"],
            0.0,
        )
        # No carry cost when disabled
        self.assertEqual(
            result_no_carry.financial_summary["development_cost_breakdown"]["carry_cost"],
            0.0,
        )

    def test_carry_cost_off_by_default(self) -> None:
        parcel = Parcel(
            parcel_id="carry-002",
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            area=43560.0,
            jurisdiction="Example City",
        )
        layout = SubdivisionLayout(
            layout_id="layout-no-carry",
            parcel_id="carry-002",
            lot_count=10,
            open_space_area=0.0,
            road_length=200.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=400000.0,
            cost_per_home=200000.0,
            road_cost_per_ft=200.0,
            soft_cost_factor=0.0,
        )

        service = FeasibilityService()
        result = service.evaluate(parcel=parcel, layout=layout, market_data=market_data)

        # Without explicit opt-in, carry cost should be zero
        self.assertEqual(
            result.financial_summary["development_cost_breakdown"]["carry_cost"],
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
