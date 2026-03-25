"""Canonical market data contract for feasibility evaluation."""

from __future__ import annotations

from typing import Optional

from pydantic import AliasChoices, Field

from .base import BedrockModel


class MarketData(BedrockModel):
    """Deterministic market assumptions consumed by the feasibility layer."""

    estimated_home_price: float = Field(ge=0)
    construction_cost_per_home: float = Field(
        ge=0,
        validation_alias=AliasChoices("construction_cost_per_home", "cost_per_home"),
    )
    road_cost_per_ft: float = Field(ge=0)
    land_price: Optional[float] = Field(default=None, ge=0)
    soft_cost_factor: Optional[float] = Field(default=None, ge=0)

    @property
    def cost_per_home(self) -> float:
        return self.construction_cost_per_home
