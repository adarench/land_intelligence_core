"""Templated deal memo generation grounded in structured optimization outputs."""

from __future__ import annotations

from typing import Any, Optional


def generate_deal_memo(
    *,
    parcel_id: str,
    jurisdiction: str,
    area_acres: float,
    zoning_district: str,
    units: int,
    estimated_home_price: float,
    construction_cost_per_home: float,
    projected_revenue: float,
    projected_cost: float,
    projected_profit: float,
    roi: Optional[float],
    roi_best_case: Optional[float],
    roi_worst_case: Optional[float],
    break_even_price: Optional[float],
    confidence: float,
    recommendation: Optional[str],
    reason: Optional[str],
    key_risks: list[str],
    pricing_proxy: str,
    used_county_fallback: bool,
    zoning_bypassed: bool,
    median_home_value: Optional[float],
    land_price: Optional[float],
    target_price: Optional[float],
    min_lot_size_sqft: Optional[float] = None,
    max_units_per_acre: Optional[float] = None,
    front_setback_ft: Optional[float] = None,
    evaluation_grade: Optional[str] = None,
) -> dict[str, str]:
    """Return a dict of deal memo sections suitable for rendering."""

    sections: dict[str, str] = {}

    # Summary
    roi_pct = f"{roi * 100:.1f}%" if roi is not None else "N/A"
    profit_str = _currency(projected_profit)
    sections["summary"] = (
        f"This is a {units}-unit subdivision opportunity in {jurisdiction} "
        f"on {area_acres:.1f} acres zoned {zoning_district}. "
        f"The model projects {profit_str} profit at {roi_pct} ROI "
        f"with {confidence * 100:.0f}% confidence."
    )

    # Zoning constraints and unit calculation
    constraint_parts = []
    if min_lot_size_sqft:
        constraint_parts.append(f"minimum lot size {min_lot_size_sqft:,.0f} sqft")
    if max_units_per_acre:
        constraint_parts.append(f"maximum density {max_units_per_acre} units/acre")
        theoretical_max = int(area_acres * max_units_per_acre)
        constraint_parts.append(f"theoretical maximum ~{theoretical_max} units on {area_acres:.1f} acres")
    if front_setback_ft:
        constraint_parts.append(f"front setback {front_setback_ft:.0f} ft")

    if constraint_parts:
        sections["zoning_constraints"] = (
            f"District {zoning_district} requires: {'. '.join(constraint_parts)}. "
            f"After accounting for roads, setbacks, and infrastructure, the layout engine produced {units} buildable lots."
        )
        if zoning_bypassed:
            sections["zoning_constraints"] += " Note: these constraints are from fallback defaults, not a verified overlay."
    else:
        sections["zoning_constraints"] = f"Zoning district {zoning_district} — constraint details not available."

    # Evaluation grade
    if evaluation_grade:
        grade_labels = {
            "DECISION_GRADE": "This evaluation uses real zoning data and calibrated cost/revenue assumptions. Suitable for acquisition screening.",
            "EXPLORATORY": "This evaluation uses estimated or partially inferred data. Use for initial screening only — verify before committing.",
            "BLOCKED": "Insufficient data to produce a reliable evaluation. Do not use for decision-making.",
        }
        sections["evaluation_grade"] = grade_labels.get(evaluation_grade, f"Evaluation grade: {evaluation_grade}")

    # Revenue basis
    revenue_source = "jurisdiction-specific Census ACS 2024 median" if not used_county_fallback else "county-level Census ACS 2024 fallback"
    median_str = _currency(median_home_value) if median_home_value else "default ($480,000)"
    sections["revenue_basis"] = (
        f"Revenue assumes {units} homes at {_currency(estimated_home_price)} each, "
        f"derived from {revenue_source} data. "
        f"The market reference median for {jurisdiction} is {median_str}. "
        f"Total projected revenue is {_currency(projected_revenue)}."
    )

    # Cost basis
    sections["cost_basis"] = (
        f"Construction cost is estimated at {_currency(construction_cost_per_home)} per home "
        f"based on national baseline costs adjusted by regional price parity. "
        f"Total projected cost including land, infrastructure, permitting, and soft costs "
        f"is {_currency(projected_cost)}."
    )
    if land_price is not None:
        sections["cost_basis"] += f" Land basis is estimated at {_currency(land_price)} (18% of home value × acreage)."

    # Sensitivity
    sensitivity_parts = []
    if roi_worst_case is not None:
        sensitivity_parts.append(f"worst case {roi_worst_case * 100:.1f}%")
    if roi_best_case is not None:
        sensitivity_parts.append(f"best case {roi_best_case * 100:.1f}%")
    if break_even_price is not None:
        sensitivity_parts.append(f"break-even home price {_currency(break_even_price)}")
    if sensitivity_parts:
        sections["sensitivity"] = (
            f"ROI ranges from {' to '.join(sensitivity_parts[:2])}. "
            + (f"The deal breaks even at a home price of {_currency(break_even_price)}. " if break_even_price else "")
            + "These ranges reflect ±10% variation in revenue and ±8-10% in costs."
        )

    # Key risks
    if key_risks:
        readable = [r.replace("_", " ") for r in key_risks]
        sections["key_risks"] = "Key risk factors: " + ", ".join(readable) + "."
    else:
        sections["key_risks"] = "No elevated risk factors identified."

    # Data quality
    quality_notes = []
    if used_county_fallback:
        quality_notes.append("Revenue is based on county-level data, not jurisdiction-specific pricing.")
    if zoning_bypassed:
        quality_notes.append("Zoning uses fallback rules — not backed by a real GIS overlay for this jurisdiction.")
    quality_notes.append("Construction costs use national baseline data adjusted by regional price parity. Actual costs may differ.")
    sections["data_quality"] = " ".join(quality_notes)

    # Recommendation
    if recommendation:
        rec_label = recommendation.replace("_", " ")
        sections["recommendation"] = (
            f"System recommendation: {rec_label}. "
            + (reason or "")
        )
        if target_price is not None:
            sections["recommendation"] += f" Recommended maximum offer: {_currency(target_price)}."
    else:
        if projected_profit >= 0 and roi is not None and roi >= 0.15:
            sections["recommendation"] = "This parcel appears to meet acquisition thresholds under current assumptions."
        elif projected_profit >= 0:
            sections["recommendation"] = "This parcel is marginally positive. Review sensitivity before committing."
        else:
            sections["recommendation"] = "This parcel does not meet thresholds under current assumptions."

    return sections


def _currency(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1000:
        return f"${value:,.0f}"
    return f"${value:.0f}"
