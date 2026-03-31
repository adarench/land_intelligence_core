"""Validation helpers for canonical Bedrock contracts."""

from __future__ import annotations

from typing import Any, Iterable, List, Sequence

from pydantic import ValidationError

from .experiment_run import ExperimentRun
from .feasibility_result import FeasibilityResult
from .layout_result import LayoutResult
from .layout_candidate_batch import LayoutCandidateBatch
from .optimization_run import OptimizationRun
from .parcel import Parcel
from .parcel import SUPPORTED_PARCEL_CRS
from .pipeline_execution_result import PipelineExecutionResult
from .pipeline_run import PipelineRun
from .schema_registry import get_canonical_serialization_fields, get_schema_model, get_service_validation_rule
from .zoning_rules import DevelopmentStandard, ZoningDistrict, ZoningRules


def validate_contract(schema_name: str, payload: Any):
    """Validate a payload or model instance against a registered schema."""

    model = get_schema_model(schema_name)
    if isinstance(payload, model):
        return model.model_validate(payload.model_dump())
    if hasattr(payload, "model_dump"):
        return model.model_validate(payload.model_dump())
    return model.model_validate(payload)


def validate_contract_collection(schema_name: str, payloads: Iterable[Any]) -> List[Any]:
    return [validate_contract(schema_name, item) for item in payloads]


def build_zoning_rules(
    parcel_id: str,
    zoning: ZoningDistrict | dict[str, Any],
    standards: Sequence[DevelopmentStandard | dict[str, Any]],
    *,
    jurisdiction: str | None = None,
) -> ZoningRules:
    zoning_payload = zoning.model_dump() if hasattr(zoning, "model_dump") else dict(zoning)
    standards_payload = [
        item.model_dump() if hasattr(item, "model_dump") else dict(item)
        for item in standards
    ]
    return ZoningRules.model_validate(
        {
            "parcel_id": parcel_id,
            "jurisdiction": jurisdiction,
            "district": zoning_payload.get("code") or zoning_payload.get("district"),
            "district_id": zoning_payload.get("id"),
            "description": zoning_payload.get("description"),
            "metadata": zoning_payload.get("metadata"),
            "standards": standards_payload,
        }
    )


def build_zoning_rules_from_lookup(
    parcel: Parcel | dict[str, Any],
    payload: Any,
) -> ZoningRules:
    """Normalize service/API zoning lookup payloads into canonical ZoningRules."""

    parcel_contract = validate_contract("Parcel", parcel)
    if hasattr(payload, "model_dump"):
        raw_payload = payload.model_dump()
    else:
        raw_payload = dict(payload)

    rules = raw_payload.get("rules") or {}
    if hasattr(rules, "model_dump"):
        rules_payload = rules.model_dump()
    else:
        rules_payload = dict(rules)
    raw_setbacks = rules_payload.get("setbacks") or {}
    if hasattr(raw_setbacks, "model_dump"):
        raw_setbacks = raw_setbacks.model_dump()
    raw_setbacks = {
        key: value
        for key, value in dict(raw_setbacks).items()
        if value is not None
    }

    district = (
        raw_payload.get("district")
        or raw_payload.get("zoning_district")
        or rules_payload.get("district")
        or rules_payload.get("code")
    )
    return ZoningRules.model_validate(
        {
            "parcel_id": parcel_contract.parcel_id,
            "jurisdiction": raw_payload.get("jurisdiction") or parcel_contract.jurisdiction,
            "district": district,
            "description": rules_payload.get("description"),
            "overlays": rules_payload.get("overlays", raw_payload.get("overlays", [])),
            "setbacks": raw_setbacks,
            "min_lot_size_sqft": rules_payload.get("min_lot_size_sqft"),
            "max_units_per_acre": rules_payload.get("max_units_per_acre"),
            "height_limit_ft": rules_payload.get("height_limit_ft", rules_payload.get("height_limit")),
            "lot_coverage_max": rules_payload.get("lot_coverage_max", rules_payload.get("lot_coverage_limit")),
            "min_frontage_ft": rules_payload.get("min_frontage_ft"),
            "road_right_of_way_ft": rules_payload.get("road_right_of_way_ft"),
            "metadata": rules_payload.get("metadata"),
            "standards": rules_payload.get("standards") or [],
        }
    )


