"""Deterministic financial feasibility evaluation service."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from typing import Iterable, Optional

from bedrock.contracts.base import BedrockModel, EngineMetadata
from bedrock.contracts.feasibility import FeasibilityResult
from bedrock.contracts.layout import SubdivisionLayout
from bedrock.contracts.market_data import MarketData
from bedrock.contracts.parcel import Parcel
from bedrock.contracts.scenario_evaluation import ScenarioEvaluation
from bedrock.contracts.feasibility_validation import DealOutcomeMetrics, FeasibilityValidationRecord
from bedrock.models.financial_models import FinancialMetrics
from bedrock.services.feasibility_calibration import FeasibilityCalibrationReport, compare_predicted_vs_actual
from bedrock.services.feasibility_models import FeasibilityEngine, RiskScoringModel


class FeasibilityEvaluationResponse(BedrockModel):
    result: Optional[FeasibilityResult] = None
    results: list[FeasibilityResult]
    scenario_evaluation: Optional[ScenarioEvaluation] = None


class FeasibilityService:
    """Evaluate a layout with a transparent deterministic baseline model."""

    source_engine = "bedrock.services.feasibility_service"
    default_estimated_home_price = 480000.0
    default_construction_cost_per_home = 260000.0
    default_road_cost_per_ft = 300.0
    default_land_price = 0.0
    default_soft_cost_factor = 0.0

    def __init__(
        self,
        *,
        engine: Optional[FeasibilityEngine] = None,
        risk_scoring_model: Optional[RiskScoringModel] = None,
    ) -> None:
        self._engine = engine or FeasibilityEngine()
        self._risk_scoring_model = risk_scoring_model or RiskScoringModel()

    def evaluate(
        self,
        *,
        parcel: Parcel,
        layout: SubdivisionLayout,
        market_data: Optional[MarketData] = None,
        include_runtime_metadata: bool = False,
    ) -> FeasibilityResult:
        effective_market_data = self.resolve_market_data(market_data)
        outputs = self._engine.compute(parcel=parcel, layout=layout, market_data=effective_market_data)

        financials = FinancialMetrics(
            units=outputs.units,
            estimated_home_price=outputs.estimated_home_price,
            construction_cost_per_home=outputs.construction_cost_per_home,
            road_cost_per_ft=effective_market_data.road_cost_per_ft,
            road_length=layout.road_length,
            land_cost=outputs.land_cost,
            projected_revenue=outputs.projected_revenue,
            development_cost_total=outputs.development_cost_total,
            projected_cost=outputs.projected_cost,
            projected_profit=outputs.projected_profit,
            ROI=outputs.roi,
        )
        profit_margin = None if outputs.projected_revenue == 0 else outputs.projected_profit / outputs.projected_revenue
        revenue_per_unit = 0.0 if outputs.units == 0 else outputs.projected_revenue / float(outputs.units)
        cost_per_unit = 0.0 if outputs.units == 0 else outputs.projected_cost / float(outputs.units)

        runtime_metadata = (
            EngineMetadata(source_engine=self.source_engine) if include_runtime_metadata else None
        )

        return FeasibilityResult(
            scenario_id=self._build_scenario_id(parcel=parcel, layout=layout, market_data=effective_market_data),
            parcel_id=parcel.parcel_id,
            layout_id=layout.layout_id,
            units=outputs.units,
            estimated_home_price=financials.estimated_home_price,
            construction_cost_per_home=financials.construction_cost_per_home,
            development_cost_total=financials.development_cost_total,
            projected_revenue=financials.projected_revenue,
            projected_cost=financials.projected_cost,
            projected_profit=financials.projected_profit,
            ROI=financials.ROI,
            profit_margin=profit_margin,
            revenue_per_unit=revenue_per_unit,
            cost_per_unit=cost_per_unit,
            risk_score=self._risk_score(
                parcel=parcel,
                layout=layout,
                market_data=effective_market_data,
                financials=financials,
            ),
            constraint_violations=self._constraint_violations(financials),
            confidence=0.9,
            financial_summary={
                **financials.model_dump(),
                "development_cost_breakdown": {
                    "roads": outputs.cost_breakdown.roads_cost,
                    "utilities": outputs.cost_breakdown.utilities_cost,
                    "grading": outputs.cost_breakdown.grading_cost,
                    "sitework": outputs.cost_breakdown.sitework_cost,
                    "permitting": outputs.cost_breakdown.permitting_cost,
                },
            },
            explanation=self._build_explanation(financials),
            assumptions=self._assumption_flags(market_data),
            metadata=runtime_metadata,
        )

    def evaluate_layouts(
        self,
        parcel: Parcel,
        layouts: Iterable[SubdivisionLayout],
        market_data: Optional[MarketData] = None,
    ) -> list[FeasibilityResult]:
        effective_market_data = self.resolve_market_data(market_data)
        results = [
            self.evaluate(parcel=parcel, layout=layout, market_data=effective_market_data)
            for layout in layouts
        ]
        ranked = sorted(
            results,
            key=lambda result: (
                float("-inf") if result.ROI is None else result.ROI,
                result.projected_profit,
                result.layout_id,
            ),
            reverse=True,
        )
        for index, result in enumerate(ranked, start=1):
            object.__setattr__(result, "rank", index)
        return ranked

    def summarize_scenario(
        self,
        parcel: Parcel,
        layouts: Iterable[SubdivisionLayout],
        market_data: Optional[MarketData] = None,
    ) -> ScenarioEvaluation:
        ranked = self.evaluate_layouts(parcel, layouts, market_data)
        best = ranked[0] if ranked else None
        return ScenarioEvaluation(
            parcel_id=parcel.parcel_id,
            layout_count=len(ranked),
            best_layout_id=best.layout_id if best else None,
            best_roi=best.ROI if best else None,
            best_profit=best.projected_profit if best else None,
            best_units=best.units if best else None,
            layouts_ranked=ranked,
        )

    @staticmethod
    def to_validation_record(
        *,
        result: FeasibilityResult,
        actual: DealOutcomeMetrics,
        record_id: str,
        notes: str | None = None,
    ) -> FeasibilityValidationRecord:
        from bedrock.services.feasibility_calibration import build_validation_record

        return build_validation_record(result=result, actual=actual, record_id=record_id, notes=notes)

    @staticmethod
    def compare_to_actual(
        *,
        result: FeasibilityResult,
        actual: DealOutcomeMetrics,
        record_id: str | None = None,
    ) -> FeasibilityCalibrationReport:
        return compare_predicted_vs_actual(result=result, actual=actual, record_id=record_id)

    @staticmethod
    def _build_scenario_id(
        *,
        parcel: Parcel,
        layout: SubdivisionLayout,
        market_data: MarketData,
    ) -> str:
        fingerprint = (
            f"{parcel.parcel_id}|{layout.layout_id}|{layout.lot_count}|{layout.road_length}|"
            f"{market_data.estimated_home_price}|{market_data.construction_cost_per_home}|"
            f"{market_data.road_cost_per_ft}|{market_data.land_price}|{market_data.soft_cost_factor}"
        )
        return str(uuid5(NAMESPACE_URL, fingerprint))

    @classmethod
    def resolve_market_data(cls, market_data: Optional[MarketData]) -> MarketData:
        if market_data is None:
            return cls.default_market_data()
        return MarketData(
            estimated_home_price=float(market_data.estimated_home_price),
            construction_cost_per_home=float(market_data.construction_cost_per_home),
            road_cost_per_ft=float(market_data.road_cost_per_ft),
            land_price=cls.default_land_price if market_data.land_price is None else float(market_data.land_price),
            soft_cost_factor=(
                cls.default_soft_cost_factor
                if market_data.soft_cost_factor is None
                else float(market_data.soft_cost_factor)
            ),
        )

    @classmethod
    def market_data_from_overrides(
        cls,
        *,
        estimated_home_price: Optional[float] = None,
        construction_cost_per_home: Optional[float] = None,
        road_cost_per_ft: Optional[float] = None,
        land_price: Optional[float] = None,
        soft_cost_factor: Optional[float] = None,
    ) -> MarketData:
        return MarketData(
            estimated_home_price=(
                cls.default_estimated_home_price
                if estimated_home_price is None
                else float(estimated_home_price)
            ),
            construction_cost_per_home=(
                cls.default_construction_cost_per_home
                if construction_cost_per_home is None
                else float(construction_cost_per_home)
            ),
            road_cost_per_ft=cls.default_road_cost_per_ft if road_cost_per_ft is None else float(road_cost_per_ft),
            land_price=cls.default_land_price if land_price is None else float(land_price),
            soft_cost_factor=(
                cls.default_soft_cost_factor if soft_cost_factor is None else float(soft_cost_factor)
            ),
        )

    @staticmethod
    def _constraint_violations(financials: FinancialMetrics) -> list[str]:
        violations: list[str] = []
        if financials.units == 0:
            violations.append("layout_has_no_units")
        if financials.projected_profit < 0:
            violations.append("projected_profit_negative")
        if financials.projected_cost == 0:
            violations.append("projected_total_cost_zero")
        return violations

    def _risk_score(
        self,
        *,
        parcel: Parcel,
        layout: SubdivisionLayout,
        market_data: MarketData,
        financials: FinancialMetrics,
    ) -> float:
        return self._risk_scoring_model.score(
            parcel=parcel,
            layout=layout,
            market_data=market_data,
            projected_cost=financials.projected_cost,
            development_cost_total=financials.development_cost_total,
            roi=financials.ROI,
        )

    @staticmethod
    def _build_explanation(financials: FinancialMetrics) -> dict:
        drivers = {
            "home_price": financials.estimated_home_price,
            "unit_count": financials.units,
            "road_cost": financials.development_cost_total,
        }
        primary_driver = max(drivers.items(), key=lambda item: item[1])[0]
        return {
            "primary_driver": primary_driver,
            "cost_breakdown": {
                "construction": financials.construction_cost,
                "development": financials.development_cost_total,
            },
            "revenue_breakdown": {
                "units": financials.units,
                "price_per_home": financials.estimated_home_price,
            },
        }

    @classmethod
    def default_market_data(cls) -> MarketData:
        return MarketData(
            estimated_home_price=cls.default_estimated_home_price,
            construction_cost_per_home=cls.default_construction_cost_per_home,
            road_cost_per_ft=cls.default_road_cost_per_ft,
            land_price=cls.default_land_price,
            soft_cost_factor=cls.default_soft_cost_factor,
        )

    @classmethod
    def _assumption_flags(cls, market_data: Optional[MarketData]) -> dict[str, bool]:
        if market_data is None:
            return {
                "used_default_estimated_home_price": True,
                "used_default_construction_cost_per_home": True,
                "used_default_road_cost_per_ft": True,
                "used_default_land_price": True,
                "used_default_soft_cost_factor": True,
            }
        return {
            "used_default_estimated_home_price": (
                float(market_data.estimated_home_price) == cls.default_estimated_home_price
            ),
            "used_default_construction_cost_per_home": (
                float(market_data.construction_cost_per_home) == cls.default_construction_cost_per_home
            ),
            "used_default_road_cost_per_ft": float(market_data.road_cost_per_ft) == cls.default_road_cost_per_ft,
            "used_default_land_price": (
                market_data.land_price is None or float(market_data.land_price) == cls.default_land_price
            ),
            "used_default_soft_cost_factor": (
                market_data.soft_cost_factor is None
                or float(market_data.soft_cost_factor) == cls.default_soft_cost_factor
            ),
        }


def evaluate_layouts(
    parcel: Parcel,
    layouts: Iterable[SubdivisionLayout],
    market_data: MarketData,
) -> list[FeasibilityResult]:
    """Pipeline-oriented helper for ranking feasibility across layout candidates."""

    return FeasibilityService().evaluate_layouts(parcel, layouts, market_data)


def evaluate_scenario(
    parcel: Parcel,
    layouts: Iterable[SubdivisionLayout],
    market_data: MarketData,
) -> ScenarioEvaluation:
    """Pipeline-oriented helper for a ranked scenario summary."""

    return FeasibilityService().summarize_scenario(parcel, layouts, market_data)
