from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bedrock.services.phase1_parcel_zoning_evaluation_service import (
    compare_phase1_reports,
    load_phase1_dataset,
    load_phase1_report,
    run_phase1_evaluation,
)


ROOT = Path(__file__).resolve().parents[2]


class Phase1ParcelZoningEvaluationTest(unittest.TestCase):
    def test_phase1_evaluation_tracks_required_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "phase1_results.json"
            report = run_phase1_evaluation(
                dataset_path=ROOT / "test_data" / "phase1_parcel_zoning_dataset.json",
                zoning_dataset_root=ROOT / "test_data" / "phase1_zoning_dataset",
                case_ids=["layout_case_001", "layout_case_002", "layout_case_003", "layout_case_004"],
                output_path=output_path,
            )

            self.assertEqual(report["record_count"], 4)
            self.assertIn("parcel_normalization_success_rate", report["metrics"])
            self.assertIn("zoning_rule_completeness", report["metrics"])
            self.assertIn("pipeline_runtime", report["metrics"])
            self.assertGreaterEqual(report["metrics"]["parcel_normalization_success_rate"], 1.0)
            self.assertGreater(report["metrics"]["zoning_rule_completeness"], 0.8)
            self.assertTrue(report["quality_gate"]["rule_completeness_passed"])
            self.assertEqual(len(report["records"]), 4)

            loaded = load_phase1_report(output_path)
            self.assertEqual(loaded["record_count"], 4)

    def test_phase1_dataset_contains_twenty_records(self) -> None:
        _dataset_name, records = load_phase1_dataset(ROOT / "test_data" / "phase1_parcel_zoning_dataset.json")
        self.assertGreaterEqual(len(records), 20)

    def test_compare_phase1_reports_detects_regressions(self) -> None:
        baseline = {
            "metrics": {
                "zoning_rule_completeness": 0.95,
                "rule_extraction_failure_count": 0,
                "schema_violation_count": 0,
                "pipeline_runtime": {"avg_seconds": 0.5},
            }
        }
        current = {
            "metrics": {
                "zoning_rule_completeness": 0.70,
                "rule_extraction_failure_count": 2,
                "schema_violation_count": 1,
                "pipeline_runtime": {"avg_seconds": 0.8},
            }
        }
        regression = compare_phase1_reports(current=current, baseline=baseline, max_runtime_regression_pct=0.2)
        self.assertTrue(regression["has_regression"])
        self.assertGreaterEqual(regression["regression_count"], 3)


if __name__ == "__main__":
    unittest.main()
