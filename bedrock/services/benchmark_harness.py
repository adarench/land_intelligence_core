"""Reusable benchmark harness for the Bedrock feasibility pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"
for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from contracts.feasibility import FeasibilityResult
from contracts.market_data import MarketData
from contracts.validators import build_zoning_rules, validate_contract, validate_feasibility_pipeline_contracts
from contracts.zoning import ZoningDistrict, ZoningRules
from engines import parcel_engine, zoning_engine
from orchestration.pipeline_runner import PipelineRunner, PipelineTelemetryRun
from pipelines.parcel_feasibility_pipeline import ParcelFeasibilityPipeline
from services.feasibility_service import FeasibilityService


DEFAULT_DATASET_ROOT = WORKSPACE_ROOT / "test_data"
DEFAULT_MANIFEST_PATH = DEFAULT_DATASET_ROOT / "benchmark_manifest.json"


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    parcel: Any
    zoning_rules: ZoningRules
    market_data: MarketData
    utility_cost_per_lot: float
    soft_cost_percent: float
    land_cost: float
    notes: str = ""


@dataclass(frozen=True)
class BenchmarkRecord:
    case_id: str
    benchmark_type: str
    status: str
    feasible: bool
    metrics: dict[str, Any]
    stage_runtimes: dict[str, float]
    stub_used: bool
    validation_passed: bool
    error: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "benchmark_type": self.benchmark_type,
            "status": self.status,
            "feasible": self.feasible,
            "metrics": self.metrics,
            "stage_runtimes": self.stage_runtimes,
            "stub_used": self.stub_used,
            "validation_passed": self.validation_passed,
            "error": self.error,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class PipelineMetrics:
    layouts_evaluated: int
    pipeline_runtime: float
    best_ROI: float | None
    best_unit_yield: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "layouts_evaluated": self.layouts_evaluated,
            "pipeline_runtime": self.pipeline_runtime,
            "best_ROI": self.best_ROI,
            "best_unit_yield": self.best_unit_yield,
        }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_case(entry: dict[str, Any], dataset_root: Path) -> BenchmarkCase:
    parcel = validate_contract("Parcel", _load_json(dataset_root / entry["parcel_file"]))
    zoning_rules = validate_contract("ZoningRules", _load_json(dataset_root / entry["zoning_file"]))
    assumptions = dict(entry.get("financial_assumptions") or {})
    market_data = MarketData(
        estimated_home_price=float(assumptions.get("estimated_home_price", 0.0)),
        construction_cost_per_home=float(
            assumptions.get("construction_cost_per_home", assumptions.get("cost_per_home", 0.0))
        ),
        road_cost_per_ft=float(assumptions.get("road_cost_per_ft", 0.0)),
        land_price=float(assumptions.get("land_cost", 0.0)),
        soft_cost_factor=float(assumptions.get("soft_cost_percent", 0.0)),
    )
    return BenchmarkCase(
        case_id=str(entry["case_id"]),
        parcel=parcel,
        zoning_rules=zoning_rules,
        market_data=market_data,
        utility_cost_per_lot=float(assumptions.get("utility_cost_per_lot", 0.0)),
        soft_cost_percent=float(assumptions.get("soft_cost_percent", 0.0)),
        land_cost=float(assumptions.get("land_cost", 0.0)),
        notes=str(entry.get("notes", "")),
    )


def load_benchmark_cases(
    dataset_root: Path | None = None,
    *,
    manifest_path: Path | None = None,
    case_ids: Sequence[str] | None = None,
) -> list[BenchmarkCase]:
    dataset_root = (dataset_root or DEFAULT_DATASET_ROOT).resolve()
    manifest_path = (manifest_path or DEFAULT_MANIFEST_PATH).resolve()
    manifest = _load_json(manifest_path)
    selected = set(case_ids or [])
    cases = [
        _load_case(entry, dataset_root)
        for entry in manifest.get("cases", [])
        if not selected or entry.get("case_id") in selected
    ]
    if selected and len(cases) != len(selected):
        missing = sorted(selected.difference({case.case_id for case in cases}))
        raise ValueError(f"Unknown benchmark case ids: {', '.join(missing)}")
    return cases


def _build_fixture_district(case: BenchmarkCase) -> ZoningDistrict:
    return ZoningDistrict(
        id=case.zoning_rules.district_id or f"{case.case_id}:{case.zoning_rules.district}",
        jurisdiction_id=case.zoning_rules.jurisdiction or case.parcel.jurisdiction,
        code=case.zoning_rules.district,
        description=case.zoning_rules.description or f"Benchmark fixture for {case.case_id}",
        metadata=case.zoning_rules.metadata,
    )


@contextmanager
def fixture_pipeline_services(case: BenchmarkCase) -> Iterator[None]:
    original_get_parcel = parcel_engine.get_parcel
    original_get_zoning = zoning_engine.get_zoning
    original_get_standards = zoning_engine.get_development_standards
    fixture_district = _build_fixture_district(case)
    fixture_standards = list(case.zoning_rules.standards)

    def _fixture_get_parcel(parcel_id: str):
        if parcel_id != case.parcel.parcel_id:
            raise ValueError(f"Fixture benchmark case {case.case_id} only supports parcel_id={case.parcel.parcel_id}")
        return case.parcel

    def _fixture_get_zoning(parcel):
        if parcel.parcel_id != case.parcel.parcel_id:
            raise ValueError(f"Unexpected parcel_id for benchmark zoning lookup: {parcel.parcel_id}")
        return fixture_district

    def _fixture_get_development_standards(parcel, zoning=None):
        if parcel.parcel_id != case.parcel.parcel_id:
            raise ValueError(f"Unexpected parcel_id for benchmark standards lookup: {parcel.parcel_id}")
        return fixture_standards

    parcel_engine.get_parcel = _fixture_get_parcel
    zoning_engine.get_zoning = _fixture_get_zoning
    zoning_engine.get_development_standards = _fixture_get_development_standards
    try:
        yield
    finally:
        parcel_engine.get_parcel = original_get_parcel
        zoning_engine.get_zoning = original_get_zoning
        zoning_engine.get_development_standards = original_get_standards


@contextmanager
def _noop_context() -> Iterator[None]:
    yield


def _financial_metrics(case: BenchmarkCase, units: int, road_length: float) -> dict[str, float | int | None]:
    revenue = float(units) * case.market_data.estimated_home_price
    construction_cost = float(units) * case.market_data.construction_cost_per_home
    road_cost = float(road_length) * case.market_data.road_cost_per_ft
    utility_cost = float(units) * case.utility_cost_per_lot
    hard_cost = construction_cost + road_cost + utility_cost
    soft_cost = hard_cost * case.soft_cost_percent
    total_cost = hard_cost + soft_cost + case.land_cost
    profit = revenue - total_cost
    roi = None if total_cost == 0 else profit / total_cost
    return {
        "units": units,
        "road_length": road_length,
        "revenue": revenue,
        "construction_cost": construction_cost,
        "road_cost": road_cost,
        "utility_cost": utility_cost,
        "soft_cost": soft_cost,
        "land_cost": case.land_cost,
        "total_cost": total_cost,
        "profit": profit,
        "ROI": roi,
    }


def _zoning_density_violation(case: BenchmarkCase, units: int) -> str | None:
    if case.zoning_rules.max_units_per_acre is None:
        return None
    max_units = int((case.parcel.area_sqft / 43560.0) * case.zoning_rules.max_units_per_acre)
    if units > max_units:
        return "exceeds_zoning_density_limit"
    return None


def _collect_stage_runtimes(run: PipelineTelemetryRun) -> dict[str, float]:
    stage_runtimes = {interaction.pipeline_stage: interaction.execution_time for interaction in run.interactions}
    total_runtime = run.metrics.get("execution_time")
    if total_runtime is not None:
        stage_runtimes["pipeline_total"] = float(total_runtime)
    return stage_runtimes


def _stub_used(run: PipelineTelemetryRun) -> bool:
    return any(interaction.stub_used for interaction in run.interactions)


def run_pipeline_case(case: BenchmarkCase, *, use_live_services: bool = False) -> BenchmarkRecord:
    runner = PipelineRunner()
    pipeline = ParcelFeasibilityPipeline()
    feasibility_service = FeasibilityService()
    stage_runtimes: dict[str, float] = {}

    try:
        context = _noop_context() if use_live_services else fixture_pipeline_services(case)
        with context:
            artifacts = runner.run_pipeline(
                pipeline_name="parcel_feasibility_benchmark",
                pipeline_fn=pipeline.run,
                inputs={"parcel_id": case.parcel.parcel_id},
            )
        run = next(reversed(runner.runs.values()))
        stage_runtimes = _collect_stage_runtimes(run)

        feasibility_started = time.perf_counter()
        scenario_evaluation = feasibility_service.summarize_scenario(
            artifacts.parcel,
            [artifacts.layout],
            case.market_data,
        )
        stage_runtimes["financial_feasibility"] = time.perf_counter() - feasibility_started
        feasibility_response = scenario_evaluation.layouts_ranked[0]

        zoning_rules = build_zoning_rules(
            artifacts.parcel.parcel_id,
            artifacts.zoning,
            artifacts.standards,
            jurisdiction=artifacts.parcel.jurisdiction,
        )
        validate_feasibility_pipeline_contracts(
            artifacts.parcel,
            zoning_rules,
            artifacts.layout,
            artifacts.result,
        )

        financial_metrics = _financial_metrics(case, artifacts.layout.unit_count, artifacts.layout.road_length_ft)
        violations = sorted(
            set(artifacts.result.constraint_violations)
            | set(feasibility_response.constraint_violations)
            | (
                {density_violation}
                if (density_violation := _zoning_density_violation(case, artifacts.layout.unit_count))
                else set()
            )
        )
        metrics = {
            **financial_metrics,
            "runtime": stage_runtimes.get("pipeline_total", 0.0) + stage_runtimes["financial_feasibility"],
            "layouts_evaluated": scenario_evaluation.layout_count,
            "pipeline_runtime": stage_runtimes.get("pipeline_total", 0.0),
            "best_ROI": scenario_evaluation.best_roi,
            "best_unit_yield": scenario_evaluation.best_units,
            "constraint_violations": violations,
        }
        feasible = not violations and metrics["profit"] >= 0
        return BenchmarkRecord(
            case_id=case.case_id,
            benchmark_type="pipeline",
            status="success",
            feasible=feasible,
            metrics=metrics,
            stage_runtimes=stage_runtimes,
            stub_used=_stub_used(run),
            validation_passed=True,
            notes=case.notes,
        )
    except Exception as exc:
        return BenchmarkRecord(
            case_id=case.case_id,
            benchmark_type="pipeline",
            status="failure",
            feasible=False,
            metrics={
                "units": 0,
                "road_length": 0.0,
                "profit": 0.0,
                "ROI": None,
                "runtime": stage_runtimes.get("pipeline_total", 0.0),
                "layouts_evaluated": 0,
                "pipeline_runtime": stage_runtimes.get("pipeline_total", 0.0),
                "best_ROI": None,
                "best_unit_yield": None,
                "constraint_violations": [],
            },
            stage_runtimes=stage_runtimes,
            stub_used=False,
            validation_passed=False,
            error=str(exc),
            notes=case.notes,
        )

def run_layout_case(case: BenchmarkCase) -> BenchmarkRecord:
    stage_runtimes: dict[str, float] = {}
    try:
        zoning_district = _build_fixture_district(case)
        standards = list(case.zoning_rules.standards)
        started = time.perf_counter()
        layout = parcel_engine.generate_layout(case.parcel, zoning_district, standards)
        stage_runtimes["layout_generation"] = time.perf_counter() - started

        feasibility_started = time.perf_counter()
        scenario_evaluation = FeasibilityService().summarize_scenario(case.parcel, [layout], case.market_data)
        feasibility_result = scenario_evaluation.layouts_ranked[0]
        stage_runtimes["financial_feasibility"] = time.perf_counter() - feasibility_started

        validate_feasibility_pipeline_contracts(
            case.parcel,
            case.zoning_rules,
            layout,
            feasibility_result.model_dump(),
        )
        financial_metrics = _financial_metrics(case, layout.unit_count, layout.road_length_ft)
        violations = sorted(
            set(feasibility_result.constraint_violations)
            | (
                {density_violation}
                if (density_violation := _zoning_density_violation(case, layout.unit_count))
                else set()
            )
        )
        metrics = {
            **financial_metrics,
            "runtime": sum(stage_runtimes.values()),
            "layouts_evaluated": scenario_evaluation.layout_count,
            "pipeline_runtime": sum(stage_runtimes.values()),
            "best_ROI": scenario_evaluation.best_roi,
            "best_unit_yield": scenario_evaluation.best_units,
            "constraint_violations": violations,
        }
        feasible = not violations and metrics["profit"] >= 0
        return BenchmarkRecord(
            case_id=case.case_id,
            benchmark_type="layout",
            status="success",
            feasible=feasible,
            metrics=metrics,
            stage_runtimes=stage_runtimes,
            stub_used=bool(layout.metadata and layout.metadata.source_run_id == "stub"),
            validation_passed=True,
            notes=case.notes,
        )
    except Exception as exc:
        return BenchmarkRecord(
            case_id=case.case_id,
            benchmark_type="layout",
            status="failure",
            feasible=False,
            metrics={
                "units": 0,
                "road_length": 0.0,
                "profit": 0.0,
                "ROI": None,
                "runtime": sum(stage_runtimes.values()),
                "layouts_evaluated": 0,
                "pipeline_runtime": sum(stage_runtimes.values()),
                "best_ROI": None,
                "best_unit_yield": None,
                "constraint_violations": [],
            },
            stage_runtimes=stage_runtimes,
            stub_used=False,
            validation_passed=False,
            error=str(exc),
            notes=case.notes,
        )


def _summary(records: Sequence[BenchmarkRecord], *, benchmark_type: str) -> dict[str, Any]:
    successful = [record for record in records if record.status == "success"]
    runtimes = [float(record.metrics.get("runtime", 0.0) or 0.0) for record in successful]
    profits = [float(record.metrics.get("profit", 0.0) or 0.0) for record in successful]
    rois = [record.metrics.get("ROI") for record in successful if record.metrics.get("ROI") is not None]
    ranked_rois = [record.metrics.get("best_ROI") for record in successful if record.metrics.get("best_ROI") is not None]
    layouts_evaluated = [int(record.metrics.get("layouts_evaluated", 0) or 0) for record in successful]
    best_unit_yields = [
        int(record.metrics.get("best_unit_yield", 0) or 0)
        for record in successful
        if record.metrics.get("best_unit_yield") is not None
    ]
    pipeline_metrics = PipelineMetrics(
        layouts_evaluated=sum(layouts_evaluated),
        pipeline_runtime=sum(float(record.metrics.get("pipeline_runtime", 0.0) or 0.0) for record in successful),
        best_ROI=max(ranked_rois) if ranked_rois else None,
        best_unit_yield=max(best_unit_yields) if best_unit_yields else None,
    )
    return {
        "benchmark_type": benchmark_type,
        "dataset_size": len(records),
        "success_count": sum(record.status == "success" for record in records),
        "failure_count": sum(record.status == "failure" for record in records),
        "feasible_count": sum(record.feasible for record in records),
        "stub_case_count": sum(record.stub_used for record in records),
        "average_runtime": (sum(runtimes) / len(runtimes)) if runtimes else 0.0,
        "average_profit": (sum(profits) / len(profits)) if profits else 0.0,
        "average_ROI": (sum(rois) / len(rois)) if rois else None,
        "pipeline_metrics": pipeline_metrics.to_dict(),
        "records": [record.to_dict() for record in records],
    }


def run_pipeline_benchmark(
    dataset_root: Path | None = None,
    *,
    manifest_path: Path | None = None,
    case_ids: Sequence[str] | None = None,
    use_live_services: bool = False,
) -> dict[str, Any]:
    cases = load_benchmark_cases(dataset_root, manifest_path=manifest_path, case_ids=case_ids)
    records = [run_pipeline_case(case, use_live_services=use_live_services) for case in cases]
    return _summary(records, benchmark_type="pipeline")


def run_layout_benchmark(
    dataset_root: Path | None = None,
    *,
    manifest_path: Path | None = None,
    case_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    cases = load_benchmark_cases(dataset_root, manifest_path=manifest_path, case_ids=case_ids)
    records = [run_layout_case(case) for case in cases]
    return _summary(records, benchmark_type="layout")


def _build_parser(benchmark_type: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{benchmark_type}_benchmark")
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--case", dest="cases", action="append", default=[])
    parser.add_argument("--pretty", action="store_true")
    if benchmark_type == "pipeline":
        parser.add_argument("--use-live-services", action="store_true")
    return parser


def main_pipeline(argv: Sequence[str] | None = None) -> int:
    args = _build_parser("pipeline").parse_args(argv)
    report = run_pipeline_benchmark(
        dataset_root=args.dataset_root,
        manifest_path=args.manifest,
        case_ids=args.cases or None,
        use_live_services=args.use_live_services,
    )
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0


def main_layout(argv: Sequence[str] | None = None) -> int:
    args = _build_parser("layout").parse_args(argv)
    report = run_layout_benchmark(
        dataset_root=args.dataset_root,
        manifest_path=args.manifest,
        case_ids=args.cases or None,
    )
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0
