"""LLM-powered feasibility inference for parcels without overlay-backed zoning.

When the zoning pipeline cannot produce a decision-grade result (missing overlay,
degraded data, or bypass), this service uses Claude to estimate realistic
development parameters based on parcel context, nearby development patterns,
and internal closing data.

All outputs are explicitly labeled as INFERRED and include confidence scores.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import anthropic

from bedrock.contracts.parcel import Parcel

_CALIBRATION_PATH = Path(__file__).resolve().parents[1] / "data" / "market" / "flagship_calibration.json"
_MARKET_PATH = Path(__file__).resolve().parents[1] / "data" / "market" / "utah_market_reference_20260326.json"

INFERENCE_SCHEMA = {
    "mode": "INFERRED",
    "zoning_assumption": "string — estimated zoning type (e.g., 'low-density residential R-1-10')",
    "density_estimate_du_ac": "number — estimated dwelling units per acre",
    "min_lot_size_sqft_estimate": "number — estimated minimum lot size in sqft",
    "estimated_units_low": "integer — conservative unit count",
    "estimated_units_high": "integer — optimistic unit count",
    "estimated_units_mid": "integer — best estimate unit count",
    "price_per_unit": "number — estimated sale price per home",
    "cost_per_unit": "number — estimated total cost per home (construction + land + infrastructure)",
    "projected_revenue": "number",
    "projected_cost": "number",
    "projected_profit": "number",
    "roi": "number — as decimal (e.g., 0.15 for 15%)",
    "confidence": "number 0-1",
    "reasoning_summary": "string — 2-3 sentence explanation of the analysis",
    "key_assumptions": ["list of string — key assumptions made"],
    "recommendation": "acquire | renegotiate_price | pursue_rezoning | abandon",
}

SYSTEM_PROMPT = """You are a land development feasibility analyst for Utah residential subdivisions.

Given a parcel's characteristics and market context, estimate the most likely development scenario.

You MUST:
- Scale unit counts realistically with parcel acreage
- Use the internal closing data to anchor price and cost estimates
- Account for infrastructure (roads, utilities) consuming ~25-30% of gross acreage
- Never produce 1 unit for parcels over 2 acres
- Never produce $0 revenue or cost
- Be conservative — better to underestimate ROI than overestimate
- Return ONLY valid JSON matching the schema provided"""


def _load_calibration() -> dict:
    try:
        return json.loads(_CALIBRATION_PATH.read_text())
    except Exception:
        return {}


def _load_market_reference() -> dict:
    try:
        return json.loads(_MARKET_PATH.read_text())
    except Exception:
        return {}


def _build_context(parcel: Parcel, zoning_hint: Optional[dict] = None) -> str:
    """Build the context string for the LLM from parcel data and market info."""
    cal = _load_calibration()
    market = _load_market_reference()
    area_acres = parcel.area_sqft / 43560.0
    jurisdiction = parcel.jurisdiction or "Unknown"

    # Get calibration data for this jurisdiction
    cal_jurisdictions = cal.get("jurisdictions", {})
    cal_data = cal_jurisdictions.get(jurisdiction, {})
    global_cal = cal.get("global", {})

    # Get market data
    market_jurisdictions = market.get("jurisdictions", {})
    market_data = market_jurisdictions.get(jurisdiction, {})
    county_name = market_data.get("county_name", "")
    county_data = market.get("counties", {}).get(county_name, {})

    median_price = (
        cal_data.get("median_sale_price")
        or market_data.get("median_home_value")
        or county_data.get("median_home_value")
        or 480000
    )
    median_margin = cal_data.get("median_final_margin_pct") or global_cal.get("median_final_margin_pct") or 14.4
    median_cost = median_price * (1 - median_margin / 100)

    # Zoning hint from fallback
    zoning_district = "Unknown"
    zoning_density = None
    zoning_lot_size = None
    if zoning_hint:
        zoning_district = zoning_hint.get("district", "Unknown")
        zoning_density = zoning_hint.get("max_units_per_acre")
        zoning_lot_size = zoning_hint.get("min_lot_size_sqft")

    # Internal closing summary
    pricing_source = "internal calibration" if cal_data.get("median_sale_price") else "Census ACS 2024"
    n_closings = cal_data.get("n_total", 0)

    context = f"""PARCEL ANALYSIS REQUEST

