from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"
for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.api.parcel_api import create_app as create_parcel_app
from bedrock.api.pipeline_api import create_app as create_pipeline_app
from bedrock.api.layout_api import app as layout_app
from bedrock.api.zoning_api import create_app as create_zoning_app
from tests.runtime_validation_utils import RUNTIME_CASES

REQUIRED_LAYOUT_FIELDS = (
    "min_lot_size_sqft",
    "max_units_per_acre",
    "setbacks.front",
    "setbacks.side",
    "setbacks.rear",
)
PIPELINE_GATE_TARGET = 0.80
ZONING_GATE_TARGET = 0.90
LATEST_REPORT_PATH = BEDROCK_ROOT / "benchmarks" / "po2_stabilization_latest.json"
HISTORY_PATH = BEDROCK_ROOT / "benchmarks" / "po2_stabilization_history.jsonl"
BASELINE_PATH = BEDROCK_ROOT / "benchmarks" / "po2_stabilization_baseline.json"
PO2_DATASET_PATH = WORKSPACE_ROOT / "test_data" / "po2_parcel_zoning_dataset.json"
FIXTURE_DATASET_PATH = WORKSPACE_ROOT / "test_data" / "phase1_parcel_zoning_dataset.json"


@dataclass(frozen=True)
class MatrixCase:
    matrix: str
    jurisdiction: str
    parcel_id: str
    geometry: dict[str, Any]
    area_sqft: float = 100000.0
    expected_district: str | None = None
    matrix_scope: str = "production"
    synthetic_dataset: bool = False

    def parcel_load_payload(self) -> dict[str, Any]:
        return {
            "parcel_id": self.parcel_id,
            "geometry": self.geometry,
            "jurisdiction": self.jurisdiction,
        }

    def zoning_payload(self) -> dict[str, Any]:
        return {
            "parcel_id": self.parcel_id,
            "geometry": self.geometry,
            "jurisdiction": self.jurisdiction,
            "area_sqft": self.area_sqft,
        }

    def pipeline_payload(self) -> dict[str, Any]:
        return {
            "parcel_geometry": self.geometry,
            "parcel_id": self.parcel_id,
            "jurisdiction": self.jurisdiction,
            "max_candidates": 5,
        }


