from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bedrock.services.layout_evaluation_service import (
    compare_layout_evaluation_reports,
    load_evaluation_inputs,
    load_layout_evaluation_report,
    run_layout_evaluation,
)


ROOT = Path(__file__).resolve().parents[2]


class LayoutEvaluationFrameworkTest(unittest.TestCase):
    def test_dataset_and_scenarios_load(self) -> None:
        dataset_name, parcels, scenarios = load_evaluation_inputs(
            dataset_root=ROOT / "test_data",
            manifest_path=ROOT / "test_data" / "layout_evaluation_manifest.json",
        )
        self.assertEqual(dataset_name, "layout_eval_v1")
        self.assertGreaterEqual(len(parcels), 20)
        self.assertGreaterEqual(len(scenarios), 3)

    def test_layout_evaluation_report_contains_required_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "layout_eval_results.json"
            report = run_layout_evaluation(
                dataset_root=ROOT / "test_data",
                manifest_path=ROOT / "test_data" / "layout_evaluation_manifest.json",
                parcel_ids=["layout_case_001", "layout_case_002"],
                scenario_ids=["A", "B"],
                output_path=output_path,
                algorithm_variant="test.variant",
                run_id="test-run-1",
            )

            self.assertEqual(report["run_id"], "test-run-1")
            self.assertEqual(report["algorithm_variant"], "test.variant")
            self.assertEqual(report["dataset_summary"]["parcel_count"], 2)
            self.assertEqual(report["dataset_summary"]["scenario_count"], 2)
            self.assertEqual(report["dataset_summary"]["case_count"], 4)
            self.assertIn("average_unit_yield", report["aggregate_metrics"])
            self.assertIn("average_road_length", report["aggregate_metrics"])
            self.assertIn("runtime_distribution", report["aggregate_metrics"])
            self.assertIn("solver_stability", report["aggregate_metrics"])

            loaded = load_layout_evaluation_report(output_path)
            self.assertEqual(len(loaded["records"]), 4)
            first = loaded["records"][0]
            self.assertIn("unit_yield", first["metrics"])
            self.assertIn("road_length", first["metrics"])
            self.assertIn("lot_size_distribution", first["metrics"])
            self.assertIn("solver_runtime", first["metrics"])
            self.assertIn("constraint_violations", first["metrics"])
            self.assertIn("yield_efficiency", first["metrics"])
            self.assertIn("road_efficiency", first["metrics"])

    def test_regression_detection_flags_runtime_and_yield_drop(self) -> None:
        baseline = {
            "aggregate_metrics": {
                "average_unit_yield": 10.0,
                "runtime_distribution": {"avg_seconds": 1.0},
                "solver_stability": {"success_rate": 1.0},
            }
        }
        current = {
            "aggregate_metrics": {
                "average_unit_yield": 8.0,
                "runtime_distribution": {"avg_seconds": 1.5},
                "solver_stability": {"success_rate": 0.9},
            }
        }
        regression = compare_layout_evaluation_reports(current=current, baseline=baseline)
        self.assertTrue(regression["has_regression"])
        self.assertGreaterEqual(regression["regression_count"], 3)


if __name__ == "__main__":
    unittest.main()
