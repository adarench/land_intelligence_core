from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from bedrock.services.zoning_service import ZoningService
from tests.pipeline.test_zoning_to_layout import CASES, _run_layout_candidate, normalized_rule_dataset


def _standard_numeric_value(zoning_rules, *standard_types: str) -> float | None:
    wanted = {item.lower() for item in standard_types}
    for standard in zoning_rules.standards:
        if standard.standard_type.lower() not in wanted:
            continue
        try:
            return float(standard.value)
        except (TypeError, ValueError):
            return None
    return None


@pytest.fixture
def zoning_layout_fixture_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Reuse the exact normalized fixture setup from the zoning->layout integration tests.
    return normalized_rule_dataset.__wrapped__(tmp_path, monkeypatch)


def test_zoning_layout_constraint_compliance(zoning_layout_fixture_dataset: Path) -> None:
    zoning_service = ZoningService(dataset_root=zoning_layout_fixture_dataset)
    violations: list[dict[str, object]] = []
    compliance_summary: list[dict[str, object]] = []

    for case in CASES:
        zoning_result = zoning_service.lookup(case.parcel)
        candidate, _solver_constraints = _run_layout_candidate(case, zoning_result.rules, use_prior=True, max_candidates=8)
        lots = candidate.result.lots

        min_lot_size_sqft = float(zoning_result.rules.min_lot_size_sqft)
        front_setback_ft = float(case.front_setback_ft)
        rear_setback_ft = float(case.rear_setback_ft)
        side_setback_ft = float(case.side_setback_ft)
        max_depth_ft = max(80.0, 110.0 - front_setback_ft - rear_setback_ft)
        required_buildable_width_ft = min_lot_size_sqft / max(max_depth_ft, 1.0)
        min_frontage_with_setbacks_ft = required_buildable_width_ft + (2.0 * side_setback_ft)

        min_depth_ft = _standard_numeric_value(
            zoning_result.rules,
            "min_depth_ft",
            "min_lot_depth_ft",
            "minimum_lot_depth_ft",
        )
        max_units_allowed = float(zoning_result.rules.max_units_per_acre) * (float(case.parcel.area_sqft) / 43560.0)
        lot_count = float(candidate.result.metrics.get("lot_count", len(lots)))

        if lot_count - 1e-6 > max_units_allowed:
            violations.append(
                {
                    "jurisdiction": case.jurisdiction,
                    "district": case.district,
                    "constraint": "density_cap",
                    "units": lot_count,
                    "max_units_allowed": round(max_units_allowed, 4),
                }
            )

        area_pass = 0
        depth_pass = 0
        min_depth_pass = 0
        frontage_pass = 0

        for lot_index, lot in enumerate(lots, start=1):
            lot_area_sqft = float(lot.area_sqft)
            lot_depth_ft = float(lot.depth_ft)
            lot_frontage_ft = float(lot.frontage_ft)

            area_ok = lot_area_sqft + 1e-6 >= min_lot_size_sqft
            depth_ok = lot_depth_ft <= max_depth_ft * 1.02
            min_depth_ok = True if min_depth_ft is None else lot_depth_ft + 1e-6 >= min_depth_ft
            frontage_ok = lot_frontage_ft + 1e-6 >= min_frontage_with_setbacks_ft

            area_pass += int(area_ok)
            depth_pass += int(depth_ok)
            min_depth_pass += int(min_depth_ok)
            frontage_pass += int(frontage_ok)

            if not area_ok:
                violations.append(
                    {
                        "jurisdiction": case.jurisdiction,
                        "district": case.district,
                        "lot_index": lot_index,
                        "constraint": "min_lot_size_sqft",
                        "lot_area_sqft": round(lot_area_sqft, 2),
                        "min_lot_size_sqft": min_lot_size_sqft,
                    }
                )
            if not depth_ok:
                violations.append(
                    {
                        "jurisdiction": case.jurisdiction,
                        "district": case.district,
                        "lot_index": lot_index,
                        "constraint": "max_buildable_depth_ft",
                        "lot_depth_ft": round(lot_depth_ft, 2),
                        "max_depth_ft": round(max_depth_ft, 2),
                    }
                )
            if not min_depth_ok:
                violations.append(
                    {
                        "jurisdiction": case.jurisdiction,
                        "district": case.district,
                        "lot_index": lot_index,
                        "constraint": "min_depth_ft",
                        "lot_depth_ft": round(lot_depth_ft, 2),
                        "min_depth_ft": round(float(min_depth_ft), 2),
                    }
                )
            if not frontage_ok:
                violations.append(
                    {
                        "jurisdiction": case.jurisdiction,
                        "district": case.district,
                        "lot_index": lot_index,
                        "constraint": "frontage_with_side_setbacks",
                        "lot_frontage_ft": round(lot_frontage_ft, 2),
                        "required_frontage_ft": round(min_frontage_with_setbacks_ft, 2),
                    }
                )

        compliance_summary.append(
            {
                "jurisdiction": case.jurisdiction,
                "district": case.district,
                "lots": len(lots),
                "units": int(lot_count),
                "density_cap": math.floor(max_units_allowed),
                "area_pass": area_pass,
                "depth_pass": depth_pass,
                "min_depth_pass": min_depth_pass,
                "frontage_pass": frontage_pass,
                "min_depth_ft": min_depth_ft,
            }
        )

    assert not violations, json.dumps(
        {
            "constraint_compliance_summary": compliance_summary,
            "violations": violations,
        },
        indent=2,
        sort_keys=True,
    )
