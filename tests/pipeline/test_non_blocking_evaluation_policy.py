from __future__ import annotations

import argparse
import json
from pathlib import Path

from bedrock.scripts import po2_stabilization_gate
from bedrock.scripts import run_http_validation


def test_http_build_evaluation_summary_returns_poor_for_failing_gates() -> None:
    report = {"summary": {"chain_success_rate": 0.4}}
    gates = {
        "failures": [
            {"gate": "min_chain_success_rate"},
            {"gate": "endpoint_max_p95_seconds", "endpoint": "pipeline.run"},
        ],
        "passes": [],
    }

    summary = run_http_validation.build_evaluation_summary(report, gates)

    assert isinstance(summary["score"], float)
    assert summary["status"] == "poor"
    assert summary["issues"] == ["min_chain_success_rate", "endpoint_max_p95_seconds@pipeline.run"]
    assert summary["notes"]


def test_http_main_is_non_blocking_even_when_gates_fail(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "report.json"
    baseline_path = tmp_path / "baseline.json"
    config_path.write_text(json.dumps({"name": "unit", "cases": [], "gates": {}}))

    monkeypatch.setattr(
        run_http_validation,
        "parse_args",
        lambda: argparse.Namespace(
            config=config_path,
            output=output_path,
            baseline=baseline_path,
            write_baseline=False,
        ),
    )
    monkeypatch.setattr(
        run_http_validation,
        "run_validation",
        lambda _config: {
            "summary": {"chain_success_rate": 0.0, "total_cases": 1},
            "endpoint_metrics": {},
            "case_results": [],
        },
    )
    monkeypatch.setattr(
        run_http_validation,
        "evaluate_gates",
        lambda _report, _config, baseline=None: {
            "advisory_only": True,
            "passed": False,
            "failure_count": 1,
            "failures": [{"gate": "min_chain_success_rate"}],
            "passes": [],
        },
    )
    monkeypatch.setattr(run_http_validation, "build_open_issues", lambda _report: [])

    rc = run_http_validation.main()

    assert rc == 0
    payload = json.loads(output_path.read_text())
    assert payload["gates"]["passed"] is False
    assert payload["evaluation_summary"]["status"] in {"warning", "poor"}
    assert isinstance(payload["evaluation_summary"]["issues"], list)


def test_po2_main_is_non_blocking_even_when_gate_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        po2_stabilization_gate,
        "build_report",
        lambda: {
            "gate": {"po2_gate_passed": False},
            "evaluation_summary": {"score": 0.5, "status": "poor", "issues": [], "notes": []},
        },
    )
    monkeypatch.setattr(po2_stabilization_gate, "save_report", lambda _report: None)
    monkeypatch.setattr(
        po2_stabilization_gate,
        "BASELINE_PATH",
        Path("/tmp/po2_stabilization_baseline_test.json"),
    )

    rc = po2_stabilization_gate.main([])
    assert rc == 0