Parcel ID: {parcel.parcel_id}
Jurisdiction: {jurisdiction}
County: {county_name or 'Unknown'}
Area: {area_acres:.2f} acres ({parcel.area_sqft:,.0f} sqft)
Zoning District (fallback): {zoning_district}
{f'Fallback density: {zoning_density} du/ac' if zoning_density else ''}
{f'Fallback min lot: {zoning_lot_size:,.0f} sqft' if zoning_lot_size else ''}

MARKET CONTEXT
Median home value ({jurisdiction}): ${median_price:,.0f} (source: {pricing_source})
{f'Internal closing data: {n_closings} closed deals in {jurisdiction}' if n_closings else 'No internal closing data for this jurisdiction'}
Median builder final margin: {median_margin:.1f}%
Estimated cost per home: ${median_cost:,.0f}
{f'SF median price: ${cal_data["sf_median_sale_price"]:,.0f}' if cal_data.get("sf_median_sale_price") else ''}
{f'TH median price: ${cal_data["th_median_sale_price"]:,.0f}' if cal_data.get("th_median_sale_price") else ''}

DEVELOPMENT ASSUMPTIONS
- Roads/infrastructure consume ~25-30% of gross acreage
- Typical Utah residential density: 3-5 du/ac (suburban), 1-2 du/ac (rural), 6-12 du/ac (townhome/multifamily)
- Land cost estimate: 18% of home value × acreage
- Soft costs: ~10% of hard costs
- Road cost: ~$300/ft
- Permitting: ~$3,500/unit
- Sitework: ~$18,000/acre