def missing_zoning_fields_for_layout(
    rules: ZoningRules | dict[str, Any],
) -> list[str]:
    """Return missing required zoning fields for layout compatibility."""

    contract = validate_contract("ZoningRules", rules)
    missing_fields: list[str] = []
    if not contract.district:
        missing_fields.append("district")
    if contract.min_lot_size_sqft is None:
        missing_fields.append("min_lot_size_sqft")
    if contract.max_units_per_acre is None:
        missing_fields.append("max_units_per_acre")
    for edge in ("front", "side", "rear"):
        if getattr(contract.setbacks, edge) is None:
            missing_fields.append(f"setbacks.{edge}")
    return missing_fields


def invalid_zoning_values_for_layout(
    rules: ZoningRules | dict[str, Any],
) -> list[str]:
    """Return invalid zoning fields that fail layout safety checks."""

    contract = validate_contract("ZoningRules", rules)
    invalid_fields: list[str] = []
    if (contract.min_lot_size_sqft or 0.0) <= 0.0:
        invalid_fields.append("min_lot_size_sqft")
    if (contract.max_units_per_acre or 0.0) <= 0.0:
        invalid_fields.append("max_units_per_acre")
    for edge in ("front", "side", "rear"):
        if (getattr(contract.setbacks, edge) or 0.0) <= 0.0:
            invalid_fields.append(f"setbacks.{edge}")
    return invalid_fields


def validate_zoning_rules_for_layout(
    rules: ZoningRules | dict[str, Any],
) -> ZoningRules:
    """Enforce the minimum zoning completeness required by the layout service."""

    contract = validate_contract("ZoningRules", rules)
    missing_fields = missing_zoning_fields_for_layout(contract)
    if missing_fields:
        raise ValueError(
            "ZoningRules is incomplete for layout compatibility: "
            + ", ".join(missing_fields)
        )

    invalid_fields = invalid_zoning_values_for_layout(contract)
    if invalid_fields:
        raise ValueError(
            "ZoningRules contains invalid layout values: " + ", ".join(invalid_fields)
        )
    return contract


def validate_parcel_output(parcel: Parcel | dict[str, Any]) -> Parcel:
    contract = validate_contract("Parcel", parcel)
    if not contract.parcel_id:
        raise ValueError("Parcel.parcel_id is required")
    if not contract.jurisdiction:
        raise ValueError("Parcel.jurisdiction is required")
    if contract.crs not in SUPPORTED_PARCEL_CRS:
        raise ValueError("Parcel.crs must be a supported parcel CRS")
    if contract.area_sqft <= 0:
        raise ValueError("Parcel.area_sqft must be greater than zero")
    if contract.centroid is None or len(contract.centroid) != 2:
        raise ValueError("Parcel.centroid must contain [x, y]")
    if contract.bounding_box is None or len(contract.bounding_box) != 4:
        raise ValueError("Parcel.bounding_box must contain [min_x, min_y, max_x, max_y]")
    return contract


def validate_layout_result_output(layout: LayoutResult | dict[str, Any]) -> LayoutResult:
    contract = validate_contract("LayoutResult", layout)
    if not contract.layout_id:
        raise ValueError("LayoutResult.layout_id is required")
    if not contract.parcel_id:
        raise ValueError("LayoutResult.parcel_id is required")
    if contract.unit_count < 0:
        raise ValueError("LayoutResult.unit_count must be >= 0")
    if contract.road_length_ft < 0:
        raise ValueError("LayoutResult.road_length_ft must be >= 0")
    return contract


