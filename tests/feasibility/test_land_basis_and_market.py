"""Tests for land basis modes, market model debug, and recommendation framing."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT, REPO_ROOT / "bedrock"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from bedrock.contracts.parcel import Parcel
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.market_data import MarketData
from bedrock.services.feasibility_service import FeasibilityService


def _parcel(parcel_id: str = "lb-001", jurisdiction: str = "South Jordan") -> Parcel:
    return Parcel(
        parcel_id=parcel_id,
        geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
        area=435600.0,
        jurisdiction=jurisdiction,
    )


def _layout(parcel_id: str = "lb-001") -> SubdivisionLayout:
    return SubdivisionLayout(
        layout_id="layout-lb",
        parcel_id=parcel_id,
        lot_count=40,
        open_space_area=0.0,
        road_length=3000.0,
        utility_length=0.0,
    )


class TestLandBasisConsistency(unittest.TestCase):
    def test_excluded_mode_produces_zero_land_cost(self) -> None:
        service = FeasibilityService()
        parcel = _parcel()
        layout = _layout()
        md = MarketData(
            estimated_home_price=400000.0,
            cost_per_home=250000.0,
            road_cost_per_ft=300.0,
            land_price=0.0,
            soft_cost_factor=0.0,
        )
        result = service.evaluate(
            parcel=parcel, layout=layout, market_data=md,
            enrichment_context={"land_basis_mode": "excluded"},
        )
        self.assertEqual(result.financial_summary["land_cost"], 0.0)
        self.assertEqual(result.assumptions["land_basis_mode"], "excluded")
        self.assertEqual(result.assumptions["land_basis_value"], 0.0)

    def test_user_mode_uses_provided_value(self) -> None:
        service = FeasibilityService()
        parcel = _parcel()
        layout = _layout()
        md = MarketData(
            estimated_home_price=400000.0,
            cost_per_home=250000.0,
            road_cost_per_ft=300.0,
            land_price=500000.0,
            soft_cost_factor=0.0,
        )
        result = service.evaluate(
            parcel=parcel, layout=layout, market_data=md,
            enrichment_context={"land_basis_mode": "user"},
        )
        self.assertEqual(result.financial_summary["land_cost"], 500000.0)
        self.assertEqual(result.assumptions["land_basis_mode"], "user")
        self.assertEqual(result.assumptions["land_basis_value"], 500000.0)

    def test_changing_land_basis_changes_roi(self) -> None:
        service = FeasibilityService()
        parcel = _parcel()
        layout = _layout()

        md_excluded = MarketData(
            estimated_home_price=400000.0, cost_per_home=250000.0,
            road_cost_per_ft=300.0, land_price=0.0, soft_cost_factor=0.0,
        )
        md_with_land = MarketData(
            estimated_home_price=400000.0, cost_per_home=250000.0,
            road_cost_per_ft=300.0, land_price=2000000.0, soft_cost_factor=0.0,
        )

        r_excluded = service.evaluate(parcel=parcel, layout=layout, market_data=md_excluded)
        r_with_land = service.evaluate(parcel=parcel, layout=layout, market_data=md_with_land)

        self.assertGreater(r_excluded.ROI or 0, r_with_land.ROI or 0)
        self.assertGreater(r_excluded.projected_profit, r_with_land.projected_profit)
        self.assertLess(r_excluded.projected_cost, r_with_land.projected_cost)


class TestMarketModelDebug(unittest.TestCase):
    def test_debug_populated_for_calibrated_jurisdiction(self) -> None:
        service = FeasibilityService()
        parcel = _parcel(jurisdiction="South Jordan")
        layout = _layout()

        result = service.evaluate(parcel=parcel, layout=layout)

        debug = result.assumptions.get("market_model_debug")
        self.assertIsNotNone(debug, "market_model_debug should be present in assumptions")
        self.assertIn("price_distribution", debug)
        self.assertIn("final_selected_price", debug)
        self.assertIn("product_type", debug)
        self.assertGreater(debug["final_selected_price"], 0)

    def test_sf_median_preferred_when_available(self) -> None:
        service = FeasibilityService()
        parcel = _parcel(jurisdiction="South Jordan")
        layout = _layout()

        result = service.evaluate(parcel=parcel, layout=layout)

        debug = result.assumptions.get("market_model_debug", {})
        dist = debug.get("price_distribution", {})
        sf_median = dist.get("sf_median")

        # South Jordan has sf_median_sale_price in calibration data
        if sf_median and sf_median > 0:
            self.assertEqual(
                debug["final_selected_price"], sf_median,
                "Should use SF median when available",
            )
            self.assertEqual(debug["product_type"], "single_family")

    def test_distribution_has_p25_p75(self) -> None:
        service = FeasibilityService()
        parcel = _parcel(jurisdiction="South Jordan")
        layout = _layout()

        result = service.evaluate(parcel=parcel, layout=layout)

        debug = result.assumptions.get("market_model_debug", {})
        dist = debug.get("price_distribution", {})
        self.assertIsNotNone(dist.get("p25"), "p25 should be present")
        self.assertIsNotNone(dist.get("p75"), "p75 should be present")
        self.assertGreater(dist["p75"], dist["p25"])

    def test_different_jurisdictions_produce_different_prices(self) -> None:
        service = FeasibilityService()

        r_sj = service.evaluate(
            parcel=_parcel("p1", "South Jordan"),
            layout=SubdivisionLayout(layout_id="l1", parcel_id="p1", lot_count=10, open_space_area=0.0, road_length=500.0, utility_length=0.0),
        )
        r_lehi = service.evaluate(
            parcel=_parcel("p2", "Lehi"),
            layout=SubdivisionLayout(layout_id="l2", parcel_id="p2", lot_count=10, open_space_area=0.0, road_length=500.0, utility_length=0.0),
        )

        self.assertNotEqual(r_sj.estimated_home_price, r_lehi.estimated_home_price)


class TestBackwardCompatibility(unittest.TestCase):
    def test_default_land_basis_mode_is_proxy(self) -> None:
        service = FeasibilityService()
        parcel = _parcel()
        layout = _layout()

        result = service.evaluate(parcel=parcel, layout=layout)

        self.assertEqual(result.assumptions.get("land_basis_mode"), "proxy")

    def test_no_enrichment_context_still_works(self) -> None:
        service = FeasibilityService()
        parcel = _parcel()
        layout = _layout()

        result = service.evaluate(parcel=parcel, layout=layout)

        self.assertIsNotNone(result.projected_cost)
        self.assertIsNotNone(result.ROI)
        self.assertIn("land_basis_value", result.assumptions)


class TestSensitivityUsesDistribution(unittest.TestCase):
    def test_calibrated_jurisdiction_uses_p25_p75_for_sensitivity(self) -> None:
        service = FeasibilityService()
        parcel = _parcel(jurisdiction="South Jordan")
        layout = _layout()

        result = service.evaluate(parcel=parcel, layout=layout)

        debug = result.assumptions.get("market_model_debug", {})
        dist = debug.get("price_distribution", {})
        p25 = dist.get("p25")
        p75 = dist.get("p75")

        if p25 and p75:
            # Best case revenue should be based on p75, not p75 * 1.10
            # Worst case revenue should be based on p25, not median * 0.90
            best_roi = result.ROI_best_case
            worst_roi = result.ROI_worst_case
            base_roi = result.ROI

            self.assertIsNotNone(best_roi)
            self.assertIsNotNone(worst_roi)
            self.assertGreater(best_roi, base_roi)
            self.assertLess(worst_roi, base_roi)


if __name__ == "__main__":
    unittest.main()