INSTRUCTIONS
Estimate the most likely single-family residential development scenario for this parcel.
If the parcel is very large (>20 acres) or in a rural area, consider that it may be agricultural/rural residential with low density.
If the parcel is in a suburban area with development activity, estimate suburban density."""

    return context


def run_inference(
    parcel: Parcel,
    *,
    zoning_hint: Optional[dict] = None,
    stream: bool = False,
) -> dict[str, Any]:
    """Run LLM inference to estimate feasibility for a parcel.

    Returns a structured dict with estimated development parameters.
    If stream=True, yields progress strings before the final result.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback_inference(parcel, zoning_hint)

    client = anthropic.Anthropic(api_key=api_key)
    context = _build_context(parcel, zoning_hint)

    user_prompt = f"""{context}

Return a JSON object with these fields:
- mode: "INFERRED"
- zoning_assumption: your estimated zoning type
- density_estimate_du_ac: estimated units per acre
- min_lot_size_sqft_estimate: estimated min lot size
- estimated_units_low: conservative count
- estimated_units_high: optimistic count
- estimated_units_mid: best estimate
- price_per_unit: estimated sale price
- cost_per_unit: estimated total cost per home
- projected_revenue: units × price
- projected_cost: units × cost + land + infrastructure
- projected_profit: revenue - cost
- roi: profit / cost as decimal
- confidence: 0-1 (higher = more data available)
- reasoning_summary: 2-3 sentences explaining your analysis
- key_assumptions: list of key assumptions
- recommendation: "acquire" | "renegotiate_price" | "pursue_rezoning" | "abandon"

Return ONLY the JSON object, no other text."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        content = response.content[0].text.strip()
        # Extract JSON
        if content.startswith("```"):
            import re
            content = re.sub(r"^```(?:json)?\n?", "", content)
            content = re.sub(r"\n?```$", "", content)

        result = json.loads(content)
        result["mode"] = "INFERRED"
        result["parcel_id"] = parcel.parcel_id
        result["jurisdiction"] = parcel.jurisdiction
        result["area_acres"] = parcel.area_sqft / 43560.0

        # Sanity checks
        result = _validate_inference(result, parcel)
        return result

    except Exception as e:
        return _fallback_inference(parcel, zoning_hint, error=str(e))


def run_inference_streaming(
    parcel: Parcel,
    *,
    zoning_hint: Optional[dict] = None,
):
    """Generator that yields progress updates then the final result."""
    yield {"type": "progress", "message": "Analyzing parcel context..."}

    area_acres = parcel.area_sqft / 43560.0
    yield {"type": "progress", "message": f"Parcel: {area_acres:.1f} acres in {parcel.jurisdiction or 'Unknown'}"}

    yield {"type": "progress", "message": "Estimating likely zoning and density..."}

    yield {"type": "progress", "message": "Applying local market comps and cost model..."}

    result = run_inference(parcel, zoning_hint=zoning_hint)

    yield {"type": "progress", "message": "Final feasibility computed."}
    yield {"type": "result", "data": result}


def _validate_inference(result: dict, parcel: Parcel) -> dict:
    """Apply sanity bounds to inference results.

    Only recalculates financials when values are missing/invalid or when units
    were forced up. This preserves parcel-specific differentiation from the LLM.
    """
    area_acres = parcel.area_sqft / 43560.0
    needs_recalc = False

    # Never 1 unit on >2 acre parcels
    units = result.get("estimated_units_mid", 0)
    if area_acres > 2 and units <= 1:
        min_units = max(2, int(area_acres * 0.5))
        result["estimated_units_mid"] = min_units
        result["estimated_units_low"] = max(1, min_units - 2)
        result["estimated_units_high"] = int(min_units * 1.3)
        needs_recalc = True

    # Never $0 revenue or cost
    if not result.get("price_per_unit") or result["price_per_unit"] <= 0:
        result["price_per_unit"] = 450000
        needs_recalc = True
    if not result.get("cost_per_unit") or result["cost_per_unit"] <= 0:
        result["cost_per_unit"] = 385000
        needs_recalc = True

    # Only recalculate if values are missing/invalid or units were corrected.
    # Preserving LLM-generated values is critical — unconditional recalc collapses
    # all parcel ROIs to the same (price-cost)/cost ratio regardless of parcel size.
    has_valid_financials = (
        result.get("projected_revenue", 0) > 0
        and result.get("projected_cost", 0) > 0
        and result.get("projected_profit") is not None
        and result.get("roi") is not None
    )
    if needs_recalc or not has_valid_financials:
        units = result.get("estimated_units_mid", 1)
        price = result.get("price_per_unit", 450000)
        cost = result.get("cost_per_unit", 385000)
        result["projected_revenue"] = units * price
        result["projected_cost"] = units * cost
        result["projected_profit"] = result["projected_revenue"] - result["projected_cost"]
        if result["projected_cost"] > 0:
            result["roi"] = result["projected_profit"] / result["projected_cost"]

    # Bound confidence
    conf = result.get("confidence", 0.5)
    result["confidence"] = max(0.1, min(0.85, conf))

    return result


def _fallback_inference(
    parcel: Parcel,
    zoning_hint: Optional[dict] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    """Deterministic fallback when LLM is unavailable."""
    area_acres = parcel.area_sqft / 43560.0
    cal = _load_calibration()
    global_cal = cal.get("global", {})

    # Estimate density from zoning hint or parcel size
    if zoning_hint and zoning_hint.get("max_units_per_acre"):
        density = float(zoning_hint["max_units_per_acre"])
    elif area_acres > 20:
        density = 0.5  # Rural
    elif area_acres > 5:
        density = 2.0  # Low-density
    else:
        density = 4.0  # Suburban

    units = max(2, int(area_acres * density * 0.7))  # 70% efficiency
    price = float(global_cal.get("median_sale_price", 450000))
    margin = float(global_cal.get("median_final_margin_pct", 14.4))
    cost = price * (1 - margin / 100)

    revenue = units * price
    total_cost = units * cost
    profit = revenue - total_cost
    roi = profit / total_cost if total_cost > 0 else 0

    return {
        "mode": "INFERRED",
        "parcel_id": parcel.parcel_id,
        "jurisdiction": parcel.jurisdiction,
        "area_acres": area_acres,
        "zoning_assumption": f"Estimated {'rural' if area_acres > 20 else 'suburban'} residential",
        "density_estimate_du_ac": density,
        "min_lot_size_sqft_estimate": int(43560 / density) if density > 0 else 43560,
        "estimated_units_low": max(1, int(units * 0.7)),
        "estimated_units_high": int(units * 1.3),
        "estimated_units_mid": units,
        "price_per_unit": price,
        "cost_per_unit": cost,
        "projected_revenue": revenue,
        "projected_cost": total_cost,
        "projected_profit": profit,
        "roi": roi,
        "confidence": 0.35,
        "reasoning_summary": f"Deterministic estimate based on {area_acres:.1f} acres at {density} du/ac. "
                            f"No LLM analysis available{f' ({error})' if error else ''}. "
                            f"Using global median pricing from internal closing data.",
        "key_assumptions": [
            f"Density: {density} du/ac (estimated from parcel size)",
            f"Price: ${price:,.0f} (global median from {global_cal.get('total_calibration_records', 0)} closings)",
            f"Margin: {margin:.1f}% (global median)",
            "Infrastructure: 30% of gross area",
        ],
        "recommendation": "renegotiate_price" if roi > 0.05 else "abandon",
    }