def validate_feasibility_result_output(
    result: FeasibilityResult | dict[str, Any],
) -> FeasibilityResult:
    if isinstance(result, dict):
        required_payload_fields = (
            "parcel_id",
            "layout_id",
            "units",
            "projected_revenue",
            "projected_cost",
            "projected_profit",
        )
        missing_payload_fields = [
            field_name
            for field_name in required_payload_fields
            if field_name not in result and field_name != "units"
        ]
        if "units" not in result and "feasible_units" not in result and "max_units" not in result:
            missing_payload_fields.append("units")
        if missing_payload_fields:
            raise ValueError(
                "FeasibilityResult is missing required fields: "
                + ", ".join(missing_payload_fields)
            )
    contract = validate_contract("FeasibilityResult", result)
    required_non_null = (
        "parcel_id",
        "layout_id",
        "units",
        "projected_revenue",
        "projected_cost",
        "projected_profit",
    )
    missing = [
        field_name
        for field_name in required_non_null
        if getattr(contract, field_name, None) is None
    ]
    if missing:
        raise ValueError(
            "FeasibilityResult is missing required fields: " + ", ".join(missing)
        )
    return contract


def validate_pipeline_run_output(run: PipelineRun | dict[str, Any]) -> PipelineRun:
    contract = validate_contract("PipelineRun", run)
    if not contract.run_id:
        raise ValueError("PipelineRun.run_id is required")
    if not contract.status:
        raise ValueError("PipelineRun.status is required")
    if not contract.parcel_id:
        raise ValueError("PipelineRun.parcel_id is required")
    if not contract.timestamp:
        raise ValueError("PipelineRun.timestamp is required")
    allowed_statuses = {"completed", "non_buildable", "unsupported", "near_feasible"}
    if contract.status not in allowed_statuses:
        raise ValueError(
            "PipelineRun.status must be one of: " + ", ".join(sorted(allowed_statuses))
        )
    if contract.zoning_result.parcel_id != contract.parcel_id:
        raise ValueError("PipelineRun.zoning_result.parcel_id must match PipelineRun.parcel_id")
    if contract.status == "completed":
        if contract.layout_result is None:
            raise ValueError("PipelineRun.layout_result is required when status is completed")
        if contract.feasibility_result is None:
            raise ValueError("PipelineRun.feasibility_result is required when status is completed")
        if contract.near_feasible_result is not None:
            raise ValueError("PipelineRun.near_feasible_result must be null when status is completed")
    elif contract.status == "near_feasible":
        if contract.layout_result is not None:
            raise ValueError("PipelineRun.layout_result must be null when status is near_feasible")
        if contract.feasibility_result is not None:
            raise ValueError("PipelineRun.feasibility_result must be null when status is near_feasible")
        if contract.near_feasible_result is None:
            raise ValueError("PipelineRun.near_feasible_result is required when status is near_feasible")
        return contract
    else:
        if contract.layout_result is not None:
            raise ValueError("PipelineRun.layout_result must be null when status is not completed")
        if contract.feasibility_result is not None:
            raise ValueError("PipelineRun.feasibility_result must be null when status is not completed")
        if contract.near_feasible_result is not None:
            raise ValueError("PipelineRun.near_feasible_result must be null when status is not near_feasible")
        return contract
    if contract.layout_result.parcel_id != contract.parcel_id:
        raise ValueError("PipelineRun.layout_result.parcel_id must match PipelineRun.parcel_id")
    if contract.feasibility_result.parcel_id != contract.parcel_id:
        raise ValueError(
            "PipelineRun.feasibility_result.parcel_id must match PipelineRun.parcel_id"
        )
    if contract.feasibility_result.layout_id != contract.layout_result.layout_id:
        raise ValueError("PipelineRun.feasibility_result.layout_id must match PipelineRun.layout_result.layout_id")
    return contract


