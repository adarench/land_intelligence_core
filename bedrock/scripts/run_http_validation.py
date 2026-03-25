from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"
for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from api.feasibility_api import router as feasibility_router
from api.layout_api import app as layout_app
from api.parcel_api import create_router as create_parcel_router
from api.pipeline_api import router as pipeline_router
from api.zoning_api import router as zoning_router
from contracts.pipeline_run import PipelineRun
from contracts.validators import (
    validate_contract,
    validate_feasibility_result_output,
    validate_layout_result_output,
    validate_parcel_output,
    validate_zoning_rules_for_layout,
)
from services.parcel_service import ParcelService


ENDPOINTS = (
    "parcel.load",
    "zoning.lookup",
    "layout.search",
    "feasibility.evaluate",
    "pipeline.run",
)

ENDPOINT_PATH = {
    "parcel.load": "/parcel/load",
    "zoning.lookup": "/zoning/lookup",
    "layout.search": "/layout/search",
    "feasibility.evaluate": "/feasibility/evaluate",
    "pipeline.run": "/pipeline/run",
}


def _nested_get(payload: Any, dotted_path: str) -> Any:
    current = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _build_app() -> FastAPI:
    app = FastAPI(title="Bedrock HTTP Validation Harness")
    app.include_router(create_parcel_router(ParcelService()))
    app.include_router(zoning_router)
    app.include_router(layout_app.router)
    app.include_router(feasibility_router)
    app.include_router(pipeline_router)
    return app


