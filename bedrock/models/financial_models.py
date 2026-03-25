"""Reusable financial models for feasibility evaluation."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from bedrock.contracts.base import BedrockModel


class FinancialMetrics(BedrockModel):
    units: int = Field(ge=0)
    estimated_home_price: float = Field(ge=0)
    construction_cost_per_home: float = Field(ge=0)
    road_cost_per_ft: float = Field(ge=0)
    road_length: float = Field(ge=0)
    land_cost: float = Field(default=0.0, ge=0)
    projected_revenue: float = Field(ge=0)
    development_cost_total: float = Field(ge=0)
    projected_cost: float = Field(ge=0)
    projected_profit: float
    ROI: Optional[float] = None

    @property
    def revenue(self) -> float:
        return self.projected_revenue

    @property
    def construction_cost(self) -> float:
        return float(self.units) * self.construction_cost_per_home

    @property
    def development_cost(self) -> float:
        return self.development_cost_total

    @property
    def total_cost(self) -> float:
        return self.projected_cost

    @property
    def profit(self) -> float:
        return self.projected_profit

    @property
    def roi(self) -> Optional[float]:
        return self.ROI


DEFAULT_HOME_SIZE_SQFT = 2000.0
MIN_HOME_SIZE_SQFT = 1200.0
MAX_HOME_SIZE_SQFT = 3200.0


def calculate_estimated_home_size_sqft(*, parcel_area_sqft: float, units: int) -> float:
    if int(units) <= 0:
        return DEFAULT_HOME_SIZE_SQFT
    area_per_unit = float(parcel_area_sqft) / float(units)
    heuristic_size = area_per_unit * 0.22
    return min(MAX_HOME_SIZE_SQFT, max(MIN_HOME_SIZE_SQFT, heuristic_size))


def calculate_cost_per_sqft(*, baseline_cost_per_home: float, reference_home_size_sqft: float = DEFAULT_HOME_SIZE_SQFT) -> float:
    if float(reference_home_size_sqft) <= 0.0:
        raise ValueError("reference_home_size_sqft must be greater than zero")
    return float(baseline_cost_per_home) / float(reference_home_size_sqft)


def calculate_cost_per_home_from_sqft(*, home_size_sqft: float, cost_per_sqft: float, regional_factor: float = 1.0) -> float:
    return float(home_size_sqft) * float(cost_per_sqft) * float(regional_factor)


def calculate_revenue(*, units: int, estimated_home_price: float) -> float:
    return float(units) * float(estimated_home_price)


def calculate_profit(*, revenue: float, total_cost: float) -> float:
    return float(revenue) - float(total_cost)


def calculate_roi(*, profit: float, total_cost: float) -> Optional[float]:
    if float(total_cost) == 0.0:
        return None
    return float(profit) / float(total_cost)