def validate_layout_candidate_batch_output(
    payload: LayoutCandidateBatch | dict[str, Any],
) -> LayoutCandidateBatch:
    contract = validate_contract("LayoutCandidateBatch", payload)
    if not contract.parcel_id:
        raise ValueError("LayoutCandidateBatch.parcel_id is required")
    if contract.candidate_count_valid != len(contract.layouts):
        raise ValueError("LayoutCandidateBatch.candidate_count_valid must match layouts length")
    for layout in contract.layouts:
        validate_layout_result_output(layout)
        if layout.parcel_id != contract.parcel_id:
            raise ValueError("LayoutCandidateBatch layout parcel_id must match batch parcel_id")
    return contract


def validate_optimization_run_output(
    payload: OptimizationRun | dict[str, Any],
) -> OptimizationRun:
    contract = validate_contract("OptimizationRun", payload)
    if not contract.optimization_run_id:
        raise ValueError("OptimizationRun.optimization_run_id is required")
    if not contract.parcel_id:
        raise ValueError("OptimizationRun.parcel_id is required")
    if contract.zoning_result.parcel_id != contract.parcel_id:
        raise ValueError("OptimizationRun.zoning_result.parcel_id must match OptimizationRun.parcel_id")
    ranked_scores = []
    for candidate in contract.layout_candidates:
        if candidate.layout_result.parcel_id != contract.parcel_id:
            raise ValueError("Optimization candidate layout parcel_id must match OptimizationRun.parcel_id")
        if candidate.feasibility_result.parcel_id != contract.parcel_id:
            raise ValueError("Optimization candidate feasibility parcel_id must match OptimizationRun.parcel_id")
        if candidate.feasibility_result.layout_id != candidate.layout_result.layout_id:
            raise ValueError("Optimization candidate feasibility layout_id must match layout_result.layout_id")
        ranked_scores.append(candidate.objective_score)
    if contract.best_candidate is not None:
        if contract.best_candidate.optimization_rank != 1:
            raise ValueError("OptimizationRun.best_candidate must have optimization_rank=1")
    if ranked_scores and ranked_scores != sorted(ranked_scores, reverse=True):
        raise ValueError("OptimizationRun.layout_candidates must be sorted by objective_score desc")
    return contract


def validate_experiment_run_output(run: ExperimentRun | dict[str, Any]) -> ExperimentRun:
    contract = validate_contract("ExperimentRun", run)
    if not contract.experiment_id:
        raise ValueError("ExperimentRun.experiment_id is required")
    if not contract.run_ids:
        raise ValueError("ExperimentRun.run_ids must include at least one run_id")
    return contract


def validate_pipeline_execution_result_output(
    payload: PipelineExecutionResult | dict[str, Any],
) -> PipelineExecutionResult:
    contract = validate_contract("PipelineExecutionResult", payload)
    if not contract.run_id:
        raise ValueError("PipelineExecutionResult.run_id is required")
    if not contract.status:
        raise ValueError("PipelineExecutionResult.status is required")
    return contract


def serialize_contract_canonical(schema_name: str, payload: Any) -> dict[str, Any]:
    """Serialize a contract with canonical outbound naming and drift checks."""

    contract = validate_contract(schema_name, payload)
    serialized = contract.model_dump(mode="python")
    expected_fields = set(get_canonical_serialization_fields(schema_name))
    serialized_fields = set(serialized.keys())
    if serialized_fields != expected_fields:
        unexpected = sorted(serialized_fields - expected_fields)
        missing = sorted(expected_fields - serialized_fields)
        details: list[str] = []
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        if missing:
            details.append("missing=" + ",".join(missing))
        raise ValueError(
            f"{schema_name} canonical serialization drift detected: " + "; ".join(details)
        )
    return serialized


