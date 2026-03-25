"""Contracts for real-world feasibility calibration and validation datasets."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from .base import BedrockModel


class DealOutcomeMetrics(BedrockModel):
    """Canonical metric set used for predicted vs actual feasibility comparison."""

    sale_price: float = Field(ge=0)
    construction_cost: float = Field(ge=0)
    development_cost: float = Field(ge=0)
    ROI: float


class FeasibilityValidationRecord(BedrockModel):
    """One row in a real-world feasibility calibration dataset."""

    record_id: str
    parcel_id: str
    layout_id: str
    scenario_id: Optional[str] = None
    predicted: DealOutcomeMetrics
    actual: DealOutcomeMetrics
    notes: Optional[str] = None
