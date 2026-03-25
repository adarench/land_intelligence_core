from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = ROOT / "bedrock"
for candidate in (ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from services.benchmark_harness import run_layout_benchmark, run_pipeline_benchmark


class PipelineBenchmarkHarnessTest(unittest.TestCase):
    def test_pipeline_benchmark_includes_pipeline_metrics(self) -> None:
        report = run_pipeline_benchmark(dataset_root=ROOT / "test_data")

        self.assertEqual(report["benchmark_type"], "pipeline")
        self.assertEqual(report["failure_count"], 0)
        self.assertIn("pipeline_metrics", report)
        self.assertIn("layouts_evaluated", report["pipeline_metrics"])
        self.assertIn("pipeline_runtime", report["pipeline_metrics"])
        self.assertIn("best_ROI", report["pipeline_metrics"])
        self.assertIn("best_unit_yield", report["pipeline_metrics"])

        for record in report["records"]:
            self.assertEqual(record["status"], "success")
            self.assertIn("layouts_evaluated", record["metrics"])
            self.assertIn("pipeline_runtime", record["metrics"])
            self.assertIn("best_ROI", record["metrics"])
            self.assertIn("best_unit_yield", record["metrics"])

    def test_layout_benchmark_includes_pipeline_metrics(self) -> None:
        report = run_layout_benchmark(dataset_root=ROOT / "test_data")

        self.assertEqual(report["benchmark_type"], "layout")
        self.assertEqual(report["failure_count"], 0)
        self.assertIn("pipeline_metrics", report)
        self.assertIn("best_unit_yield", report["pipeline_metrics"])


if __name__ == "__main__":
    unittest.main()