def _get_nested(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _missing_layout_fields(zoning_payload: dict[str, Any]) -> list[str]:
    return [field_name for field_name in REQUIRED_LAYOUT_FIELDS if _get_nested(zoning_payload, field_name) is None]


def _response_payload(response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _extract_pipeline_failure(payload: Any, status_code: int) -> tuple[str | None, str | None]:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            stage = detail.get("stage")
            error = detail.get("error")
            return (
                str(stage) if stage is not None else None,
                str(error) if error is not None else None,
            )
        if isinstance(detail, list):
            return "request.validation", "request_validation_error"
        if isinstance(detail, str):
            return "request.validation", "request_validation_error"
        stage = payload.get("stage")
        error = payload.get("error")
        return (
            str(stage) if stage is not None else None,
            str(error) if error is not None else None,
        )
    if isinstance(payload, str) and status_code >= 500:
        return "internal", "internal_error"
    return None, None


def _zoning_source_run_id(zoning_payload: Any) -> str:
    if not isinstance(zoning_payload, dict):
        return ""
    metadata = zoning_payload.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    run_id = metadata.get("source_run_id")
    return str(run_id or "").strip()


def _is_stub_zoning_source(run_id: str) -> bool:
    value = run_id.lower()
    return "zoning_stub_districts" in value or "stub" in value


def _uses_fallback_source(run_id: str) -> bool:
    value = run_id.lower()
    return "jurisdiction_fallback" in value or "fallback" in value


def _load_matrix_cases() -> list[MatrixCase]:
    cases: list[MatrixCase] = []

    for runtime_case in RUNTIME_CASES:
        payload = runtime_case.parcel_payload()
        cases.append(
            MatrixCase(
                matrix="representative_jurisdictions",
                jurisdiction=runtime_case.jurisdiction,
                parcel_id=runtime_case.parcel_id,
                geometry=payload["geometry"],
                area_sqft=float(payload.get("area_sqft", 100000.0)),
                expected_district=runtime_case.expected_district,
                matrix_scope="production",
                synthetic_dataset=False,
            )
        )

    dataset_payload = json.loads(PO2_DATASET_PATH.read_text())
    for item in dataset_payload.get("records", []):
        cases.append(
            MatrixCase(
                matrix="supported_buildable_matrix",
                jurisdiction=str(item.get("jurisdiction", "unknown")),
                parcel_id=str(item.get("parcel_id", "unknown")),
                geometry=dict(item["geometry"]),
                area_sqft=float(item.get("area_sqft", 100000.0)),
                expected_district=(str(item["zoning_district"]) if item.get("zoning_district") else None),
                matrix_scope="production",
                synthetic_dataset=bool(item.get("synthetic_dataset", False)),
            )
        )

    fixture_payload = json.loads(FIXTURE_DATASET_PATH.read_text())
    for item in fixture_payload.get("records", []):
        cases.append(
            MatrixCase(
                matrix="fixture_regression_matrix",
                jurisdiction=str(item.get("jurisdiction", "unknown")),
                parcel_id=str(item.get("parcel_id", "unknown")),
                geometry=dict(item["geometry"]),
                area_sqft=float(item.get("area_sqft", 100000.0)),
                expected_district=(str(item["zoning_district"]) if item.get("zoning_district") else None),
                matrix_scope="fixture",
                synthetic_dataset=True,
            )
        )
    return cases


def _run_case(
    case: MatrixCase,
    parcel_client: TestClient,
    zoning_client: TestClient,
    layout_client: TestClient,
    pipeline_client: TestClient,
) -> dict[str, Any]:
    parcel_started = time.perf_counter()
    parcel_response = parcel_client.post("/parcel/load", json=case.parcel_load_payload())
    parcel_runtime = time.perf_counter() - parcel_started
    parcel_body = _response_payload(parcel_response)

    zoning_started = time.perf_counter()
    zoning_response = zoning_client.post("/zoning/lookup", json={"parcel": case.zoning_payload()})
    zoning_runtime = time.perf_counter() - zoning_started
    zoning_body = _response_payload(zoning_response)

    district = zoning_body.get("district") if isinstance(zoning_body, dict) else None
    missing_fields = _missing_layout_fields(zoning_body) if isinstance(zoning_body, dict) and zoning_response.status_code == 200 else list(REQUIRED_LAYOUT_FIELDS)
    district_resolution_ok = zoning_response.status_code == 200 and bool(district) and (
        case.expected_district is None or district == case.expected_district
    )
    rule_complete = zoning_response.status_code == 200 and not missing_fields
    zoning_source_run_id = _zoning_source_run_id(zoning_body)
    stub_zoning = zoning_response.status_code == 200 and _is_stub_zoning_source(zoning_source_run_id)
    fallback_usage = zoning_response.status_code == 200 and _uses_fallback_source(zoning_source_run_id)

    layout_started = time.perf_counter()
    layout_response = None
    if zoning_response.status_code == 200 and parcel_response.status_code == 200 and isinstance(parcel_body, dict):
        layout_response = layout_client.post(
            "/layout/search",
            json={
                "parcel": parcel_body,
                "zoning": zoning_body,
                "max_candidates": 5,
            },
        )
    layout_runtime = time.perf_counter() - layout_started
    layout_status_code = int(layout_response.status_code) if layout_response is not None else 0
    layout_body = _response_payload(layout_response) if layout_response is not None else {"detail": "skipped"}
    layout_success = layout_response is not None and layout_response.status_code == 200
    layout_units = (
        int(layout_body.get("units", 0))
        if layout_success and isinstance(layout_body, dict) and isinstance(layout_body.get("units"), (int, float))
        else 0
    )

    pipeline_started = time.perf_counter()
    pipeline_response = pipeline_client.post("/pipeline/run", json=case.pipeline_payload())
    pipeline_runtime = time.perf_counter() - pipeline_started
    pipeline_body = _response_payload(pipeline_response)
    failed_stage, failed_error = _extract_pipeline_failure(
        pipeline_body,
        pipeline_response.status_code,
    )

    return {
        "matrix": case.matrix,
        "matrix_scope": case.matrix_scope,
        "synthetic_dataset": case.synthetic_dataset,
        "jurisdiction": case.jurisdiction,
        "parcel_id": case.parcel_id,
        "expected_district": case.expected_district,
        "parcel": {
            "status_code": parcel_response.status_code,
            "success": parcel_response.status_code == 200,
            "runtime_seconds": parcel_runtime,
            "body": parcel_body,
        },
        "zoning": {
            "status_code": zoning_response.status_code,
            "success": zoning_response.status_code == 200,
            "runtime_seconds": zoning_runtime,
            "district": district,
            "district_resolution_ok": district_resolution_ok,
            "rule_complete": rule_complete,
            "missing_layout_fields": missing_fields,
            "source_run_id": zoning_source_run_id,
            "stub_zoning": stub_zoning,
            "fallback_usage": fallback_usage,
            "body": zoning_body,
        },
        "layout": {
            "status_code": layout_status_code,
            "success": layout_success,
            "runtime_seconds": layout_runtime,
            "units": layout_units,
            "body": layout_body,
        },
        "pipeline": {
            "status_code": pipeline_response.status_code,
            "success": pipeline_response.status_code == 200,
            "runtime_seconds": pipeline_runtime,
            "failed_stage": failed_stage,
            "failed_error": failed_error,
            "body": pipeline_body,
        },
        "parcel_usable": bool(layout_success),
        "partial_rule_usable": bool((not rule_complete) and layout_success),
    }


def _rate(values: list[bool]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _runtime_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(values)
    count = len(ordered)

    def pick(pct: float) -> float:
        index = max(0, min(count - 1, int(round((count - 1) * pct))))
        return float(ordered[index])

    return {
        "avg": float(sum(values) / count),
        "p50": pick(0.5),
        "p95": pick(0.95),
        "max": float(max(values)),
    }


def _load_previous_metrics() -> dict[str, Any] | None:
    if BASELINE_PATH.exists():
        return json.loads(BASELINE_PATH.read_text())
    if not LATEST_REPORT_PATH.exists():
        return None
    return json.loads(LATEST_REPORT_PATH.read_text())


def _delta(current: float, previous: float | None) -> float | None:
    if previous is None:
        return None
    return current - previous


def build_report() -> dict[str, Any]:
    cases = _load_matrix_cases()
    parcel_client = TestClient(create_parcel_app(), raise_server_exceptions=False)
    zoning_client = TestClient(create_zoning_app(), raise_server_exceptions=False)
    layout_client = TestClient(layout_app, raise_server_exceptions=False)
    pipeline_client = TestClient(create_pipeline_app(), raise_server_exceptions=False)

    rows = [_run_case(case, parcel_client, zoning_client, layout_client, pipeline_client) for case in cases]
    production_rows = [row for row in rows if row.get("matrix_scope", "production") == "production"]

    pipeline_successes = [row["pipeline"]["success"] for row in production_rows]
    zoning_successes = [row["zoning"]["success"] for row in production_rows]
    district_resolution_hits = [row["zoning"]["district_resolution_ok"] for row in production_rows]
    rule_completeness_hits = [row["zoning"]["rule_complete"] for row in production_rows]
    parcel_successes = [row["parcel"]["success"] for row in production_rows]
    parcel_usability_hits = [bool(row.get("parcel_usable")) for row in production_rows]
    partial_rule_usable_hits = [bool(row.get("partial_rule_usable")) for row in production_rows]
    stub_hits = [bool(row["zoning"].get("stub_zoning")) for row in production_rows if row["zoning"]["success"]]
    fallback_hits = [bool(row["zoning"].get("fallback_usage")) for row in production_rows if row["zoning"]["success"]]
    synthetic_hits = [bool(row.get("synthetic_dataset")) for row in production_rows]
    pipeline_runtimes = [float(row["pipeline"]["runtime_seconds"]) for row in production_rows]

    failed_stages = Counter(
        (row["pipeline"]["failed_stage"] or "none") for row in production_rows if not row["pipeline"]["success"]
    )
    failed_errors = Counter(
        (row["pipeline"]["failed_error"] or "none") for row in production_rows if not row["pipeline"]["success"]
    )

    matrix_breakdown: dict[str, Any] = {}
    for matrix_name in sorted({row["matrix"] for row in rows}):
        selected = [row for row in rows if row["matrix"] == matrix_name]
        matrix_breakdown[matrix_name] = {
            "total": len(selected),
            "matrix_scope": selected[0].get("matrix_scope", "production") if selected else "production",
            "pipeline_success_rate": _rate([row["pipeline"]["success"] for row in selected]),
            "zoning_success_rate": _rate([row["zoning"]["success"] for row in selected]),
            "district_accuracy": _rate([row["zoning"]["district_resolution_ok"] for row in selected]),
            "rule_completeness_rate": _rate([row["zoning"]["rule_complete"] for row in selected]),
            "geometry_stability_rate": _rate([row["parcel"]["success"] for row in selected]),
            "stub_zoning_rate": _rate([bool(row["zoning"].get("stub_zoning")) for row in selected if row["zoning"]["success"]]),
            "fallback_usage_rate": _rate([bool(row["zoning"].get("fallback_usage")) for row in selected if row["zoning"]["success"]]),
            "synthetic_dataset_rate": _rate([bool(row.get("synthetic_dataset")) for row in selected]),
            "parcel_usability_rate": _rate([bool(row.get("parcel_usable")) for row in selected]),
            "partial_rule_usable_rate": _rate([bool(row.get("partial_rule_usable")) for row in selected]),
        }

    previous = _load_previous_metrics()
    previous_metrics = previous.get("metrics", {}) if previous else {}

    metrics = {
        "total_cases": len(production_rows),
        "fixture_cases": len(rows) - len(production_rows),
        "pipeline_success_rate": _rate(pipeline_successes),
        "zoning_success_rate": _rate(zoning_successes),
        "district_accuracy": _rate(district_resolution_hits),
        "rule_completeness_rate": _rate(rule_completeness_hits),
        "geometry_stability_rate": _rate(parcel_successes),
        "stub_zoning_rate": _rate(stub_hits),
        "fallback_usage_rate": _rate(fallback_hits),
        "synthetic_dataset_rate": _rate(synthetic_hits),
        "parcel_usability_rate": _rate(parcel_usability_hits),
        "partial_rule_usable_rate": _rate(partial_rule_usable_hits),
        "pipeline_runtime_seconds": _runtime_stats(pipeline_runtimes),
        "failed_stage_counts": dict(failed_stages),
        "failed_error_counts": dict(failed_errors),
        "matrix_breakdown": matrix_breakdown,
    }
    trend = {
        "pipeline_success_rate_delta": _delta(
            metrics["pipeline_success_rate"], previous_metrics.get("pipeline_success_rate")
        ),
        "zoning_success_rate_delta": _delta(
            metrics["zoning_success_rate"], previous_metrics.get("zoning_success_rate")
        ),
        "district_accuracy_delta": _delta(
            metrics["district_accuracy"], previous_metrics.get("district_accuracy")
        ),
        "rule_completeness_rate_delta": _delta(
            metrics["rule_completeness_rate"], previous_metrics.get("rule_completeness_rate")
        ),
        "geometry_stability_rate_delta": _delta(
            metrics["geometry_stability_rate"], previous_metrics.get("geometry_stability_rate")
        ),
        "stub_zoning_rate_delta": _delta(
            metrics["stub_zoning_rate"], previous_metrics.get("stub_zoning_rate")
        ),
        "fallback_usage_rate_delta": _delta(
            metrics["fallback_usage_rate"], previous_metrics.get("fallback_usage_rate")
        ),
        "synthetic_dataset_rate_delta": _delta(
            metrics["synthetic_dataset_rate"], previous_metrics.get("synthetic_dataset_rate")
        ),
        "parcel_usability_rate_delta": _delta(
            metrics["parcel_usability_rate"], previous_metrics.get("parcel_usability_rate")
        ),
        "partial_rule_usable_rate_delta": _delta(
            metrics["partial_rule_usable_rate"], previous_metrics.get("partial_rule_usable_rate")
        ),
    }
    gate = {
        "pipeline_success_target": PIPELINE_GATE_TARGET,
        "zoning_success_target": ZONING_GATE_TARGET,
        "pipeline_success_passed": metrics["pipeline_success_rate"] >= PIPELINE_GATE_TARGET,
        "zoning_success_passed": metrics["zoning_success_rate"] >= ZONING_GATE_TARGET,
        "district_accuracy_target": 0.95,
        "district_accuracy_passed": metrics["district_accuracy"] >= 0.95,
        "rule_completeness_target": 0.95,
        "rule_completeness_passed": metrics["rule_completeness_rate"] >= 0.95,
        "parcel_usability_target": 0.95,
        "parcel_usability_passed": metrics["parcel_usability_rate"] >= 0.95,
        "stub_zoning_required_max": 0.0,
        "fallback_usage_required_max": 0.0,
        "synthetic_dataset_required_max": 0.0,
        "stub_zoning_passed": metrics["stub_zoning_rate"] <= 0.0,
        "fallback_usage_passed": metrics["fallback_usage_rate"] <= 0.0,
        "synthetic_dataset_passed": metrics["synthetic_dataset_rate"] <= 0.0,
    }
    gate["po2_gate_passed"] = all(
        (
            gate["pipeline_success_passed"],
            gate["zoning_success_passed"],
            gate["district_accuracy_passed"],
            gate["rule_completeness_passed"],
            gate["parcel_usability_passed"],
            gate["stub_zoning_passed"],
            gate["fallback_usage_passed"],
            gate["synthetic_dataset_passed"],
        )
    )

    recommendations = []
    if not gate["zoning_success_passed"]:
        recommendations.append(
            {
                "owner": "zoning_agent",
                "reason": "zoning reliability below gate",
                "focus": [
                    "district resolution failures",
                    "incomplete zoning rule payloads",
                ],
            }
        )
    if metrics["geometry_stability_rate"] < 1.0:
        recommendations.append(
            {
                "owner": "parcel_agent",
                "reason": "geometry stability below 100%",
                "focus": [
                    "geometry normalization failures",
                    "parcel ingestion edge cases",
                ],
            }
        )
    if not gate["stub_zoning_passed"] or not gate["fallback_usage_passed"] or not gate["synthetic_dataset_passed"]:
        recommendations.append(
            {
                "owner": "evaluation_agent",
                "reason": "production matrix representation quality failed",
                "focus": [
                    "remove stub zoning sources from production matrix",
                    "eliminate fallback-derived zoning in production matrix",
                    "ensure synthetic datasets remain fixture-only",
                ],
            }
        )

    gate_checks = [
        gate["pipeline_success_passed"],
        gate["zoning_success_passed"],
        gate["district_accuracy_passed"],
        gate["rule_completeness_passed"],
        gate["parcel_usability_passed"],
        gate["stub_zoning_passed"],
        gate["fallback_usage_passed"],
        gate["synthetic_dataset_passed"],
    ]
    gate_score = (sum(1 for value in gate_checks if value) / len(gate_checks)) if gate_checks else 1.0
    if gate_score >= 0.9 and gate["po2_gate_passed"]:
        eval_status = "good"
    elif gate_score >= 0.7:
        eval_status = "warning"
    else:
        eval_status = "poor"

    eval_issues: list[str] = []
    if not gate["pipeline_success_passed"]:
        eval_issues.append("pipeline_success_below_target")
    if not gate["zoning_success_passed"]:
        eval_issues.append("zoning_success_below_target")
    if not gate["district_accuracy_passed"]:
        eval_issues.append("district_accuracy_below_target")
    if not gate["rule_completeness_passed"]:
        eval_issues.append("rule_completeness_below_target")
    if not gate["parcel_usability_passed"]:
        eval_issues.append("parcel_usability_below_target")
    if not gate["stub_zoning_passed"]:
        eval_issues.append("stub_zoning_present_in_production")
    if not gate["fallback_usage_passed"]:
        eval_issues.append("fallback_usage_present_in_production")
    if not gate["synthetic_dataset_passed"]:
        eval_issues.append("synthetic_dataset_present_in_production")

    evaluation_summary = {
        "score": float(round(gate_score, 6)),
        "status": eval_status,
        "issues": eval_issues,
        "notes": [
            "Evaluation is advisory-only and does not block pipeline execution.",
            f"pipeline_success_rate={metrics['pipeline_success_rate']:.3f}",
            f"zoning_success_rate={metrics['zoning_success_rate']:.3f}",
            f"po2_gate_passed={gate['po2_gate_passed']}",
        ],
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "pipeline_success_rate": {
                "target": PIPELINE_GATE_TARGET,
                "justification": "PO-2 requires majority end-to-end success before unlocking downstream work.",
            },
            "zoning_success_rate": {
                "target": ZONING_GATE_TARGET,
                "justification": "Zoning lookup must be highly reliable to avoid cascading false negatives.",
            },
            "district_accuracy": {
                "target": 0.95,
                "justification": "Wrong district assignment invalidates downstream feasibility calculations.",
            },
            "rule_completeness_rate": {
                "target": 0.95,
                "justification": "Layout-safe zoning fields must be present for nearly all production cases.",
            },
            "parcel_usability_rate": {
                "target": 0.95,
                "justification": "Production benchmark parcels must be layout-usable, not merely zoning-resolvable.",
            },
            "stub_zoning_rate": {
                "target_max": 0.0,
                "justification": "Production progress cannot be measured against stub zoning outputs.",
            },
            "fallback_usage_rate": {
                "target_max": 0.0,
                "justification": "Fallback-derived rules mask representation regressions in real datasets.",
            },
            "synthetic_dataset_rate": {
                "target_max": 0.0,
                "justification": "Synthetic datasets are fixture-only and must not influence production signal.",
            },
        },
        "scope_lock": {
            "allowed_blocker_agents": ["zoning_agent", "parcel_agent"],
            "frozen_layers_until_gate_pass": ["layout", "feasibility"],
            "policy": "No new features or refactors while PO-2 gate is failing.",
        },
        "gate": gate,
        "evaluation_summary": evaluation_summary,
        "metrics": metrics,
        "trend_since_last_run": trend,
        "recommendations": recommendations,
        "cases": rows,
    }
    return report


def save_report(report: dict[str, Any]) -> None:
    LATEST_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    with HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, sort_keys=True))
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run PO-2 stabilization gate checks.")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Deprecated no-op flag kept for CLI compatibility (evaluation is always non-blocking).",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write current report to the PO-2 baseline file.",
    )
    args = parser.parse_args(argv)

    report = build_report()
    save_report(report)
    if args.write_baseline:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
