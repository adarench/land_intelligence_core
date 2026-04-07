"""Pure functions for FEMA flood zone risk assessment and cost adjustment."""

from __future__ import annotations

from dataclasses import dataclass

# FEMA high-risk flood zones (Special Flood Hazard Areas)
HIGH_RISK_ZONES = frozenset({"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"})
# Moderate-to-low risk zones
MODERATE_RISK_ZONES = frozenset({"B", "X500", "0.2 PCT ANNUAL CHANCE"})
# Minimal risk zones
MINIMAL_RISK_ZONES = frozenset({"C", "X", "AREA OF MINIMAL FLOOD HAZARD"})


@dataclass(frozen=True)
class FloodAssessment:
    """Result of intersecting a parcel with FEMA NFHL flood data."""

    flood_zone: str | None
    flood_area_ratio: float  # 0.0-1.0: fraction of parcel in flood zone
    cost_multiplier: float  # multiplier on development cost for flood mitigation
    is_high_risk: bool
    buildable_area_reduction_sqft: float


def assess_flood_risk(
    *,
    flood_zone: str | None,
    flood_area_ratio: float,
    parcel_area_sqft: float,
) -> FloodAssessment:
    """Classify flood risk and compute cost/buildable-area impacts.

    Args:
        flood_zone: FEMA zone designation (e.g., "AE", "X", None).
        flood_area_ratio: Fraction of parcel area within the flood zone (0.0-1.0).
        parcel_area_sqft: Total parcel area in square feet.
    """
    flood_area_ratio = max(0.0, min(1.0, float(flood_area_ratio)))

    if flood_zone is None or flood_area_ratio <= 0.0:
        return FloodAssessment(
            flood_zone=flood_zone,
            flood_area_ratio=0.0,
            cost_multiplier=1.0,
            is_high_risk=False,
            buildable_area_reduction_sqft=0.0,
        )

    zone_upper = flood_zone.strip().upper()
    is_high_risk = zone_upper in HIGH_RISK_ZONES

    if is_high_risk:
        # High-risk zones: significant cost premium and buildable area reduction
        # Cost multiplier scales with flood area ratio
        cost_multiplier = 1.0 + (0.25 * flood_area_ratio)
        # Reduce buildable area by flood-affected portion (conservative)
        buildable_reduction = parcel_area_sqft * flood_area_ratio * 0.85
    elif zone_upper in MODERATE_RISK_ZONES:
        cost_multiplier = 1.0 + (0.08 * flood_area_ratio)
        buildable_reduction = parcel_area_sqft * flood_area_ratio * 0.3
    else:
        # Minimal risk / zone X
        cost_multiplier = 1.0
        buildable_reduction = 0.0

    return FloodAssessment(
        flood_zone=flood_zone,
        flood_area_ratio=flood_area_ratio,
        cost_multiplier=cost_multiplier,
        is_high_risk=is_high_risk,
        buildable_area_reduction_sqft=buildable_reduction,
    )


def calculate_flood_cost_adjustment(
    *,
    development_cost: float,
    assessment: FloodAssessment,
) -> float:
    """Additional development cost due to flood mitigation (fill, elevation, drainage).

    Returns the delta cost (amount to add), not the total.
    """
    if assessment.cost_multiplier <= 1.0:
        return 0.0
    return float(development_cost) * (assessment.cost_multiplier - 1.0)
