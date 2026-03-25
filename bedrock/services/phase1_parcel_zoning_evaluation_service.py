"""Phase 1 evaluation harness for Parcel + Zoning runtime performance."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"
for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.contracts.parcel import Parcel
from bedrock.services.parcel_service import ParcelService
from bedrock.services.zoning_service import ZoningService

DEFAULT_DATASET_PATH = WORKSPACE_ROOT / "test_data" / "phase1_parcel_zoning_dataset.json"
DEFAULT_OUTPUT_PATH = WORKSPACE_ROOT / "bedrock" / "benchmarks" / "phase1_parcel_zoning_results.json"

_REQUIRED_RULE_FIELDS = (
    "district",
    "min_lot_size_sqft",
    "max_units_per_acre",
    "setbacks.front",
    "setbacks.side",
    "setbacks.rear",
)


@dataclass(frozen=True)
class Phase1Case:
    parcel_id: str
    jurisdiction: str
    zoning_district: str
    geometry: dict[str, Any]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_phase1_dataset(dataset_path: Path | None = None, *, case_ids: Sequence[str] | None = None) -> tuple[str, list[Phase1Case]]:
    dataset_path = (dataset_path or DEFAULT_DATASET_PATH).resolve()
    payload = _load_json(dataset_path)
    selected = set(case_ids or [])
    records = [
        Phase1Case(
            parcel_id=str(item["parcel_id"]),
            jurisdiction=str(item.get("jurisdiction", "unknown")),
            zoning_district=str(item.get("zoning_district", "")),
            geometry=dict(item["geometry"]),
        )
        for item in payload.get("records", [])
        if not selected or item.get("parcel_id") in selected
    ]
    if selected and len(records) != len(selected):
        missing = sorted(selected.difference({record.parcel_id for record in records}))
        raise ValueError(f"Unknown phase1 parcel ids: {', '.join(missing)}")
    return str(payload.get("dataset", dataset_path.stem)), records


def _rule_completeness_ratio(rules) -> float:
    present = 0
    if getattr(rules, "district", None):
        present += 1
    if getattr(rules, "min_lot_size_sqft", None) is not None:
        present += 1
    if getattr(rules, "max_units_per_acre", None) is not None:
        present += 1
    setbacks = getattr(rules, "setbacks", None)
    for edge in ("front", "side", "rear"):
        value = getattr(setbacks, edge, None) if setbacks is not None else None
        if value is not None:
            present += 1
    return present / len(_REQUIRED_RULE_FIELDS)


def _runtime_summary(runtimes: list[float]) -> dict[str, float]:
    if not runtimes:
        return {"total_seconds": 0.0, "avg_seconds": 0.0, "p95_seconds": 0.0}
    p95 = max(runtimes)
    if len(runtimes) >= 2:
        p95 = statistics.quantiles(runtimes, n=20, method="inclusive")[18]
    return {
        "total_seconds": float(sum(runtimes)),
        "avg_seconds": float(sum(runtimes) / len(runtimes)),
        "p95_seconds": float(p95),
    }


def run_phase1_evaluation(
    dataset_path: Path | None = None,
    *,
    case_ids: Sequence[str] | None = None,
    zoning_dataset_root: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    dataset_name, cases = load_phase1_dataset(dataset_path, case_ids=case_ids)
    parcel_service = ParcelService()
    zoning_service = ZoningService(dataset_root=zoning_dataset_root)

    records: list[dict[str, Any]] = []
    case_runtimes: list[float] = []
    completeness_scores: list[float] = []
    parcel_success_count = 0
    zoning_success_count = 0
    schema_violation_count = 0
    rule_extraction_failure_count = 0

    for case in cases:
        started = time.perf_counter()
        parcel_runtime = 0.0
        zoning_runtime = 0.0
        rule_completeness = 0.0
        schema_valid = False
        rule_extraction_success = False
        errors: list[str] = []
        normalized_parcel: Parcel | None = None

        try:
            parcel_started = time.perf_counter()
            if parcel_service.parcel_exists(case.parcel_id):
                normalized_parcel = parcel_service.get_parcel(case.parcel_id)
                if normalized_parcel is None:
                    raise RuntimeError(f"parcel_exists true but parcel missing: {case.parcel_id}")
            else:
                normalized_parcel = parcel_service.load_parcel(
                    geometry=case.geometry,
                    parcel_id=case.parcel_id,
                    jurisdiction=case.jurisdiction,
                )
            parcel_runtime = time.perf_counter() - parcel_started
            parcel_success_count += 1
            schema_valid = True
        except Exception as exc:
            errors.append(f"parcel_normalization:{exc}")
            schema_violation_count += 1

        if normalized_parcel is not None:
            try:
                zoning_started = time.perf_counter()
                zoning_result = zoning_service.lookup(normalized_parcel)
                zoning_runtime = time.perf_counter() - zoning_started
                zoning_success_count += 1
                rule_extraction_success = True
                rule_completeness = _rule_completeness_ratio(zoning_result.rules)
                completeness_scores.append(rule_completeness)
            except Exception as exc:
                errors.append(f"zoning_lookup:{exc}")
                rule_extraction_failure_count += 1

        case_runtime = time.perf_counter() - started
        case_runtimes.append(case_runtime)
        records.append(
            {
                "parcel_id": case.parcel_id,
                "jurisdiction": case.jurisdiction,
                "zoning_district": case.zoning_district,
                "parcel_normalization_success": normalized_parcel is not None,
                "rule_extraction_success": rule_extraction_success,
                "schema_valid": schema_valid,
                "zoning_rule_completeness": rule_completeness,
                "runtime": {
                    "parcel_seconds": parcel_runtime,
                    "zoning_seconds": zoning_runtime,
                    "pipeline_seconds": case_runtime,
                },
                "errors": errors,
            }
        )

    metrics = {
        "parcel_normalization_success_rate": (parcel_success_count / len(cases)) if cases else 0.0,
        "zoning_rule_completeness": (sum(completeness_scores) / len(completeness_scores)) if completeness_scores else 0.0,
        "pipeline_runtime": _runtime_summary(case_runtimes),
        "schema_violation_count": schema_violation_count,
        "rule_extraction_failure_count": rule_extraction_failure_count,
        "zoning_lookup_success_rate": (zoning_success_count / len(cases)) if cases else 0.0,
    }
    report = {
        "dataset": dataset_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(cases),
        "metrics": metrics,
        "quality_gate": {
            "rule_completeness_target": 0.8,
            "rule_completeness_passed": metrics["zoning_rule_completeness"] > 0.8,
        },
        "records": records,
    }
    write_phase1_report(report, output_path=output_path)
    return report


def write_phase1_report(report: dict[str, Any], *, output_path: Path | None = None) -> Path:
    path = (output_path or DEFAULT_OUTPUT_PATH).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return path


def load_phase1_report(path: Path) -> dict[str, Any]:
    return _load_json(path.resolve())


def compare_phase1_reports(
    *,
    current: dict[str, Any],
    baseline: dict[str, Any],
    max_runtime_regression_pct: float = 0.2,
) -> dict[str, Any]:
    regressions: list[dict[str, Any]] = []
    current_metrics = current.get("metrics", {})
    baseline_metrics = baseline.get("metrics", {})

    current_completeness = float(current_metrics.get("zoning_rule_completeness", 0.0))
    baseline_completeness = float(baseline_metrics.get("zoning_rule_completeness", 0.0))
    if current_completeness + 1e-9 < baseline_completeness:
        regressions.append(
            {
                "metric": "zoning_rule_completeness",
                "baseline": baseline_completeness,
                "current": current_completeness,
                "reason": "completeness_drop",
            }
        )

    current_failures = int(current_metrics.get("rule_extraction_failure_count", 0))
    baseline_failures = int(baseline_metrics.get("rule_extraction_failure_count", 0))
    if current_failures > baseline_failures:
        regressions.append(
            {
                "metric": "rule_extraction_failure_count",
                "baseline": baseline_failures,
                "current": current_failures,
                "reason": "extraction_failures_increased",
            }
        )

    current_schema_violations = int(current_metrics.get("schema_violation_count", 0))
    baseline_schema_violations = int(baseline_metrics.get("schema_violation_count", 0))
    if current_schema_violations > baseline_schema_violations:
        regressions.append(
            {
                "metric": "schema_violation_count",
                "baseline": baseline_schema_violations,
                "current": current_schema_violations,
                "reason": "schema_violations_increased",
            }
        )

    current_avg_runtime = float(current_metrics.get("pipeline_runtime", {}).get("avg_seconds", 0.0))
    baseline_avg_runtime = float(baseline_metrics.get("pipeline_runtime", {}).get("avg_seconds", 0.0))
    if baseline_avg_runtime > 0 and current_avg_runtime > baseline_avg_runtime * (1.0 + max_runtime_regression_pct):
        regressions.append(
            {
                "metric": "pipeline_runtime.avg_seconds",
                "baseline": baseline_avg_runtime,
                "current": current_avg_runtime,
                "reason": "runtime_regression",
            }
        )

    return {
        "has_regression": bool(regressions),
        "regression_count": len(regressions),
        "regressions": regressions,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phase1_parcel_zoning_evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--zoning-dataset-root", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--case", dest="cases", action="append", default=[])
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = run_phase1_evaluation(
        dataset_path=args.dataset,
        case_ids=args.cases or None,
        zoning_dataset_root=args.zoning_dataset_root,
        output_path=args.output,
    )
    payload: dict[str, Any] = {"report": report}
    if args.baseline:
        baseline = load_phase1_report(args.baseline)
        payload["regression"] = compare_phase1_reports(current=report, baseline=baseline)
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
