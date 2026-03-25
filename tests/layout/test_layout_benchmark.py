from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bedrock.services.layout_benchmark_service import (
    compare_layout_benchmark_runs,
    load_layout_benchmark_cases,
    load_benchmark_report,
    run_layout_benchmark,
)


ROOT = Path(__file__).resolve().parents[2]


class LayoutBenchmarkServiceTest(unittest.TestCase):
    def test_layout_benchmark_records_required_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "layout_benchmark_results.json"
            report = run_layout_benchmark(
                dataset_root=ROOT / "test_data",
                manifest_path=ROOT / "test_data" / "layout_benchmark_manifest.json",
                case_ids=["layout_case_001"],
                output_path=output_path,
            )

            self.assertTrue(output_path.exists())
            self.assertIn("experiment_run", report)
            self.assertIn("run_id", report["experiment_run"])
            self.assertIn("dataset", report["experiment_run"])
            self.assertIn("algorithm_variant", report["experiment_run"])
            self.assertIn("metrics", report["experiment_run"])
            self.assertIn("timestamp", report["experiment_run"])
            self.assertEqual(report["experiment_run"]["metrics"]["dataset_size"], 1)
            self.assertEqual(report["experiment_run"]["metrics"]["failure_count"], 0)
            self.assertEqual(len(report["records"]), 1)

            record = report["records"][0]
            self.assertIn("algorithm_variant", record)
            metrics = record["metrics"]
            self.assertGreater(metrics["units"], 0)
            self.assertGreater(metrics["units_generated"], 0)
            self.assertGreater(metrics["lot_yield_per_acre"], 0.0)
            self.assertGreaterEqual(metrics["road_length"], 0.0)
            self.assertGreaterEqual(metrics["layout_score"], 0.0)
            self.assertGreater(metrics["runtime"], 0.0)
            self.assertGreaterEqual(metrics["candidate_search_count"], 1)
            self.assertGreater(metrics["parcel_area"], 0.0)
            self.assertGreater(metrics["parcel_compactness"], 0.0)
            self.assertLessEqual(metrics["parcel_compactness"], 1.0 + 1e-6)
            self.assertIsInstance(metrics["constraint_violations"], list)
            self.assertIsInstance(metrics["invalid_lot_count"], int)

            loaded = load_benchmark_report(output_path)
            self.assertEqual(loaded["records"][0]["case_id"], "layout_case_001")

    def test_layout_benchmark_dataset_contains_twenty_cases(self) -> None:
        cases = load_layout_benchmark_cases(
            dataset_root=ROOT / "test_data",
            manifest_path=ROOT / "test_data" / "layout_benchmark_manifest.json",
        )
        self.assertGreaterEqual(len(cases), 20)

    def test_compare_benchmark_runs_detects_regression(self) -> None:
        baseline = {
            "experiment_run": {"timestamp": "2026-03-16T00:00:00+00:00"},
            "records": [
                {
                    "case_id": "layout_case_001",
                    "status": "success",
                    "metrics": {
                        "units": 10,
                        "lot_yield_per_acre": 1.0,
                        "layout_score": 0.9,
                        "runtime": 1.0,
                    },
                }
            ],
        }
        current = {
            "experiment_run": {"timestamp": "2026-03-16T01:00:00+00:00"},
            "records": [
                {
                    "case_id": "layout_case_001",
                    "status": "success",
                    "metrics": {
                        "units": 8,
                        "lot_yield_per_acre": 0.85,
                        "layout_score": 0.8,
                        "runtime": 1.4,
                    },
                }
            ],
        }

        regression = compare_layout_benchmark_runs(current=current, baseline=baseline)

        self.assertTrue(regression["has_regression"])
        self.assertGreaterEqual(regression["regression_count"], 1)
        metrics = {item["metric"] for item in regression["regressions"]}
        self.assertIn("units", metrics)
        self.assertIn("lot_yield_per_acre", metrics)
        self.assertIn("layout_score", metrics)
        self.assertIn("runtime", metrics)


if __name__ == "__main__":
    unittest.main()
