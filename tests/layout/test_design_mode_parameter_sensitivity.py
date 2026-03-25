from __future__ import annotations

from bedrock.contracts.zoning_rules import DevelopmentStandard, SetbackSet, ZoningRules
from bedrock.services.layout_service import search_layout
from tests.pipeline.test_zoning_to_layout import CASES


BASE_TEMPLATE = {
    "minLotSizeSqft": 6000.0,
    "frontSetbackFt": 25.0,
    "sideSetbackFt": 8.0,
    "rearSetbackFt": 20.0,
    "maxUnitsPerAcre": 5.0,
    "roadWidthFt": 32.0,
    "lotFrontageFt": 50.0,
    "blockDepthFt": 110.0,
    "internalOffsetFt": 0.0,
    "strategy": "grid",
}

def _target_lot_depth_ft(block_depth_ft: float, internal_offset_ft: float, front_setback_ft: float, rear_setback_ft: float) -> float:
    return max(40.0, block_depth_ft - front_setback_ft - rear_setback_ft - internal_offset_ft * 2.0)


def _effective_frontage_ft(min_lot_size_sqft: float, target_lot_depth_ft: float, lot_frontage_ft: float, side_setback_ft: float) -> float:
    required_buildable_width_ft = min_lot_size_sqft / max(target_lot_depth_ft, 1.0)
    return max(lot_frontage_ft, required_buildable_width_ft + side_setback_ft * 2.0)


def _build_design_mode_zoning(template: dict[str, float | str]) -> ZoningRules:
    case = CASES[0]
    parcel = case.parcel
    district = case.district
    front = float(template["frontSetbackFt"]) + float(template["internalOffsetFt"])
    side = float(template["sideSetbackFt"]) + float(template["internalOffsetFt"])
    rear = float(template["rearSetbackFt"]) + float(template["internalOffsetFt"])
    target_depth_ft = _target_lot_depth_ft(
        float(template["blockDepthFt"]),
        float(template["internalOffsetFt"]),
        float(template["frontSetbackFt"]),
        float(template["rearSetbackFt"]),
    )
    effective_frontage_ft = _effective_frontage_ft(
        float(template["minLotSizeSqft"]),
        target_depth_ft,
        float(template["lotFrontageFt"]),
        side,
    )

    standards = [
        DevelopmentStandard(
            id=f"{district}:min_lot_size_sqft",
            standard_type="min_lot_size_sqft",
            value=float(template["minLotSizeSqft"]),
            units="sqft",
        ),
        DevelopmentStandard(
            id=f"{district}:max_units_per_acre",
            standard_type="max_units_per_acre",
            value=float(template["maxUnitsPerAcre"]),
            units="du/ac",
        ),
        DevelopmentStandard(
            id=f"{district}:front_setback_ft",
            standard_type="front_setback_ft",
            value=front,
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:side_setback_ft",
            standard_type="side_setback_ft",
            value=side,
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:rear_setback_ft",
            standard_type="rear_setback_ft",
            value=rear,
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:min_frontage_ft",
            standard_type="min_frontage_ft",
            value=effective_frontage_ft,
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:lot_frontage_ft",
            standard_type="lot_frontage_ft",
            value=effective_frontage_ft,
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:frontage_min_ft",
            standard_type="frontage_min_ft",
            value=effective_frontage_ft,
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:road_right_of_way_ft",
            standard_type="road_right_of_way_ft",
            value=float(template["roadWidthFt"]),
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:layout_block_depth_ft",
            standard_type="layout_block_depth_ft",
            value=target_depth_ft,
            units="ft",
        ),
        DevelopmentStandard(
            id=f"{district}:easement_buffer_ft",
            standard_type="easement_buffer_ft",
            value=float(template["internalOffsetFt"]),
            units="ft",
        ),
    ]

    return ZoningRules(
        parcel_id=parcel.parcel_id,
        jurisdiction=case.jurisdiction,
        district=district,
        overlays=[],
        standards=standards,
        setbacks=SetbackSet(front=front, side=side, rear=rear),
        min_lot_size_sqft=float(template["minLotSizeSqft"]),
        max_units_per_acre=float(template["maxUnitsPerAcre"]),
        min_frontage_ft=effective_frontage_ft,
        road_right_of_way_ft=float(template["roadWidthFt"]),
        citations=["ui_design_mode_default_template"],
    )


def test_design_mode_controls_change_layout_geometry() -> None:
    case = CASES[0]
    parcel = case.parcel
    base_layout = search_layout(parcel, _build_design_mode_zoning(BASE_TEMPLATE), max_candidates=50)

    variants = {
        "min_lot_size_sqft": {**BASE_TEMPLATE, "minLotSizeSqft": 8000.0},
        "max_units_per_acre": {**BASE_TEMPLATE, "maxUnitsPerAcre": 3.0},
        "front_setback_ft": {**BASE_TEMPLATE, "frontSetbackFt": 40.0},
        "side_setback_ft": {**BASE_TEMPLATE, "sideSetbackFt": 16.0},
        "rear_setback_ft": {**BASE_TEMPLATE, "rearSetbackFt": 35.0},
        "road_width_ft": {**BASE_TEMPLATE, "roadWidthFt": 44.0},
        "lot_frontage_ft": {**BASE_TEMPLATE, "lotFrontageFt": 120.0},
        "block_depth_ft": {**BASE_TEMPLATE, "blockDepthFt": 150.0},
        "internal_offset_ft": {**BASE_TEMPLATE, "internalOffsetFt": 18.0},
    }

    for name, template in variants.items():
        layout = search_layout(parcel, _build_design_mode_zoning(template), max_candidates=50)
        assert layout.layout_id != base_layout.layout_id, f"{name} did not change layout_id"
