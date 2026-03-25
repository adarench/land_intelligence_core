from __future__ import annotations

from tests.runtime_validation_utils import format_runtime_report, run_runtime_validation


def test_live_pipeline_runtime_meets_milestone_2_success_threshold() -> None:
    report = run_runtime_validation()
    metrics = report["metrics"]

    assert metrics["pipeline_success_rate"] == 1.0, format_runtime_report(report)


def test_live_zoning_runtime_never_omits_layout_required_fields() -> None:
    report = run_runtime_validation()
    failing_cases = [
        case
        for case in report["zoning_cases"]
        if case["missing_layout_fields"]
    ]

    assert not failing_cases, format_runtime_report(report)
