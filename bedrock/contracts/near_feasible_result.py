"""Actionable output for parcels that are close to feasible but not currently valid."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import BedrockModel


class NearFeasibleResult(BedrockModel):
    schema_name: str = Field(default="NearFeasibleResult", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    status: str = Field(default="near_feasible", frozen=True)
    reason_category: str
    limiting_constraints: dict[str, Any] = Field(default_factory=dict)
    required_relaxation: dict[str, Any] = Field(default_factory=dict)
    best_attempt_summary: dict[str, Any] = Field(default_factory=dict)
    financial_upside: dict[str, Any] = Field(default_factory=dict)
    attempted_strategies: list[str] = Field(default_factory=list)
    attempted_repairs: list[str] = Field(default_factory=list)
