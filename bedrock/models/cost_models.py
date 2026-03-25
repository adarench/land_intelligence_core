"""Reusable deterministic cost models for feasibility evaluation."""

from __future__ import annotations

from pydantic import Field

from bedrock.contracts.base import BedrockModel


class CostBreakdown(BedrockModel):
    adjusted_cost_per_home: float = Field(ge=0)
    construction_cost: float = Field(ge=0)
    roads_cost: float = Field(ge=0)
    utilities_cost: float = Field(ge=0)
    grading_cost: float = Field(ge=0)
    sitework_cost: float = Field(ge=0, default=0.0)
    permitting_cost: float = Field(ge=0)
    development_cost: float = Field(ge=0)
    land_cost: float = Field(ge=0)
    total_cost: float = Field(ge=0)


REGIONAL_CONSTRUCTION_MULTIPLIERS: dict[str, float] = {
    "salt lake city": 1.08,
    "salt lake county": 1.06,
    "lehi": 1.05,
    "draper": 1.07,
}
DEFAULT_UTILITY_COST_FACTOR = 0.55
DEFAULT_GRADING_COST_FACTOR = 0.10
DEFAULT_PERMITTING_COST_PER_UNIT = 3500.0
DEFAULT_SITEWORK_COST_PER_ACRE = 18000.0


def adjusted_cost_per_home(*, base_cost_per_home: float, jurisdiction: str | None) -> float:
    if not jurisdiction:
        return float(base_cost_per_home)
    multiplier = REGIONAL_CONSTRUCTION_MULTIPLIERS.get(jurisdiction.strip().lower(), 1.0)
    return float(base_cost_per_home) * multiplier


def calculate_construction_cost(*, units: int, cost_per_home: float) -> float:
    return float(units) * float(cost_per_home)


def calculate_development_cost(*, road_length: float, road_cost_per_ft: float) -> float:
    return float(road_length) * float(road_cost_per_ft)


def calculate_utilities_cost(
    *,
    utility_length: float,
    road_cost_per_ft: float,
    utility_cost_factor: float = DEFAULT_UTILITY_COST_FACTOR,
) -> float:
    return float(utility_length) * float(road_cost_per_ft) * float(utility_cost_factor)


def calculate_grading_cost(
    *,
    roads_cost: float,
    utilities_cost: float,
    grading_factor: float = DEFAULT_GRADING_COST_FACTOR,
) -> float:
    return (float(roads_cost) + float(utilities_cost)) * float(grading_factor)


def calculate_permitting_cost(
    *,
    units: int,
    permitting_cost_per_unit: float = DEFAULT_PERMITTING_COST_PER_UNIT,
) -> float:
    return float(units) * float(permitting_cost_per_unit)


def calculate_land_cost(*, land_price: float | None) -> float:
    return 0.0 if land_price is None else float(land_price)


def calculate_sitework_cost(*, parcel_area_sqft: float, sitework_cost_per_acre: float = DEFAULT_SITEWORK_COST_PER_ACRE) -> float:
    parcel_area_acres = 0.0 if float(parcel_area_sqft) <= 0.0 else float(parcel_area_sqft) / 43560.0
    return parcel_area_acres * float(sitework_cost_per_acre)


def calculate_total_cost(
    *,
    construction_cost: float,
    development_cost: float,
    land_cost: float,
) -> float:
    return float(construction_cost) + float(development_cost) + float(land_cost)


def calculate_costs(
    *,
    units: int,
    road_length: float,
    utility_length: float,
    cost_per_home: float,
    road_cost_per_ft: float,
    land_price: float | None = None,
    jurisdiction: str | None = None,
) -> CostBreakdown:
    per_home_cost = adjusted_cost_per_home(base_cost_per_home=cost_per_home, jurisdiction=jurisdiction)
    construction_cost = calculate_construction_cost(units=units, cost_per_home=per_home_cost)
    roads_cost = calculate_development_cost(road_length=road_length, road_cost_per_ft=road_cost_per_ft)
    utilities_cost = calculate_utilities_cost(
        utility_length=utility_length,
        road_cost_per_ft=road_cost_per_ft,
    )
    grading_cost = calculate_grading_cost(roads_cost=roads_cost, utilities_cost=utilities_cost)
    permitting_cost = calculate_permitting_cost(units=units)
    development_cost = roads_cost + utilities_cost + grading_cost + permitting_cost
    land_cost = calculate_land_cost(land_price=land_price)
    total_cost = calculate_total_cost(
        construction_cost=construction_cost,
        development_cost=development_cost,
        land_cost=land_cost,
    )
    return CostBreakdown(
        adjusted_cost_per_home=per_home_cost,
        construction_cost=construction_cost,
        roads_cost=roads_cost,
        utilities_cost=utilities_cost,
        grading_cost=grading_cost,
        sitework_cost=0.0,
        permitting_cost=permitting_cost,
        development_cost=development_cost,
        land_cost=land_cost,
        total_cost=total_cost,
    )
