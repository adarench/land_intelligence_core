"""Calibration hooks for comparing predicted feasibility outputs to actual deal outcomes."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from bedrock.contracts.base import BedrockModel
from bedrock.contracts.feasibility_result import FeasibilityResult
from bedrock.contracts.feasibility_validation import DealOutcomeMetrics, FeasibilityValidationRecord


class CalibrationMetric(BedrockModel):
    predicted: float
    actual: float
    absolute_error: float = Field(ge=0)
    relative_error: Optional[float] = Field(default=None, ge=0)


class FeasibilityCalibrationReport(BedrockModel):
    record_id: Optional[str] = None
    parcel_id: str
    layout_id: str
    scenario_id: Optional[str] = None
    sale_price: CalibrationMetric
    construction_cost: CalibrationMetric
    development_cost: CalibrationMetric
    ROI: CalibrationMetric
    mean_absolute_percentage_error: Optional[float] = Field(default=None, ge=0)


def _build_metric(*, predicted: float, actual: float) -> CalibrationMetric:
    absolute_error = abs(float(predicted) - float(actual))
    relative_error = None if float(actual) == 0 else absolute_error / abs(float(actual))
    return CalibrationMetric(
        predicted=float(predicted),
        actual=float(actual),
        absolute_error=absolute_error,
        relative_error=relative_error,
    )


def to_predicted_outcome(result: FeasibilityResult) -> DealOutcomeMetrics:
    """Convert FeasibilityResult into the calibration metric vector."""

    construction_cost = float(result.units) * float(result.construction_cost_per_home)
    return DealOutcomeMetrics(
        sale_price=float(result.estimated_home_price),
        construction_cost=construction_cost,
        development_cost=float(result.development_cost_total),
        ROI=0.0 if result.ROI is None else float(result.ROI),
    )


def build_validation_record(
    *,
    result: FeasibilityResult,
    actual: DealOutcomeMetrics,
    record_id: str,
    notes: str | None = None,
) -> FeasibilityValidationRecord:
    """Build a dataset row for predicted vs actual feasibility outcomes."""

    return FeasibilityValidationRecord(
        record_id=record_id,
        parcel_id=result.parcel_id or "unknown",
        layout_id=result.layout_id,
        scenario_id=result.scenario_id,
        predicted=to_predicted_outcome(result),
        actual=actual,
        notes=notes,
    )


def compare_predicted_vs_actual(
    *,
    result: FeasibilityResult,
    actual: DealOutcomeMetrics,
    record_id: str | None = None,
) -> FeasibilityCalibrationReport:
    """Compute calibration deltas for all required real-world comparison fields."""

    predicted = to_predicted_outcome(result)
    sale_price = _build_metric(predicted=predicted.sale_price, actual=actual.sale_price)
    construction_cost = _build_metric(
        predicted=predicted.construction_cost,
        actual=actual.construction_cost,
    )
    development_cost = _build_metric(
        predicted=predicted.development_cost,
        actual=actual.development_cost,
    )
    roi = _build_metric(predicted=predicted.ROI, actual=actual.ROI)

    relative_errors = [
        metric.relative_error
        for metric in (sale_price, construction_cost, development_cost, roi)
        if metric.relative_error is not None
    ]
    mape = None if not relative_errors else sum(relative_errors) / float(len(relative_errors))
    return FeasibilityCalibrationReport(
        record_id=record_id,
        parcel_id=result.parcel_id or "unknown",
        layout_id=result.layout_id,
        scenario_id=result.scenario_id,
        sale_price=sale_price,
        construction_cost=construction_cost,
        development_cost=development_cost,
        ROI=roi,
        mean_absolute_percentage_error=mape,
    )
