"""Composable deterministic financial models used by the feasibility service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from bedrock.contracts.layout_result import LayoutResult
from bedrock.contracts.market_data import MarketData
from bedrock.contracts.parcel import Parcel
from bedrock.models.cost_models import (
    CostBreakdown,
    calculate_construction_cost,
    calculate_development_cost,
    calculate_grading_cost,
    calculate_land_cost,
    calculate_sitework_cost,
    calculate_soft_costs,
    calculate_total_cost,
    calculate_utilities_cost,
)
from bedrock.models.financial_models import (
    DEFAULT_HOME_SIZE_SQFT,
    calculate_cost_per_home_from_sqft,
    calculate_cost_per_sqft,
    calculate_estimated_home_size_sqft,
    calculate_profit,
    calculate_revenue,
    calculate_roi,
)


@dataclass(frozen=True)
class FeasibilityFinancialOutputs:
    """Canonical deterministic feasibility outputs and supporting metrics."""

    units: int
    estimated_home_price: float
    price_per_sqft: float
    estimated_home_size_sqft: float
    construction_cost_per_sqft: float
    construction_cost_per_home: float
    development_cost_total: float
    projected_revenue: float
    projected_cost: float
    projected_profit: float
    roi: Optional[float]
    land_cost: float
    soft_costs: float
    flood_cost_adjustment: float
    carry_cost: float
    cost_breakdown: CostBreakdown


class MarketPriceEstimator:
    """Deterministic market price estimator with pluggable data source multipliers."""

    def __init__(self, regional_price_multipliers: Optional[Mapping[str, float]] = None) -> None:
        normalized: dict[str, float] = {}
        for key, value in (regional_price_multipliers or {}).items():
            normalized[key.strip().lower()] = float(value)
        self._regional_price_multipliers = normalized

    def estimate(self, *, parcel: Parcel, market_data: MarketData) -> float:
        jurisdiction = (parcel.jurisdiction or "").strip().lower()
        multiplier = self._regional_price_multipliers.get(jurisdiction, 1.0)
        return float(market_data.estimated_home_price) * float(multiplier)


class ConstructionCostModel:
    """Deterministic per-home construction cost model with regional adjustment."""

    def estimate_per_home(self, *, parcel: Parcel, layout: LayoutResult, market_data: MarketData) -> float:
        estimated_home_size_sqft = calculate_estimated_home_size_sqft(
            parcel_area_sqft=float(parcel.area_sqft),
            units=max(1, int(layout.unit_count)),
        )
        baseline_cost_per_sqft = calculate_cost_per_sqft(
            baseline_cost_per_home=float(market_data.construction_cost_per_home),
            reference_home_size_sqft=DEFAULT_HOME_SIZE_SQFT,
        )
        return calculate_cost_per_home_from_sqft(
            home_size_sqft=estimated_home_size_sqft,
            cost_per_sqft=baseline_cost_per_sqft,
            regional_factor=1.0,
        )


class DevelopmentCostModel:
    """Deterministic site-development model covering roads, utilities, grading, and permitting."""

    def estimate(
        self,
        *,
        units: int,
        parcel: Parcel,
        layout: LayoutResult,
        market_data: MarketData,
    ) -> CostBreakdown:
        if units <= 0:
            return CostBreakdown(
                adjusted_cost_per_home=0.0,
                construction_cost=0.0,
                roads_cost=0.0,
                utilities_cost=0.0,
                grading_cost=0.0,
                sitework_cost=0.0,
                permitting_cost=0.0,
                development_cost=0.0,
                land_cost=calculate_land_cost(land_price=market_data.land_price),
                total_cost=0.0,
            )
        roads_cost = calculate_development_cost(
            road_length=float(layout.road_length_ft),
            road_cost_per_ft=float(market_data.road_cost_per_ft),
        )
        # Proxy: when the layout engine doesn't compute utility routing
        # (utility_length_ft defaults to 0.0), assume utilities follow road corridors.
        effective_utility_length = float(layout.utility_length_ft) or float(layout.road_length_ft)
        utilities_cost = calculate_utilities_cost(
            utility_length=effective_utility_length,
            road_cost_per_ft=float(market_data.road_cost_per_ft),
        )
        from bedrock.models.slope_models import assess_slope

        grading_factor = assess_slope(slope_percent=parcel.slope_percent)
        grading_cost = calculate_grading_cost(roads_cost=roads_cost, utilities_cost=utilities_cost, grading_factor=grading_factor)
        from bedrock.models.impact_fee_models import calculate_impact_fees

        permitting_cost = calculate_impact_fees(units=units, jurisdiction=parcel.jurisdiction)
        sitework_cost = calculate_sitework_cost(parcel_area_sqft=float(parcel.area_sqft))
        development_cost = roads_cost + utilities_cost + grading_cost + permitting_cost + sitework_cost
        land_cost = calculate_land_cost(land_price=market_data.land_price)
        return CostBreakdown(
            adjusted_cost_per_home=0.0,
            construction_cost=0.0,
            roads_cost=roads_cost,
            utilities_cost=utilities_cost,
            grading_cost=grading_cost,
            sitework_cost=sitework_cost,
            permitting_cost=permitting_cost,
            development_cost=development_cost,
            land_cost=land_cost,
            total_cost=0.0,
        )


class RiskScoringModel:
    """Deterministic risk scoring based on zoning certainty, confidence, cost variability, and layout shape."""

    def score(
        self,
        *,
        parcel: Parcel,
        layout: LayoutResult,
        market_data: MarketData,
        projected_cost: float,
        development_cost_total: float,
        roi: Optional[float],
    ) -> float:
        zoning_reliability_risk = 0.05 if parcel.zoning_district else 0.22
        parcel_area_acres = float(parcel.area_sqft) / 43560.0 if float(parcel.area_sqft) > 0 else 0.0
        units_per_acre = 0.0 if parcel_area_acres == 0 else float(layout.unit_count) / parcel_area_acres
        density_risk = min(0.25, max(0.0, (units_per_acre - 4.0) / 20.0))
        # soft_cost_factor is now applied as real cost in projected_cost — using it
        # here as well would double-count.  Use a small fixed component instead.
        market_volatility_risk = 0.05
        development_cost_ratio = 0.0 if projected_cost == 0 else float(development_cost_total) / float(projected_cost)
        cost_model_variance_risk = min(0.2, development_cost_ratio * 0.35)

        if roi is None:
            roi_risk = 0.3
        elif roi < 0:
            roi_risk = 0.4
        else:
            roi_risk = max(0.05, 0.3 - min(roi, 1.0) * 0.25)

        total_risk = (
            0.03
            + zoning_reliability_risk
            + density_risk
            + market_volatility_risk
            + cost_model_variance_risk
            + roi_risk
        )
        return min(max(total_risk, 0.0), 1.0)


class FeasibilityEngine:
    """Composition root for deterministic feasibility calculations."""

    def __init__(
        self,
        *,
        price_estimator: Optional[MarketPriceEstimator] = None,
        construction_cost_model: Optional[ConstructionCostModel] = None,
        development_cost_model: Optional[DevelopmentCostModel] = None,
    ) -> None:
        self.price_estimator = price_estimator or MarketPriceEstimator()
        self.construction_cost_model = construction_cost_model or ConstructionCostModel()
        self.development_cost_model = development_cost_model or DevelopmentCostModel()

    def compute(
        self,
        *,
        parcel: Parcel,
        layout: LayoutResult,
        market_data: MarketData,
        market_context: Optional[dict] = None,
    ) -> FeasibilityFinancialOutputs:
        units = int(layout.unit_count)
        estimated_home_price = self.price_estimator.estimate(parcel=parcel, market_data=market_data)
        estimated_home_size_sqft = calculate_estimated_home_size_sqft(
            parcel_area_sqft=float(parcel.area_sqft),
            units=max(units, 1),
        )
        price_per_sqft = float(estimated_home_price) / max(estimated_home_size_sqft, 1.0)
        if market_context and market_context.get("construction_cost_per_sqft") is not None:
            construction_cost_per_sqft = float(market_context["construction_cost_per_sqft"])
        else:
            construction_cost_per_sqft = calculate_cost_per_sqft(
                baseline_cost_per_home=float(market_data.construction_cost_per_home),
                reference_home_size_sqft=DEFAULT_HOME_SIZE_SQFT,
            )
        construction_cost_per_home = self.construction_cost_model.estimate_per_home(
            parcel=parcel,
            layout=layout,
            market_data=market_data,
        )
        development = self.development_cost_model.estimate(
            units=units,
            parcel=parcel,
            layout=layout,
            market_data=market_data,
        )

        projected_revenue = calculate_revenue(units=units, estimated_home_price=estimated_home_price)
        construction_cost = calculate_construction_cost(units=units, cost_per_home=construction_cost_per_home)
        soft_cost_factor = float(market_data.soft_cost_factor or 0.0)
        soft_costs = calculate_soft_costs(
            construction_cost=construction_cost,
            development_cost=development.development_cost,
            soft_cost_factor=soft_cost_factor,
        )
        # Flood cost: if a multiplier is provided, apply it to development cost.
        # Also accept a pre-computed flood_cost_adjustment for backward compat.
        if market_context and market_context.get("flood_cost_multiplier") is not None:
            _multiplier = float(market_context["flood_cost_multiplier"])
            flood_cost_adjustment = development.development_cost * max(_multiplier - 1.0, 0.0)
        elif market_context:
            flood_cost_adjustment = float(market_context.get("flood_cost_adjustment", 0.0))
        else:
            flood_cost_adjustment = 0.0
        base_cost = (
            calculate_total_cost(
                construction_cost=construction_cost,
                development_cost=development.development_cost,
                land_cost=development.land_cost,
            )
            + soft_costs
            + flood_cost_adjustment
        )
        # Carry cost: opt-in via market_context
        carry_cost = 0.0
        if market_context and market_context.get("include_carry_cost", False):
            from bedrock.models.carry_cost_models import calculate_carry_cost, resolve_absorption_rate, resolve_interest_rate

            absorption = float(market_context.get("absorption_rate_per_month", 0)) or resolve_absorption_rate(jurisdiction=parcel.jurisdiction)
            interest = float(market_context.get("interest_rate", 0)) or resolve_interest_rate()
            carry = calculate_carry_cost(
                units=units,
                total_capital=base_cost,
                absorption_rate=absorption,
                interest_rate=interest,
            )
            carry_cost = carry.carry_cost

        projected_cost = base_cost + carry_cost
        projected_profit = calculate_profit(revenue=projected_revenue, total_cost=projected_cost)
        roi = calculate_roi(profit=projected_profit, total_cost=projected_cost)

        cost_breakdown = development.model_copy(
            update={
                "adjusted_cost_per_home": construction_cost_per_home,
                "construction_cost": construction_cost,
                "total_cost": projected_cost,
            }
        )
        return FeasibilityFinancialOutputs(
            units=units,
            estimated_home_price=estimated_home_price,
            price_per_sqft=price_per_sqft,
            estimated_home_size_sqft=estimated_home_size_sqft,
            construction_cost_per_sqft=construction_cost_per_sqft,
            construction_cost_per_home=construction_cost_per_home,
            development_cost_total=development.development_cost,
            projected_revenue=projected_revenue,
            projected_cost=projected_cost,
            projected_profit=projected_profit,
            roi=roi,
            land_cost=development.land_cost,
            soft_costs=soft_costs,
            flood_cost_adjustment=flood_cost_adjustment,
            carry_cost=carry_cost,
            cost_breakdown=cost_breakdown,
        )
