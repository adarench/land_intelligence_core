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
from bedrock.contracts.feasibility_validation import DealOutcomeMetrics
from bedrock.services.feasibility_service import FeasibilityService


class FeasibilityServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FeasibilityService()
        self.parcel = Parcel(
            parcel_id="parcel-001",
            geometry={
                "type": "Polygon",
                "coordinates": [[
                    [0.0, 0.0],
                    [0.0, 1.0],
                    [1.0, 1.0],
                    [1.0, 0.0],
                    [0.0, 0.0],
                ]],
            },
            area=43560.0,
            jurisdiction="Example City",
        )

    def test_evaluate_computes_baseline_financials(self) -> None:
        layout = SubdivisionLayout(
            layout_id="layout-001",
            parcel_id=self.parcel.parcel_id,
            lot_count=10,
            open_space_area=0.0,
            road_length=500.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=450000.0,
            cost_per_home=250000.0,
            road_cost_per_ft=300.0,
        )

        result = self.service.evaluate(parcel=self.parcel, layout=layout, market_data=market_data)

        self.assertEqual(result.parcel_id, self.parcel.parcel_id)
        self.assertEqual(result.units, 10)
        self.assertEqual(result.estimated_home_price, 450000.0)
        self.assertEqual(result.construction_cost_per_home, 150000.0)
        self.assertEqual(result.development_cost_total, 218000.0)
        self.assertEqual(result.projected_revenue, 4500000.0)
        self.assertEqual(result.projected_cost, 1718000.0)
        self.assertEqual(result.projected_profit, 2782000.0)
        self.assertAlmostEqual(result.ROI or 0.0, 2782000.0 / 1718000.0, places=8)
        self.assertAlmostEqual(result.profit_margin or 0.0, 2782000.0 / 4500000.0, places=8)
        self.assertEqual(result.revenue_per_unit, 450000.0)
        self.assertEqual(result.cost_per_unit, 171800.0)
        self.assertEqual(result.max_units, 10)
        self.assertEqual(result.layout_id, "layout-001")
        self.assertEqual(result.constraint_violations, [])
        self.assertEqual(result.financial_summary["projected_revenue"], 4500000.0)
        self.assertEqual(result.financial_summary["land_cost"], 0.0)
        self.assertEqual(result.financial_summary["development_cost_breakdown"]["roads"], 150000.0)
        self.assertEqual(result.financial_summary["development_cost_breakdown"]["grading"], 15000.0)
        self.assertEqual(result.financial_summary["development_cost_breakdown"]["sitework"], 18000.0)
        self.assertEqual(result.financial_summary["development_cost_breakdown"]["permitting"], 35000.0)
        self.assertEqual(result.explanation.primary_driver, "home_price")
        self.assertEqual(result.explanation.cost_breakdown.construction, 1500000.0)
        self.assertEqual(result.explanation.revenue_breakdown.units, 10)

    def test_evaluate_includes_land_price_in_projected_cost(self) -> None:
        layout = SubdivisionLayout(
            layout_id="layout-land-cost",
            parcel_id=self.parcel.parcel_id,
            lot_count=4,
            open_space_area=0.0,
            road_length=100.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=400000.0,
            construction_cost_per_home=200000.0,
            road_cost_per_ft=100.0,
            land_price=50000.0,
        )

        result = self.service.evaluate(parcel=self.parcel, layout=layout, market_data=market_data)

        self.assertEqual(result.projected_revenue, 1600000.0)
        self.assertEqual(result.development_cost_total, 43000.0)
        self.assertEqual(result.projected_cost, 1051320.0)
        self.assertEqual(result.projected_profit, 548680.0)
        self.assertAlmostEqual(result.ROI or 0.0, 548680.0 / 1051320.0, places=8)

    def test_evaluate_handles_zero_total_cost(self) -> None:
        layout = SubdivisionLayout(
            layout_id="layout-zero-cost",
            parcel_id=self.parcel.parcel_id,
            lot_count=0,
            open_space_area=0.0,
            road_length=0.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=0.0,
            cost_per_home=0.0,
            road_cost_per_ft=0.0,
        )

        result = self.service.evaluate(parcel=self.parcel, layout=layout, market_data=market_data)

        self.assertIsNone(result.ROI)
        self.assertIn("layout_has_no_units", result.constraint_violations)
        self.assertIn("projected_total_cost_zero", result.constraint_violations)
        self.assertGreaterEqual(result.risk_score, 0.0)
        self.assertLessEqual(result.risk_score, 1.0)

    def test_evaluate_handles_high_cost_negative_roi(self) -> None:
        layout = SubdivisionLayout(
            layout_id="layout-high-cost",
            parcel_id=self.parcel.parcel_id,
            lot_count=3,
            open_space_area=0.0,
            road_length=900.0,
            utility_length=400.0,
        )
        market_data = MarketData(
            estimated_home_price=280000.0,
            construction_cost_per_home=420000.0,
            road_cost_per_ft=450.0,
            land_price=600000.0,
        )

        result = self.service.evaluate(parcel=self.parcel, layout=layout, market_data=market_data)

        self.assertLess(result.projected_profit, 0.0)
        self.assertLess(result.ROI or 0.0, 0.0)
        self.assertIn("projected_profit_negative", result.constraint_violations)

    def test_scenario_id_is_deterministic_for_same_inputs(self) -> None:
        layout = SubdivisionLayout(
            layout_id="layout-stable",
            parcel_id=self.parcel.parcel_id,
            lot_count=8,
            open_space_area=0.0,
            road_length=250.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=400000.0,
            cost_per_home=225000.0,
            road_cost_per_ft=275.0,
        )

        first = self.service.evaluate(parcel=self.parcel, layout=layout, market_data=market_data)
        second = self.service.evaluate(parcel=self.parcel, layout=layout, market_data=market_data)

        self.assertEqual(first.scenario_id, second.scenario_id)

    def test_evaluate_layouts_ranks_results_by_roi(self) -> None:
        better_layout = SubdivisionLayout(
            layout_id="layout-better",
            parcel_id=self.parcel.parcel_id,
            lot_count=8,
            open_space_area=0.0,
            road_length=200.0,
            utility_length=0.0,
        )
        worse_layout = SubdivisionLayout(
            layout_id="layout-worse",
            parcel_id=self.parcel.parcel_id,
            lot_count=8,
            open_space_area=0.0,
            road_length=600.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=420000.0,
            construction_cost_per_home=230000.0,
            road_cost_per_ft=250.0,
        )

        results = self.service.evaluate_layouts(self.parcel, [worse_layout, better_layout], market_data)

        self.assertEqual([result.layout_id for result in results], ["layout-better", "layout-worse"])
        self.assertGreater(results[0].ROI or float("-inf"), results[1].ROI or float("-inf"))
        self.assertEqual([result.rank for result in results], [1, 2])

    def test_summarize_scenario_returns_ranked_summary(self) -> None:
        first_layout = SubdivisionLayout(
            layout_id="layout-1",
            parcel_id=self.parcel.parcel_id,
            lot_count=6,
            open_space_area=0.0,
            road_length=150.0,
            utility_length=0.0,
        )
        second_layout = SubdivisionLayout(
            layout_id="layout-2",
            parcel_id=self.parcel.parcel_id,
            lot_count=5,
            open_space_area=0.0,
            road_length=300.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=430000.0,
            construction_cost_per_home=240000.0,
            road_cost_per_ft=200.0,
        )

        summary = self.service.summarize_scenario(self.parcel, [second_layout, first_layout], market_data)

        self.assertEqual(summary.parcel_id, self.parcel.parcel_id)
        self.assertEqual(summary.layout_count, 2)
        self.assertEqual(summary.best_layout_id, "layout-1")
        self.assertEqual(summary.best_units, 6)
        self.assertEqual([result.rank for result in summary.layouts_ranked], [1, 2])

    def test_default_evaluate_output_is_deterministic_without_runtime_metadata(self) -> None:
        layout = SubdivisionLayout(
            layout_id="layout-deterministic",
            parcel_id=self.parcel.parcel_id,
            lot_count=5,
            open_space_area=0.0,
            road_length=180.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=410000.0,
            construction_cost_per_home=235000.0,
            road_cost_per_ft=210.0,
        )

        first = self.service.evaluate(parcel=self.parcel, layout=layout, market_data=market_data)
        second = self.service.evaluate(parcel=self.parcel, layout=layout, market_data=market_data)

        self.assertIsNone(first.metadata)
        self.assertIsNone(second.metadata)
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertEqual(first.core_calculation_view(), second.core_calculation_view())

    def test_runtime_metadata_can_be_opted_in_without_affecting_core_calculation_view(self) -> None:
        layout = SubdivisionLayout(
            layout_id="layout-runtime-metadata",
            parcel_id=self.parcel.parcel_id,
            lot_count=5,
            open_space_area=0.0,
            road_length=180.0,
            utility_length=0.0,
        )
        market_data = MarketData(
            estimated_home_price=410000.0,
            construction_cost_per_home=235000.0,
            road_cost_per_ft=210.0,
        )

        deterministic = self.service.evaluate(parcel=self.parcel, layout=layout, market_data=market_data)
        runtime = self.service.evaluate(
            parcel=self.parcel,
            layout=layout,
            market_data=market_data,
            include_runtime_metadata=True,
        )

        self.assertIsNone(deterministic.metadata)
        self.assertIsNotNone(runtime.metadata)
        self.assertEqual(deterministic.core_calculation_view(), runtime.core_calculation_view())

    def test_calibration_hook_compares_predicted_vs_actual(self) -> None:
        layout = SubdivisionLayout(
            layout_id="layout-calibration",
            parcel_id=self.parcel.parcel_id,
            lot_count=4,
            open_space_area=0.0,
            road_length=120.0,
            utility_length=0.0,
        )
        predicted = self.service.evaluate(parcel=self.parcel, layout=layout)
        actual = DealOutcomeMetrics(
            sale_price=470000.0,
            construction_cost=1020000.0,
            development_cost=95000.0,
            ROI=0.18,
        )

        record = self.service.to_validation_record(
            result=predicted,
            actual=actual,
            record_id="deal-001",
            notes="post-close actuals",
        )
        report = self.service.compare_to_actual(
            result=predicted,
            actual=actual,
            record_id="deal-001",
        )

        self.assertEqual(record.record_id, "deal-001")
        self.assertEqual(record.predicted.sale_price, predicted.estimated_home_price)
        self.assertEqual(record.actual.sale_price, 470000.0)
        self.assertGreaterEqual(report.sale_price.absolute_error, 0.0)
        self.assertGreaterEqual(report.construction_cost.absolute_error, 0.0)
        self.assertGreaterEqual(report.development_cost.absolute_error, 0.0)
        self.assertGreaterEqual(report.ROI.absolute_error, 0.0)


if __name__ == "__main__":
    unittest.main()