def _safe_json(response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return {"raw": response.text}


def _extract_error_class(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            if detail.get("error"):
                return str(detail["error"])
            return "validation_error"
        if isinstance(detail, str):
            return "validation_error"
        if payload.get("error"):
            return str(payload["error"])
    return "http_error"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * p
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return values[lower]
    weight = rank - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _validate_contract_for_endpoint(endpoint: str, payload: dict[str, Any]) -> None:
    if endpoint == "parcel.load":
        validate_parcel_output(validate_contract("Parcel", payload))
        return
    if endpoint == "zoning.lookup":
        zoning = validate_contract("ZoningRules", payload)
        validate_zoning_rules_for_layout(zoning)
        return
    if endpoint == "layout.search":
        validate_layout_result_output(validate_contract("LayoutResult", payload))
        return
    if endpoint == "feasibility.evaluate":
        validate_feasibility_result_output(validate_contract("FeasibilityResult", payload))
        return
    if endpoint == "pipeline.run":
        response = PipelineRun.model_validate(payload)
        validate_zoning_rules_for_layout(validate_contract("ZoningRules", response.zoning_result.model_dump(mode="json")))
        validate_layout_result_output(validate_contract("LayoutResult", response.layout_result.model_dump(mode="json")))
        validate_feasibility_result_output(
            validate_contract("FeasibilityResult", response.feasibility_result.model_dump(mode="json"))
        )
        return
    raise ValueError(f"Unsupported endpoint for contract validation: {endpoint}")


def _invoke(client: TestClient, endpoint: str, json_payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.post(ENDPOINT_PATH[endpoint], json=json_payload)
    runtime = time.perf_counter() - started
    payload = _safe_json(response)
    ok = 200 <= response.status_code < 300
    contract_ok = False
    contract_error = None
    if ok:
        try:
            _validate_contract_for_endpoint(endpoint, payload)
            contract_ok = True
        except Exception as exc:  # pragma: no cover - defensive logging path
            contract_ok = False
            contract_error = str(exc)
    error_class = None if ok else _extract_error_class(payload)
    return {
        "endpoint": endpoint,
        "status_code": response.status_code,
        "ok": ok,
        "runtime_seconds": runtime,
        "payload": payload,
        "contract_ok": contract_ok,
        "contract_error": contract_error,
        "error_class": error_class,
    }


def _stage_status(result: dict[str, Any]) -> str:
    return "PASS" if result["ok"] and result["contract_ok"] else "FAIL"


def run_validation(config: dict[str, Any]) -> dict[str, Any]:
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)

    endpoint_runtimes: dict[str, list[float]] = defaultdict(list)
    endpoint_status: dict[str, list[bool]] = defaultdict(list)
    endpoint_contract_status: dict[str, list[bool]] = defaultdict(list)
    endpoint_errors: dict[str, Counter[str]] = defaultdict(Counter)
    endpoint_contract_errors: dict[str, Counter[str]] = defaultdict(Counter)
    endpoint_server_errors: dict[str, int] = defaultdict(int)
    success_by_jurisdiction: Counter[str] = Counter()
    total_by_jurisdiction: Counter[str] = Counter()

    case_results: list[dict[str, Any]] = []
    chain_success_count = 0
    expected_outcome_match_count = 0
    case_lookup: dict[str, dict[str, Any]] = {}

    for case in config["cases"]:
        case_id = str(case["case_id"])
        jurisdiction = str(case.get("jurisdiction") or "unknown")
        parcel_request = {
            "parcel_id": case_id,
            "geometry": case["geometry"],
            "jurisdiction": jurisdiction,
        }

        case_outcome = {
            "case_id": case_id,
            "jurisdiction": jurisdiction,
            "expected_chain_success": bool(case.get("expected_chain_success", True)),
            "notes": case.get("notes", ""),
            "stages": {},
        }

        parcel_result = _invoke(client, "parcel.load", parcel_request)
        case_outcome["stages"]["parcel.load"] = parcel_result
        parcel_payload = parcel_result["payload"] if parcel_result["ok"] else None

        zoning_result = {
            "endpoint": "zoning.lookup",
            "status_code": 0,
            "ok": False,
            "runtime_seconds": 0.0,
            "payload": {},
            "contract_ok": False,
            "contract_error": None,
            "error_class": "skipped_due_to_parcel_failure",
        }
        layout_result = {
            "endpoint": "layout.search",
            "status_code": 0,
            "ok": False,
            "runtime_seconds": 0.0,
            "payload": {},
            "contract_ok": False,
            "contract_error": None,
            "error_class": "skipped_due_to_upstream_failure",
        }
        feasibility_result = {
            "endpoint": "feasibility.evaluate",
            "status_code": 0,
            "ok": False,
            "runtime_seconds": 0.0,
            "payload": {},
            "contract_ok": False,
            "contract_error": None,
            "error_class": "skipped_due_to_upstream_failure",
        }

        if parcel_payload is not None:
            zoning_result = _invoke(client, "zoning.lookup", {"parcel": parcel_payload})
            if zoning_result["ok"]:
                layout_result = _invoke(
                    client,
                    "layout.search",
                    {
                        "parcel": parcel_payload,
                        "zoning": zoning_result["payload"],
                        "max_candidates": int(case.get("max_candidates", config.get("default_max_candidates", 8))),
                    },
                )
                if layout_result["ok"]:
                    feasibility_result = _invoke(
                        client,
                        "feasibility.evaluate",
                        {
                            "parcel": parcel_payload,
                            "layout": layout_result["payload"],
                        },
                    )

        pipeline_result = _invoke(
            client,
            "pipeline.run",
            {
                "parcel_geometry": case["geometry"],
                "parcel_id": f"{case_id}-pipeline",
                "jurisdiction": jurisdiction,
                "max_candidates": int(case.get("max_candidates", config.get("default_max_candidates", 8))),
            },
        )

        case_outcome["stages"]["zoning.lookup"] = zoning_result
        case_outcome["stages"]["layout.search"] = layout_result
        case_outcome["stages"]["feasibility.evaluate"] = feasibility_result
        case_outcome["stages"]["pipeline.run"] = pipeline_result

        chain_success = all(
            _stage_status(case_outcome["stages"][endpoint]) == "PASS"
            for endpoint in ENDPOINTS
        )
        case_outcome["chain_status"] = "SUCCESS" if chain_success else "FAILURE"
        case_outcome["expected_outcome_match"] = bool(case_outcome["expected_chain_success"] == chain_success)
        total_by_jurisdiction[jurisdiction] += 1
        case_lookup[case_id] = case
        if case_outcome["expected_outcome_match"]:
            expected_outcome_match_count += 1
        if chain_success:
            chain_success_count += 1
            success_by_jurisdiction[jurisdiction] += 1
        case_results.append(case_outcome)

        for endpoint in ENDPOINTS:
            result = case_outcome["stages"][endpoint]
            if result["status_code"] == 0 and not result["runtime_seconds"]:
                continue
            endpoint_runtimes[endpoint].append(float(result["runtime_seconds"]))
            endpoint_status[endpoint].append(bool(result["ok"]))
            if int(result["status_code"]) >= 500:
                endpoint_server_errors[endpoint] += 1
            if result["ok"]:
                endpoint_contract_status[endpoint].append(bool(result["contract_ok"]))
                if result["contract_error"]:
                    endpoint_contract_errors[endpoint][str(result["contract_error"])] += 1
            elif result["error_class"]:
                endpoint_errors[endpoint][str(result["error_class"])] += 1

    endpoint_metrics: dict[str, dict[str, Any]] = {}
    for endpoint in ENDPOINTS:
        raw_runtimes = list(endpoint_runtimes.get(endpoint, []))
        runtimes = sorted(raw_runtimes)
        warm_runtimes = sorted(raw_runtimes[1:]) if len(raw_runtimes) > 1 else []
        successes = endpoint_status.get(endpoint, [])
        contract_checks = endpoint_contract_status.get(endpoint, [])
        endpoint_metrics[endpoint] = {
            "request_count": len(successes),
            "success_count": sum(1 for item in successes if item),
            "success_rate": (sum(1 for item in successes if item) / len(successes)) if successes else 0.0,
            "runtime_seconds": {
                "avg": statistics.fmean(runtimes) if runtimes else 0.0,
                "p95": _percentile(runtimes, 0.95) if runtimes else 0.0,
                "max": max(runtimes) if runtimes else 0.0,
            },
            "runtime_seconds_warm": {
                "avg": statistics.fmean(warm_runtimes) if warm_runtimes else 0.0,
                "p95": _percentile(warm_runtimes, 0.95) if warm_runtimes else 0.0,
                "max": max(warm_runtimes) if warm_runtimes else 0.0,
            },
            "contract_checks": len(contract_checks),
            "contract_pass_count": sum(1 for item in contract_checks if item),
            "contract_conformance_rate": (
                sum(1 for item in contract_checks if item) / len(contract_checks)
            )
            if contract_checks
            else 0.0,
            "error_classes": dict(endpoint_errors.get(endpoint, Counter())),
            "contract_errors": dict(endpoint_contract_errors.get(endpoint, Counter())),
            "server_error_count": int(endpoint_server_errors.get(endpoint, 0)),
        }

    total_cases = len(case_results)
    chain_success_rate = (chain_success_count / total_cases) if total_cases else 0.0
    expected_outcome_match_rate = (expected_outcome_match_count / total_cases) if total_cases else 0.0
    total_server_errors = sum(endpoint_server_errors.values())
    unexpected_case_failures = [
        case["case_id"]
        for case in case_results
        if case["expected_chain_success"] and case["chain_status"] != "SUCCESS"
    ]
    failure_mode_counts: Counter[str] = Counter()
    for case in case_results:
        for endpoint in ENDPOINTS:
            stage = case["stages"][endpoint]
            if stage["ok"]:
                continue
            error_class = stage.get("error_class") or "unknown_error"
            failure_mode_counts[f"{endpoint}:{error_class}"] += 1

    determinism_config = config.get("determinism", {})
    determinism_rows: list[dict[str, Any]] = []
    determinism_runs = max(2, int(determinism_config.get("runs", 3)))
    determinism_fields = [str(item) for item in determinism_config.get("fields", [])]
    determinism_case_ids = [str(item) for item in determinism_config.get("case_ids", [])]
    for case_id in determinism_case_ids:
        case = case_lookup.get(case_id)
        if case is None:
            determinism_rows.append(
                {
                    "case_id": case_id,
                    "runs": [],
                    "consistent": False,
                    "reason": "unknown_case_id",
                }
            )
            continue
        observations: list[dict[str, Any]] = []
        for run_index in range(determinism_runs):
            result = _invoke(
                client,
                "pipeline.run",
                {
                    "parcel_geometry": case["geometry"],
                    "parcel_id": f"{case_id}-determinism-{run_index + 1}",
                    "jurisdiction": str(case.get("jurisdiction") or "unknown"),
                    "max_candidates": int(case.get("max_candidates", config.get("default_max_candidates", 8))),
                },
            )
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            fields_payload = {
                field_name: (
                    result["status_code"]
                    if field_name == "status_code"
                    else _nested_get(payload, field_name)
                )
                for field_name in determinism_fields
            }
            observations.append(
                {
                    "run_index": run_index + 1,
                    "status_code": result["status_code"],
                    "ok": result["ok"],
                    "error_class": result["error_class"],
                    "runtime_seconds": result["runtime_seconds"],
                    "fields": fields_payload,
                }
            )
        first_fields = observations[0]["fields"] if observations else {}
        consistent = bool(observations) and all(item["fields"] == first_fields for item in observations[1:])
        determinism_rows.append(
            {
                "case_id": case_id,
                "jurisdiction": str(case.get("jurisdiction") or "unknown"),
                "runs": observations,
                "consistent": consistent,
                "reason": None if consistent else "field_mismatch_across_runs",
            }
        )

    determinism_summary = {
        "configured": bool(determinism_case_ids),
        "runs_per_case": determinism_runs,
        "fields": determinism_fields,
        "case_count": len(determinism_rows),
        "consistent_case_count": sum(1 for item in determinism_rows if item.get("consistent")),
        "consistency_rate": (
            sum(1 for item in determinism_rows if item.get("consistent")) / len(determinism_rows)
        )
        if determinism_rows
        else 0.0,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_name": config.get("name"),
        "summary": {
            "total_cases": total_cases,
            "chain_success_count": chain_success_count,
            "chain_success_rate": chain_success_rate,
            "expected_outcome_match_count": expected_outcome_match_count,
            "expected_outcome_match_rate": expected_outcome_match_rate,
            "expected_success_cases": sum(1 for case in case_results if case["expected_chain_success"]),
            "unexpected_case_failures": unexpected_case_failures,
            "server_error_count": total_server_errors,
        },
        "failure_modes": dict(failure_mode_counts),
        "success_by_jurisdiction": {
            jurisdiction: {
                "success_count": int(success_by_jurisdiction.get(jurisdiction, 0)),
                "total_count": int(total_by_jurisdiction.get(jurisdiction, 0)),
                "success_rate": (
                    float(success_by_jurisdiction.get(jurisdiction, 0)) / float(total_by_jurisdiction[jurisdiction])
                )
                if total_by_jurisdiction[jurisdiction]
                else 0.0,
            }
            for jurisdiction in sorted(total_by_jurisdiction.keys())
        },
        "endpoint_metrics": endpoint_metrics,
        "determinism": {
            "summary": determinism_summary,
            "cases": determinism_rows,
        },
        "case_results": case_results,
    }


def evaluate_gates(
    report: dict[str, Any],
    config: dict[str, Any],
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gates_config = config.get("gates", {})
    absolute = gates_config.get("absolute", {})
    regression = gates_config.get("regression", {})
    endpoint_metrics = report["endpoint_metrics"]
    failures: list[dict[str, Any]] = []
    passes: list[dict[str, Any]] = []

    min_chain = float(absolute.get("min_chain_success_rate", 0.0))
    actual_chain = float(report["summary"]["chain_success_rate"])
    if actual_chain < min_chain:
        failures.append({"gate": "min_chain_success_rate", "actual": actual_chain, "expected_min": min_chain})
    else:
            passes.append({"gate": "min_chain_success_rate", "actual": actual_chain, "expected_min": min_chain})

    if "min_expected_outcome_match_rate" in absolute:
        min_expected_match = float(absolute.get("min_expected_outcome_match_rate", 0.0))
        actual_expected_match = float(report["summary"].get("expected_outcome_match_rate", 0.0))
        if actual_expected_match < min_expected_match:
            failures.append(
                {
                    "gate": "min_expected_outcome_match_rate",
                    "actual": actual_expected_match,
                    "expected_min": min_expected_match,
                }
            )
        else:
            passes.append(
                {
                    "gate": "min_expected_outcome_match_rate",
                    "actual": actual_expected_match,
                    "expected_min": min_expected_match,
                }
            )

    if "min_determinism_consistency_rate" in absolute:
        min_determinism = float(absolute.get("min_determinism_consistency_rate", 0.0))
        actual_determinism = float(report.get("determinism", {}).get("summary", {}).get("consistency_rate", 0.0))
        if actual_determinism < min_determinism:
            failures.append(
                {
                    "gate": "min_determinism_consistency_rate",
                    "actual": actual_determinism,
                    "expected_min": min_determinism,
                }
            )
        else:
            passes.append(
                {
                    "gate": "min_determinism_consistency_rate",
                    "actual": actual_determinism,
                    "expected_min": min_determinism,
                }
            )

    endpoint_min_success = absolute.get("endpoint_min_success_rate", {})
    for endpoint, minimum in endpoint_min_success.items():
        actual = float(endpoint_metrics.get(endpoint, {}).get("success_rate", 0.0))
        if actual < float(minimum):
            failures.append(
                {
                    "gate": "endpoint_min_success_rate",
                    "endpoint": endpoint,
                    "actual": actual,
                    "expected_min": float(minimum),
                }
            )
        else:
            passes.append(
                {
                    "gate": "endpoint_min_success_rate",
                    "endpoint": endpoint,
                    "actual": actual,
                    "expected_min": float(minimum),
                }
            )

    max_unexpected_500 = int(absolute.get("max_unexpected_500_errors", 0))
    actual_500 = int(report["summary"].get("server_error_count", 0))
    if actual_500 > max_unexpected_500:
        failures.append(
            {
                "gate": "max_unexpected_500_errors",
                "actual": actual_500,
                "expected_max": max_unexpected_500,
            }
        )
    else:
        passes.append(
            {
                "gate": "max_unexpected_500_errors",
                "actual": actual_500,
                "expected_max": max_unexpected_500,
            }
        )

    endpoint_max_p95 = absolute.get("endpoint_max_p95_seconds", {})
    for endpoint, maximum in endpoint_max_p95.items():
        runtime_payload = endpoint_metrics.get(endpoint, {})
        warm_p95 = float(runtime_payload.get("runtime_seconds_warm", {}).get("p95", 0.0))
        actual = warm_p95 if warm_p95 > 0 else float(runtime_payload.get("runtime_seconds", {}).get("p95", 0.0))
        if actual > float(maximum):
            failures.append(
                {
                    "gate": "endpoint_max_p95_seconds",
                    "endpoint": endpoint,
                    "actual": actual,
                    "expected_max": float(maximum),
                }
            )
        else:
            passes.append(
                {
                    "gate": "endpoint_max_p95_seconds",
                    "endpoint": endpoint,
                    "actual": actual,
                    "expected_max": float(maximum),
                }
            )

    min_contract_rate = float(absolute.get("min_contract_conformance_rate", 1.0))
    for endpoint in ENDPOINTS:
        checks = int(endpoint_metrics.get(endpoint, {}).get("contract_checks", 0))
        if checks == 0:
            continue
        actual = float(endpoint_metrics.get(endpoint, {}).get("contract_conformance_rate", 0.0))
        if actual < min_contract_rate:
            failures.append(
                {
                    "gate": "min_contract_conformance_rate",
                    "endpoint": endpoint,
                    "actual": actual,
                    "expected_min": min_contract_rate,
                }
            )
        else:
            passes.append(
                {
                    "gate": "min_contract_conformance_rate",
                    "endpoint": endpoint,
                    "actual": actual,
                    "expected_min": min_contract_rate,
                }
            )

    allowed_error_classes = absolute.get("allowed_error_classes", {})
    for endpoint in ENDPOINTS:
        allowed = set(allowed_error_classes.get(endpoint, []))
        observed = set(endpoint_metrics.get(endpoint, {}).get("error_classes", {}).keys())
        unexpected = sorted(observed - allowed)
        if unexpected:
            failures.append(
                {
                    "gate": "allowed_error_classes",
                    "endpoint": endpoint,
                    "unexpected_error_classes": unexpected,
                    "allowed": sorted(allowed),
                }
            )
        else:
            passes.append(
                {
                    "gate": "allowed_error_classes",
                    "endpoint": endpoint,
                    "observed": sorted(observed),
                    "allowed": sorted(allowed),
                }
            )

    if baseline is not None:
        max_success_drop = float(regression.get("max_success_rate_drop", 1.0))
        max_runtime_ratio = float(regression.get("max_runtime_regression_ratio", 999.0))
        allow_new_error_classes = bool(regression.get("allow_new_error_classes", True))

        for endpoint in ENDPOINTS:
            current_success = float(endpoint_metrics.get(endpoint, {}).get("success_rate", 0.0))
            baseline_success = float(
                baseline.get("endpoint_metrics", {})
                .get(endpoint, {})
                .get("success_rate", 0.0)
            )
            success_drop = baseline_success - current_success
            if success_drop > max_success_drop:
                failures.append(
                    {
                        "gate": "regression_success_rate_drop",
                        "endpoint": endpoint,
                        "baseline": baseline_success,
                        "current": current_success,
                        "drop": success_drop,
                        "max_drop": max_success_drop,
                    }
                )
            else:
                passes.append(
                    {
                        "gate": "regression_success_rate_drop",
                        "endpoint": endpoint,
                        "baseline": baseline_success,
                        "current": current_success,
                        "drop": success_drop,
                        "max_drop": max_success_drop,
                    }
                )

            current_p95 = float(endpoint_metrics.get(endpoint, {}).get("runtime_seconds", {}).get("p95", 0.0))
            current_p95_warm = float(
                endpoint_metrics.get(endpoint, {})
                .get("runtime_seconds_warm", {})
                .get("p95", 0.0)
            )
            baseline_p95 = float(
                baseline.get("endpoint_metrics", {})
                .get(endpoint, {})
                .get("runtime_seconds", {})
                .get("p95", 0.0)
            )
            baseline_p95_warm = float(
                baseline.get("endpoint_metrics", {})
                .get(endpoint, {})
                .get("runtime_seconds_warm", {})
                .get("p95", 0.0)
            )
            active_current = current_p95_warm if current_p95_warm > 0 else current_p95
            active_baseline = baseline_p95_warm if baseline_p95_warm > 0 else baseline_p95
            ratio = (active_current / active_baseline) if active_baseline > 0 else 1.0
            if active_baseline > 0 and ratio > max_runtime_ratio:
                failures.append(
                    {
                        "gate": "regression_runtime_ratio",
                        "endpoint": endpoint,
                        "baseline_p95": active_baseline,
                        "current_p95": active_current,
                        "ratio": ratio,
                        "max_ratio": max_runtime_ratio,
                    }
                )
            else:
                passes.append(
                    {
                        "gate": "regression_runtime_ratio",
                        "endpoint": endpoint,
                        "baseline_p95": active_baseline,
                        "current_p95": active_current,
                        "ratio": ratio,
                        "max_ratio": max_runtime_ratio,
                    }
                )

            if not allow_new_error_classes:
                current_errors = set(endpoint_metrics.get(endpoint, {}).get("error_classes", {}).keys())
                baseline_errors = set(
                    baseline.get("endpoint_metrics", {})
                    .get(endpoint, {})
                    .get("error_classes", {})
                    .keys()
                )
                new_errors = sorted(current_errors - baseline_errors)
                if new_errors:
                    failures.append(
                        {
                            "gate": "regression_new_error_classes",
                            "endpoint": endpoint,
                            "new_error_classes": new_errors,
                            "baseline_error_classes": sorted(baseline_errors),
                        }
                    )
                else:
                    passes.append(
                        {
                            "gate": "regression_new_error_classes",
                            "endpoint": endpoint,
                            "current_error_classes": sorted(current_errors),
                            "baseline_error_classes": sorted(baseline_errors),
                        }
                    )

    return {
        "advisory_only": True,
        "passed": not failures,
        "failure_count": len(failures),
        "failures": failures,
        "passes": passes,
    }


def build_open_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for case in report["case_results"]:
        expected_success = bool(case["expected_chain_success"])
        for endpoint in ENDPOINTS:
            stage = case["stages"][endpoint]
            if stage["status_code"] == 0 and stage["error_class"].startswith("skipped"):
                continue
            if stage["ok"] and stage["contract_ok"]:
                continue
            severity = "medium"
            if expected_success:
                severity = "high"
            error_class = stage.get("error_class")
            if isinstance(error_class, str) and "topology" in error_class.lower():
                severity = "critical"
            issues.append(
                {
                    "severity": severity,
                    "case_id": case["case_id"],
                    "endpoint": endpoint,
                    "status_code": stage["status_code"],
                    "error_class": error_class,
                    "contract_error": stage.get("contract_error"),
                    "notes": case.get("notes", ""),
                }
            )
    return issues


def build_evaluation_summary(report: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    failures = list(gates.get("failures", []))
    passes = list(gates.get("passes", []))
    total_checks = len(failures) + len(passes)
    score = (len(passes) / total_checks) if total_checks else 1.0

    if score >= 0.9 and not failures:
        status = "good"
    elif score >= 0.7:
        status = "warning"
    else:
        status = "poor"

    issues: list[str] = []
    for failure in failures:
        endpoint = failure.get("endpoint")
        gate_name = failure.get("gate", "unknown_gate")
        if endpoint:
            issues.append(f"{gate_name}@{endpoint}")
        else:
            issues.append(str(gate_name))

    notes = [
        "Evaluation is advisory-only and does not block pipeline execution.",
        f"total_gates={total_checks}",
        f"failed_gates={len(failures)}",
        f"chain_success_rate={report.get('summary', {}).get('chain_success_rate', 0.0):.3f}",
    ]

    return {
        "score": float(round(score, 6)),
        "status": status,
        "issues": issues,
        "notes": notes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full HTTP-chain API validation harness.")
    parser.add_argument(
        "--config",
        type=Path,
        default=WORKSPACE_ROOT / "bedrock" / "benchmarks" / "http_validation_config.json",
        help="Path to validation config dataset + gates.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=WORKSPACE_ROOT / "bedrock" / "benchmarks" / "http_validation_report.json",
        help="Path to write latest validation report.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=WORKSPACE_ROOT / "bedrock" / "benchmarks" / "http_validation_baseline.json",
        help="Path to baseline report for regression gates.",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write current report as baseline after run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = json.loads(args.config.read_text())

    report = run_validation(config)
    baseline = json.loads(args.baseline.read_text()) if args.baseline.exists() else None
    gates = evaluate_gates(report, config, baseline=baseline)
    open_issues = build_open_issues(report)
    evaluation_summary = build_evaluation_summary(report, gates)

    report["gates"] = gates
    report["open_issues"] = open_issues
    report["evaluation_summary"] = evaluation_summary
    report["baseline_used"] = str(args.baseline) if baseline is not None else None

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(report, indent=2))

    print(f"HTTP validation report written: {args.output}")
    if args.write_baseline:
        print(f"HTTP validation baseline written: {args.baseline}")
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "gates": report["gates"],
                "evaluation_summary": report["evaluation_summary"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
