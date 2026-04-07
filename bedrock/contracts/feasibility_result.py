"""Canonical feasibility contracts for layout evaluation."""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from pydantic import AliasChoices, Field, model_validator

from .base import BedrockModel, EngineMetadata


class FeasibilityScenario(BedrockModel):
    """Structured evaluation request built from parcel and zoning context."""

    scenario_id: str
    parcel_id: str
    requested_units: int = Field(ge=0)
    assumptions: Dict[str, Any] = Field(default_factory=dict)
    constraints: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Optional[EngineMetadata] = None


class CostBreakdown(BedrockModel):
    construction: float = Field(ge=0)
    development: float = Field(ge=0)


class RevenueBreakdown(BedrockModel):
    units: int = Field(ge=0)
    price_per_home: float = Field(ge=0)


class FeasibilityExplanation(BedrockModel):
    primary_driver: str
    cost_breakdown: CostBreakdown
    revenue_breakdown: RevenueBreakdown


class FeasibilityResult(BedrockModel):
    """Authoritative output contract for the feasibility stage."""

    schema_name: str = Field(default="FeasibilityResult", frozen=True)
    schema_version: str = Field(default="1.0.0", frozen=True)
    scenario_id: str
    layout_id: str
    parcel_id: str
    units: int = Field(ge=0, validation_alias=AliasChoices("units", "feasible_units", "max_units"))
    estimated_home_price: float = Field(default=0.0, ge=0)
    price_per_sqft: float = Field(default=0.0, ge=0)
    estimated_home_size_sqft: float = Field(default=0.0, ge=0)
    construction_cost_per_sqft: float = Field(default=0.0, ge=0)
    construction_cost_per_home: float = Field(
        default=0.0,
        ge=0,
        validation_alias=AliasChoices("construction_cost_per_home", "cost_per_home"),
    )
    development_cost_total: float = Field(
        default=0.0,
        ge=0,
        validation_alias=AliasChoices("development_cost_total", "development_cost"),
    )
    projected_revenue: float = Field(
        default=0.0,
        ge=0,
        validation_alias=AliasChoices("projected_revenue", "revenue"),
    )
    projected_cost: float = Field(
        default=0.0,
        ge=0,
        validation_alias=AliasChoices("projected_cost", "total_cost"),
    )
    projected_profit: float = Field(
        default=0.0,
        validation_alias=AliasChoices("projected_profit", "profit"),
    )
    ROI: Optional[float] = Field(default=None, validation_alias=AliasChoices("ROI", "roi"))
    ROI_base: Optional[float] = None
    ROI_best_case: Optional[float] = None
    ROI_worst_case: Optional[float] = None
    break_even_price: Optional[float] = Field(default=None, ge=0)
    profit_margin: Optional[float] = None
    revenue_per_unit: float = Field(default=0.0, ge=0)
    cost_per_unit: float = Field(default=0.0, ge=0)
    rank: Optional[int] = Field(default=None, ge=1)
    risk_score: float = Field(ge=0, le=1)
    requested_units: Optional[int] = Field(default=None, ge=0)
    constraint_violations: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    confidence_score: Optional[float] = Field(default=None, ge=0, le=1)
    key_risk_factors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    status: str = "unknown"
    financial_summary: Dict[str, Any] = Field(default_factory=dict)
    explanation: Optional[FeasibilityExplanation] = None
    assumptions: Dict[str, Any] = Field(default_factory=dict)
    metadata: Optional[EngineMetadata] = None
    CORE_CALCULATION_FIELDS: ClassVar[tuple[str, ...]] = (
        "scenario_id",
        "parcel_id",
        "layout_id",
        "units",
        "estimated_home_price",
        "price_per_sqft",
        "estimated_home_size_sqft",
        "construction_cost_per_sqft",
        "construction_cost_per_home",
        "development_cost_total",
        "projected_revenue",
        "projected_cost",
        "projected_profit",
        "ROI",
        "ROI_base",
        "ROI_best_case",
        "ROI_worst_case",
        "break_even_price",
        "profit_margin",
        "revenue_per_unit",
        "cost_per_unit",
        "risk_score",
        "confidence",
        "confidence_score",
        "key_risk_factors",
        "status",
        "constraint_violations",
        "financial_summary",
        "explanation",
        "assumptions",
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_status(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        financial_summary = normalized.get("financial_summary") or {}
        for source_key, target_key in (
            ("estimated_home_price", "estimated_home_price"),
            ("price_per_sqft", "price_per_sqft"),
            ("estimated_home_size_sqft", "estimated_home_size_sqft"),
            ("construction_cost_per_sqft", "construction_cost_per_sqft"),
            ("construction_cost_per_home", "construction_cost_per_home"),
            ("development_cost_total", "development_cost_total"),
            ("projected_revenue", "projected_revenue"),
            ("projected_cost", "projected_cost"),
            ("projected_profit", "projected_profit"),
            ("ROI", "ROI"),
            ("roi", "ROI"),
            ("ROI_base", "ROI_base"),
            ("ROI_best_case", "ROI_best_case"),
            ("ROI_worst_case", "ROI_worst_case"),
            ("break_even_price", "break_even_price"),
            ("profit_margin", "profit_margin"),
            ("revenue_per_unit", "revenue_per_unit"),
            ("cost_per_unit", "cost_per_unit"),
            ("confidence_score", "confidence_score"),
        ):
            if target_key not in normalized and source_key in financial_summary:
                normalized[target_key] = financial_summary[source_key]
        if normalized.get("confidence_score") is None and normalized.get("confidence") is not None:
            normalized["confidence_score"] = normalized.get("confidence")
        if normalized.get("status") in {None, "unknown"}:
            violations = normalized.get("constraint_violations") or []
            normalized["status"] = "constrained" if violations else "feasible"
        return normalized

    @model_validator(mode="after")
    def _sync_financial_summary(self) -> "FeasibilityResult":
        summary = dict(self.financial_summary)
        summary.setdefault("estimated_home_price", self.estimated_home_price)
        summary.setdefault("price_per_sqft", self.price_per_sqft)
        summary.setdefault("estimated_home_size_sqft", self.estimated_home_size_sqft)
        summary.setdefault("construction_cost_per_sqft", self.construction_cost_per_sqft)
        summary.setdefault("construction_cost_per_home", self.construction_cost_per_home)
        summary.setdefault("development_cost_total", self.development_cost_total)
        summary.setdefault("projected_revenue", self.projected_revenue)
        summary.setdefault("projected_cost", self.projected_cost)
        summary.setdefault("projected_profit", self.projected_profit)
        summary.setdefault("ROI", self.ROI)
        summary.setdefault("ROI_base", self.ROI_base)
        summary.setdefault("ROI_best_case", self.ROI_best_case)
        summary.setdefault("ROI_worst_case", self.ROI_worst_case)
        summary.setdefault("break_even_price", self.break_even_price)
        summary.setdefault("profit_margin", self.profit_margin)
        summary.setdefault("revenue_per_unit", self.revenue_per_unit)
        summary.setdefault("cost_per_unit", self.cost_per_unit)
        summary.setdefault("confidence_score", self.confidence_score if self.confidence_score is not None else self.confidence)
        summary.setdefault("key_risk_factors", list(self.key_risk_factors))
        object.__setattr__(self, "financial_summary", summary)
        return self

    @property
    def feasible_units(self) -> int:
        return self.units

    @property
    def max_units(self) -> int:
        return self.units

    @property
    def roi(self) -> Optional[float]:
        return self.ROI

    def core_calculation_view(self) -> Dict[str, Any]:
        """Return deterministic financial fields without runtime metadata timestamps."""

        payload = self.model_dump(mode="python")
        return {key: payload.get(key) for key in self.CORE_CALCULATION_FIELDS}

    def metadata_view(self) -> Optional[Dict[str, Any]]:
        """Return runtime metadata isolated from core financial outputs."""

        if self.metadata is None:
            return None
        return self.metadata.model_dump(mode="python")
