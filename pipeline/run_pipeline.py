"""DEPRECATED: Temporary vertical integration for parcel -> zoning -> layout -> feasibility.

This module is superseded by bedrock.services.pipeline_service.PipelineService.
Use PipelineService.run() for single-pass evaluation or PipelineService.optimize()
for multi-round optimization with decision-grade output.

This file is retained for backward compatibility with existing scripts but should
not be used for new work. Its evaluate_feasibility() function uses a simplified
cost model that does not match FeasibilityService and overstates profit.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Optional
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import Field

from bedrock.api.layout_api import LayoutSearchRequest, layout_search
from bedrock.contracts.base import BedrockModel
from bedrock.contracts.feasibility_result import FeasibilityResult
from bedrock.contracts.layout_result import LayoutResult
from bedrock.contracts.market_data import MarketData
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.pipeline_run import PipelineRun
from bedrock.contracts.validators import (
    validate_feasibility_result_output,
    validate_layout_result_output,
    validate_parcel_output,
    validate_pipeline_run_output,
    validate_zoning_rules_for_layout,
)
from bedrock.contracts.zoning_rules import ZoningRules
from bedrock.services.parcel_service import ParcelService
from bedrock.services.pipeline_run_store import PipelineRunStore
from bedrock.services.zoning_service import IncompleteZoningRulesError, InvalidZoningRulesError, ZoningService
from zoning_data_scraper.services.zoning_overlay import (
    AmbiguousJurisdictionMatchError,
    AmbiguousZoningMatchError,
    NoJurisdictionMatchError,
    NoZoningMatchError,
)


class PipelineBundle(BedrockModel):
    parcel: Parcel
    zoning: ZoningRules
    layout: LayoutResult
    feasibility: FeasibilityResult
    stage_runtimes: dict[str, float] = Field(default_factory=dict)
    zoning_source: str = "stub"
    used_zoning_fallback: bool = False


DEFAULT_TEST_ZONING = {
    "district": "R-1",
    "min_lot_size_sqft": 6000,
    "max_units_per_acre": 5,
    "setbacks": {
        "front": 25,
        "side": 8,
        "rear": 20,
    },
    "min_frontage_ft": 50,
    "road_right_of_way_ft": 32,
    "height_limit_ft": 35,
    "lot_coverage_max": 0.45,
    "allowed_uses": ["single_family_residential"],
    "citations": ["temporary_integration_stub"],
}


def run_pipeline(
    parcel_geometry: Optional[dict[str, Any]] = None,
    *,
    parcel: Optional[Parcel | dict[str, Any]] = None,
    parcel_id: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    max_candidates: int = 50,
    market_data: Optional[MarketData] = None,
) -> PipelineBundle:
    stage_runtimes: dict[str, float] = {}

    started = perf_counter()
    parcel_contract = _load_parcel(
        parcel_geometry=parcel_geometry,
        parcel=parcel,
        parcel_id=parcel_id,
        jurisdiction=jurisdiction,
    )
    stage_runtimes["parcel.load"] = round(perf_counter() - started, 6)

    started = perf_counter()
    zoning_rules, zoning_source, used_zoning_fallback = get_zoning(parcel_contract)
    stage_runtimes["zoning.lookup"] = round(perf_counter() - started, 6)

    started = perf_counter()
    layout_result = _run_layout(parcel_contract, zoning_rules, max_candidates=max_candidates)
    stage_runtimes["layout.search"] = round(perf_counter() - started, 6)

    started = perf_counter()
    feasibility_result = evaluate_feasibility(layout_result, parcel=parcel_contract, market_data=market_data)
    stage_runtimes["feasibility.evaluate"] = round(perf_counter() - started, 6)

    return PipelineBundle(
        parcel=parcel_contract,
        zoning=zoning_rules,
        layout=layout_result,
        feasibility=feasibility_result,
        stage_runtimes=stage_runtimes,
        zoning_source=zoning_source,
        used_zoning_fallback=used_zoning_fallback,
    )


def get_zoning(parcel: Parcel) -> tuple[ZoningRules, str, bool]:
    service = ZoningService()
    try:
        lookup = service.lookup(parcel)
        rules = validate_zoning_rules_for_layout(lookup.rules)
        return rules, "lookup", False
    except (
        AmbiguousJurisdictionMatchError,
        AmbiguousZoningMatchError,
        IncompleteZoningRulesError,
        InvalidZoningRulesError,
        NoJurisdictionMatchError,
        NoZoningMatchError,
        ValueError,
    ):
        fallback = _stub_zoning(parcel)
        return validate_zoning_rules_for_layout(fallback), "stub", True


def evaluate_feasibility(
    layout: LayoutResult | dict[str, Any],
    *,
    parcel: Parcel,
    market_data: Optional[MarketData] = None,
) -> FeasibilityResult:
    layout_result = validate_layout_result_output(layout)
    units = int(layout_result.units)
    estimated_home_price = float(market_data.estimated_home_price) if market_data else 400000.0
    construction_cost_per_home = (
        float(market_data.construction_cost_per_home) if market_data else 300000.0
    )
    revenue = float(units) * estimated_home_price
    cost = float(units) * construction_cost_per_home
    profit = revenue - cost
    roi = None if cost <= 0 else profit / cost
    profit_margin = None if revenue <= 0 else profit / revenue
    status = "feasible" if profit >= 0 else "constrained"

    return validate_feasibility_result_output(
        FeasibilityResult(
            scenario_id=str(uuid5(NAMESPACE_URL, f"{parcel.parcel_id}:{layout_result.layout_id}:{units}")),
            layout_id=layout_result.layout_id,
            parcel_id=parcel.parcel_id,
            units=units,
            estimated_home_price=estimated_home_price,
            construction_cost_per_home=construction_cost_per_home,
            development_cost_total=cost,
            projected_revenue=revenue,
            projected_cost=cost,
            projected_profit=profit,
            ROI=roi,
            profit_margin=profit_margin,
            revenue_per_unit=estimated_home_price if units else 0.0,
            cost_per_unit=construction_cost_per_home if units else 0.0,
            risk_score=0.15,
            confidence=0.9,
            status=status,
            constraint_violations=[] if profit >= 0 else ["projected_profit_negative"],
            assumptions={
                "integration_mode": "temporary",
                "estimated_home_price": estimated_home_price,
                "construction_cost_per_home": construction_cost_per_home,
            },
            financial_summary={
                "estimated_home_price": estimated_home_price,
                "construction_cost_per_home": construction_cost_per_home,
                "development_cost_total": cost,
                "projected_revenue": revenue,
                "projected_cost": cost,
                "projected_profit": profit,
                "ROI": roi,
                "profit_margin": profit_margin,
            },
        )
    )


def persist_pipeline_run(
    bundle: PipelineBundle,
    *,
    run_store: Optional[PipelineRunStore] = None,
    input_payload: Optional[dict[str, Any]] = None,
) -> PipelineRun:
    store = run_store or PipelineRunStore()
    run_id = str(uuid4())
    pipeline_run = validate_pipeline_run_output(
        PipelineRun(
            run_id=run_id,
            status="completed",
            parcel_id=bundle.parcel.parcel_id,
            zoning_result=bundle.zoning,
            layout_result=bundle.layout,
            feasibility_result=bundle.feasibility,
            timestamp=datetime.now(timezone.utc).isoformat(),
            git_commit=_resolve_git_commit(),
            input_hash=_build_input_hash(input_payload or bundle.parcel.model_dump(mode="json")),
            stage_runtimes=bundle.stage_runtimes,
            zoning_bypassed=bundle.used_zoning_fallback,
            bypass_reason="temporary_stub_zoning" if bundle.used_zoning_fallback else None,
        )
    )
    store.save_run(run_id, pipeline_run)
    return pipeline_run


def _load_parcel(
    *,
    parcel_geometry: Optional[dict[str, Any]],
    parcel: Optional[Parcel | dict[str, Any]],
    parcel_id: Optional[str],
    jurisdiction: Optional[str],
) -> Parcel:
    service = ParcelService()
    if parcel is not None:
        return service.normalize_parcel_contract(parcel)
    if parcel_geometry is None:
        raise ValueError("parcel_geometry or parcel is required")
    return validate_parcel_output(
        service.load_parcel(
            geometry=parcel_geometry,
            parcel_id=parcel_id,
            jurisdiction=jurisdiction,
        )
    )


def _run_layout(parcel: Parcel, zoning: ZoningRules, *, max_candidates: int) -> LayoutResult:
    try:
        return validate_layout_result_output(
            layout_search(
                LayoutSearchRequest(
                    parcel=parcel,
                    zoning=zoning,
                    max_candidates=max_candidates,
                )
            )
        )
    except Exception:
        fallback_zoning = validate_zoning_rules_for_layout(_stub_zoning(parcel))
        return validate_layout_result_output(
            layout_search(
                LayoutSearchRequest(
                    parcel=parcel,
                    zoning=fallback_zoning,
                    max_candidates=max_candidates,
                )
            )
        )


def _stub_zoning(parcel: Parcel) -> ZoningRules:
    district = (parcel.zoning_district or DEFAULT_TEST_ZONING["district"]).strip() or DEFAULT_TEST_ZONING["district"]
    payload = {
        **DEFAULT_TEST_ZONING,
        "parcel_id": parcel.parcel_id,
        "jurisdiction": parcel.jurisdiction,
        "district": district,
    }
    return ZoningRules.model_validate(payload)


def _build_input_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_git_commit() -> Optional[str]:
    try:
        workspace_root = Path(__file__).resolve().parents[1]
        return subprocess.check_output(
            ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None