def build_layout_result(parcel_id: str, payload: LayoutResult | dict[str, Any]) -> LayoutResult:
    layout_payload = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
    layout_payload.setdefault("parcel_id", parcel_id)
    metadata = layout_payload.get("metadata")
    if isinstance(metadata, dict):
        known_keys = {
            "source_engine",
            "source_run_id",
            "source_type",
            "rule_completeness",
            "legal_reliability",
            "observed_at",
        }
        if not set(metadata).issubset(known_keys):
            layout_payload["metadata"] = {
                "source_engine": metadata.get("source_engine")
                or metadata.get("source")
                or metadata.get("service")
                or "legacy-layout-runtime",
                "source_run_id": metadata.get("source_run_id") or metadata.get("run_id"),
                "source_type": metadata.get("source_type"),
                "rule_completeness": metadata.get("rule_completeness"),
                "legal_reliability": metadata.get("legal_reliability"),
            }
    return LayoutResult.model_validate(layout_payload)


def validate_service_output(service_name: str, payload: Any, *, parcel_id: str | None = None):
    """Validate a service output according to the Bedrock service rules."""

    rule = get_service_validation_rule(service_name)
    if rule.output_schema == "Parcel":
        return validate_parcel_output(payload)
    if rule.output_schema == "ZoningRules":
        if isinstance(payload, ZoningRules):
            return validate_zoning_rules_for_layout(payload)
        if isinstance(payload, dict) and "zoning" in payload and "standards" in payload:
            if parcel_id is None:
                raise ValueError("parcel_id is required to validate ZoningRules service output")
            jurisdiction = payload.get("jurisdiction")
            return validate_zoning_rules_for_layout(
                build_zoning_rules(parcel_id, payload["zoning"], payload["standards"], jurisdiction=jurisdiction)
            )
        if isinstance(payload, dict):
            return validate_zoning_rules_for_layout(payload)
        raise ValidationError.from_exception_data(
            "ZoningRules",
            [{"loc": ("payload",), "msg": "expected canonical ZoningRules or zoning+standards payload", "type": "value_error"}],
        )
    if rule.output_schema == "LayoutResult" and parcel_id is not None:
        return validate_layout_result_output(build_layout_result(parcel_id, payload))
    if rule.output_schema == "LayoutResult":
        return validate_layout_result_output(payload)
    if rule.output_schema == "FeasibilityResult":
        return validate_feasibility_result_output(payload)
    if rule.output_schema == "PipelineRun":
        return validate_pipeline_run_output(payload)
    if rule.output_schema == "LayoutCandidateBatch":
        return validate_layout_candidate_batch_output(payload)
    if rule.output_schema == "OptimizationRun":
        return validate_optimization_run_output(payload)
    if rule.output_schema == "ExperimentRun":
        return validate_experiment_run_output(payload)
    if rule.output_schema == "PipelineExecutionResult":
        return validate_pipeline_execution_result_output(payload)
    return validate_contract(rule.output_schema, payload)


def validate_feasibility_pipeline_contracts(
    parcel: Parcel | dict[str, Any],
    zoning_rules: ZoningRules | dict[str, Any],
    layout_result: LayoutResult | dict[str, Any],
    feasibility_result: FeasibilityResult | dict[str, Any],
) -> None:
    """Enforce cross-stage linkage invariants for the core pipeline."""

    parcel_contract = validate_contract("Parcel", parcel)
    zoning_contract = validate_contract("ZoningRules", zoning_rules)
    layout_contract = validate_contract("LayoutResult", layout_result)
    feasibility_contract = validate_contract("FeasibilityResult", feasibility_result)

    if zoning_contract.parcel_id != parcel_contract.parcel_id:
        raise ValueError("ZoningRules.parcel_id must match Parcel.parcel_id")
    if layout_contract.parcel_id != parcel_contract.parcel_id:
        raise ValueError("LayoutResult.parcel_id must match Parcel.parcel_id")
    if (
        feasibility_contract.parcel_id is not None
        and feasibility_contract.parcel_id != parcel_contract.parcel_id
    ):
        raise ValueError("FeasibilityResult.parcel_id must match Parcel.parcel_id when present")
    if feasibility_contract.layout_id != layout_contract.layout_id:
        raise ValueError("FeasibilityResult.layout_id must match LayoutResult.layout_id")
