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
from bedrock.services.market_intelligence_service import MarketIntelligenceService


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
    default_soft_cost_factor = 0.10

    def __init__(
        self,
        *,
        engine: Optional[FeasibilityEngine] = None,
        risk_scoring_model: Optional[RiskScoringModel] = None,
        market_intelligence_service: Optional[MarketIntelligenceService] = None,
    ) -> None:
        self._engine = engine or FeasibilityEngine()
        self._risk_scoring_model = risk_scoring_model or RiskScoringModel()
        self._market_intelligence = market_intelligence_service or MarketIntelligenceService()

    def evaluate(
        self,
        *,
        parcel: Parcel,
        layout: SubdivisionLayout,
        market_data: Optional[MarketData] = None,
        zoning_metadata: Optional[dict] = None,
        include_runtime_metadata: bool = False,
    ) -> FeasibilityResult:
        effective_market_data, market_context = self.resolve_market_data(parcel=parcel, layout=layout, market_data=market_data)
        # Merge zoning source metadata so _evaluation_grade can route correctly.
        # source_type and legal_reliability live on the zoning result, not market data,
        # so they must be explicitly threaded in here.
        if zoning_metadata:
            if zoning_metadata.get("source_type") and not market_context.get("source_type"):
                market_context["source_type"] = zoning_metadata["source_type"]
            if zoning_metadata.get("legal_reliability") is not None:
                market_context["legal_reliability"] = bool(zoning_metadata["legal_reliability"])
        outputs = self._engine.compute(
            parcel=parcel,
            layout=layout,
            market_data=effective_market_data,
            market_context=market_context,
        )
        sensitivity = self._sensitivity_analysis(
            units=outputs.units,
            estimated_home_price=outputs.estimated_home_price,
            projected_cost=outputs.projected_cost,
            construction_cost_total=outputs.cost_breakdown.construction_cost,
            development_cost_total=outputs.development_cost_total,
        )
        key_risk_factors = self._key_risk_factors(
            roi=outputs.roi,
            market_context=market_context,
            parcel=parcel,
            layout=layout,
        )
        confidence_breakdown = self._confidence_breakdown(
            market_context=market_context,
            key_risk_factors=key_risk_factors,
            roi=outputs.roi,
        )
        confidence_score = confidence_breakdown["composite"]

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
            price_per_sqft=outputs.price_per_sqft,
            estimated_home_size_sqft=outputs.estimated_home_size_sqft,
            construction_cost_per_sqft=outputs.construction_cost_per_sqft,
            construction_cost_per_home=financials.construction_cost_per_home,
            development_cost_total=financials.development_cost_total,
            projected_revenue=financials.projected_revenue,
            projected_cost=financials.projected_cost,
            projected_profit=financials.projected_profit,
            ROI=financials.ROI,
            ROI_base=sensitivity["ROI_base"],
            ROI_best_case=sensitivity["ROI_best_case"],
            ROI_worst_case=sensitivity["ROI_worst_case"],
            break_even_price=sensitivity["break_even_price"],
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
            confidence=confidence_score,
            confidence_score=confidence_score,
            key_risk_factors=key_risk_factors,
            financial_summary={
                **financials.model_dump(),
                "price_per_sqft": outputs.price_per_sqft,
                "estimated_home_size_sqft": outputs.estimated_home_size_sqft,
                "construction_cost_per_sqft": outputs.construction_cost_per_sqft,
                **sensitivity,
                "confidence_score": confidence_score,
                "confidence_breakdown": confidence_breakdown,
                "key_risk_factors": key_risk_factors,
                "market_context": market_context,
                "development_cost_breakdown": {
                    "roads": outputs.cost_breakdown.roads_cost,
                    "utilities": outputs.cost_breakdown.utilities_cost,
                    "grading": outputs.cost_breakdown.grading_cost,
                    "sitework": outputs.cost_breakdown.sitework_cost,
                    "permitting": outputs.cost_breakdown.permitting_cost,
                },
                "evaluation_grade": self._evaluation_grade(market_context, key_risk_factors, confidence_score),
            },
            explanation=self._build_explanation(financials),
            assumptions={**self._assumption_flags(effective_market_data), "market_sources": market_context.get("sources", {})},
            metadata=runtime_metadata,
        )

    def evaluate_layouts(
        self,
        parcel: Parcel,
        layouts: Iterable[SubdivisionLayout],
        market_data: Optional[MarketData] = None,
        zoning_metadata: Optional[dict] = None,
    ) -> list[FeasibilityResult]:
        results = [
            self.evaluate(parcel=parcel, layout=layout, market_data=market_data, zoning_metadata=zoning_metadata)
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
    def resolve_market_data(cls, *, parcel: Parcel, layout: SubdivisionLayout, market_data: Optional[MarketData]) -> tuple[MarketData, dict]:
        service = MarketIntelligenceService()
        if market_data is None:
            return service.resolve_market_data(parcel=parcel, units=max(int(layout.unit_count), 1))
        normalized = MarketData(
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
        market_context = {
            "pricing_proxy": "user_override",
            "cost_proxy": "user_override",
            "sources": {},
            "used_county_fallback": False,
        }
        return normalized, market_context

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

    @staticmethod
    def _sensitivity_analysis(
        *,
        units: int,
        estimated_home_price: float,
        projected_cost: float,
        construction_cost_total: float,
        development_cost_total: float,
    ) -> dict[str, float | None]:
        if units <= 0:
            return {
                "ROI_base": None,
                "ROI_best_case": None,
                "ROI_worst_case": None,
                "break_even_price": None,
            }
        revenue_base = float(units) * float(estimated_home_price)
        fixed_cost = max(float(projected_cost) - float(construction_cost_total) - float(development_cost_total), 0.0)
        best_revenue = revenue_base * 1.10
        worst_revenue = revenue_base * 0.90
        best_cost = fixed_cost + (construction_cost_total * 0.92) + (development_cost_total * 0.92)
        worst_cost = fixed_cost + (construction_cost_total * 1.08) + (development_cost_total * 1.10)
        roi_base = None if projected_cost == 0 else (revenue_base - projected_cost) / projected_cost
        roi_best = None if best_cost == 0 else (best_revenue - best_cost) / best_cost
        roi_worst = None if worst_cost == 0 else (worst_revenue - worst_cost) / worst_cost
        break_even_price = projected_cost / float(units)
        return {
            "ROI_base": roi_base,
            "ROI_best_case": roi_best,
            "ROI_worst_case": roi_worst,
            "break_even_price": break_even_price,
        }

    @staticmethod
    def _key_risk_factors(*, roi: float | None, market_context: dict, parcel: Parcel, layout: SubdivisionLayout) -> list[str]:
        risks: list[str] = []
        if market_context.get("used_county_fallback"):
            risks.append("county_level_pricing_proxy")
        if float(market_context.get("rpp_all_items", 100.0)) > 100.0:
            risks.append("above_national_cost_region")
        if roi is None or roi < 0:
            risks.append("negative_or_unknown_base_roi")
        road_per_unit = float(layout.road_length_ft) / max(int(layout.unit_count), 1)
        if road_per_unit > 180.0:
            risks.append("high_road_length_per_unit")
        if float(parcel.area_sqft) / max(int(layout.unit_count), 1) < 5000.0:
            risks.append("small_area_per_unit")
        if int(layout.unit_count) < 3:
            risks.append("below_subdivision_threshold")
        if roi is not None and roi > 0.80:
            risks.append("unusually_high_roi")
        median = float(market_context.get("median_home_value", 0) or 0)
        estimated = float(market_context.get("estimated_home_price", 0) or 0) if "estimated_home_price" in market_context else 0
        if median > 0 and estimated > median * 1.5:
            risks.append("estimated_price_exceeds_market")
        return risks

    @staticmethod
    def _confidence_score(*, market_context: dict, key_risk_factors: list[str], roi: float | None) -> float:
        breakdown = FeasibilityService._confidence_breakdown(
            market_context=market_context,
            key_risk_factors=key_risk_factors,
            roi=roi,
        )
        return breakdown["composite"]

    @staticmethod
    def _confidence_breakdown(*, market_context: dict, key_risk_factors: list[str], roi: float | None) -> dict:
        market_data_quality = 1.0
        if market_context.get("used_county_fallback"):
            market_data_quality = 0.7
        elif market_context.get("pricing_proxy") == "default_fallback":
            market_data_quality = 0.5

        zoning_source_quality = 1.0
        source_type = market_context.get("source_type", "")
        if source_type == "inferred":
            zoning_source_quality = 0.6
        elif any("zoning" in r for r in key_risk_factors):
            zoning_source_quality = 0.7

        cost_model_calibration = 1.0
        if any("above_national_cost_region" in r for r in key_risk_factors):
            cost_model_calibration = 0.85
        if any("high_road_length_per_unit" in r for r in key_risk_factors):
            cost_model_calibration -= 0.1

        layout_feasibility = 1.0
        if any("small_area_per_unit" in r for r in key_risk_factors):
            layout_feasibility = 0.8
        if roi is not None and roi < 0:
            layout_feasibility -= 0.15

        base = 0.88
        penalty = 0.0
        if market_data_quality < 1.0:
            penalty += (1.0 - market_data_quality) * 0.15
        if zoning_source_quality < 1.0:
            penalty += (1.0 - zoning_source_quality) * 0.10
        if cost_model_calibration < 1.0:
            penalty += (1.0 - cost_model_calibration) * 0.10
        if layout_feasibility < 1.0:
            penalty += (1.0 - layout_feasibility) * 0.10
        risk_count_penalty = min(len(key_risk_factors), 5) * 0.03
        roi_penalty = 0.05 if roi is None else 0.0

        composite = max(0.35, min(0.95, base - penalty - risk_count_penalty - roi_penalty))

        return {
            "composite": round(composite, 4),
            "market_data_quality": round(market_data_quality, 2),
            "zoning_source_quality": round(zoning_source_quality, 2),
            "cost_model_calibration": round(cost_model_calibration, 2),
            "layout_feasibility": round(layout_feasibility, 2),
        }

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
    def _evaluation_grade(market_context: dict, key_risk_factors: list[str], confidence: float) -> str:
        """Classify the output quality into DECISION_GRADE, EXPLORATORY, or BLOCKED."""
        source_type = market_context.get("source_type", "")
        legal = market_context.get("legal_reliability", False)
        calibration = market_context.get("calibration_source", "none")
        has_internal_price = market_context.get("pricing_proxy", "").startswith("internal")

        if legal and confidence >= 0.7 and "unusually_high_roi" not in key_risk_factors:
            return "DECISION_GRADE"
        if confidence >= 0.5 and source_type in ("real_lookup", "inferred"):
            return "EXPLORATORY"
        return "BLOCKED"

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


def evaluate_near_feasible_upside(
    *,
    parcel: Parcel,
    near_feasible_result: dict,
    zoning_relaxation_share: float = 0.15,
) -> dict:
    service = FeasibilityService()
    limiting = dict(near_feasible_result.get("limiting_constraints") or {})
    summary = dict(near_feasible_result.get("best_attempt_summary") or {})
    current_units = int(summary.get("lot_count") or limiting.get("max_units") or 0)
    if current_units <= 0:
        current_units = 1
    relaxed_units = max(current_units, int(round(current_units * (1.0 + zoning_relaxation_share))))
    road_length_ft = float(summary.get("total_road_ft") or 0.0)
    surrogate_layout = SubdivisionLayout(
        layout_id=f"near-feasible-{parcel.parcel_id}",
        parcel_id=parcel.parcel_id,
        unit_count=relaxed_units,
        road_length_ft=max(road_length_ft, 0.0),
        lot_geometries=[],
        road_geometries=[],
        open_space_area_sqft=0.0,
        utility_length_ft=road_length_ft * 0.75,
        metadata=EngineMetadata(source_engine="bedrock.services.feasibility_service", source_run_id="near_feasible_upside"),
    )
    result = service.evaluate(parcel=parcel, layout=surrogate_layout)
    return {
        "relaxed_units": relaxed_units,
        "projected_profit": result.projected_profit,
        "ROI": result.ROI,
        "break_even_price": result.break_even_price,
        "estimated_home_price": result.estimated_home_price,
        "price_per_sqft": result.price_per_sqft,
        "assumptions": {
            "zoning_relaxation_share": zoning_relaxation_share,
            "utility_length_proxy": surrogate_layout.utility_length_ft,
            "road_length_proxy": surrogate_layout.road_length_ft,
        },
    }
