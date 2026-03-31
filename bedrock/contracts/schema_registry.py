"""Registry for Bedrock's authoritative cross-service schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Type

from .base import BedrockModel
from .experiment_run import ExperimentRun
from .feasibility_result import FeasibilityResult
from .layout_result import LayoutResult
from .layout_candidate_batch import LayoutCandidateBatch
from .market_data import MarketData
from .optimization_run import OptimizationRun
from .parcel import Parcel
from .pipeline_execution_result import PipelineExecutionResult
from .pipeline_run import PipelineRun
from .scenario_evaluation import ScenarioEvaluation
from .zoning_rules import ZoningRules


@dataclass(frozen=True)
class SchemaRegistration:
    schema_name: str
    schema_version: str
    model: Type[BedrockModel]
    producer_services: tuple[str, ...]
    consumer_services: tuple[str, ...]
    compatibility_modules: tuple[str, ...]
    invariants: tuple[str, ...]


@dataclass(frozen=True)
class ServiceValidationRule:
    service_name: str
    input_schema: str | None
    output_schema: str
    rule_notes: tuple[str, ...]


@dataclass(frozen=True)
class ExtensionContractRegistration:
    schema_name: str
    schema_version: str
    model: Type[BedrockModel]
    contract_scope: str
    allowed_producers: tuple[str, ...]
    allowed_consumers: tuple[str, ...]
    governance_status: str


SCHEMA_REGISTRY: Dict[str, SchemaRegistration] = {
    "Parcel": SchemaRegistration(
        schema_name="Parcel",
        schema_version="1.0.0",
        model=Parcel,
        producer_services=("bedrock.engines.parcel_engine.get_parcel",),
        consumer_services=(
            "bedrock.engines.zoning_engine.get_zoning",
            "bedrock.engines.parcel_engine.generate_layout",
            "bedrock.pipelines.parcel_feasibility_pipeline",
        ),
        compatibility_modules=("contracts.parcel",),
        invariants=(
            "Parcel geometry must be Polygon or MultiPolygon GeoJSON.",
            "Parcel area is canonicalized as area_sqft.",
            "Parcel identifiers must remain stable across all pipeline stages.",
        ),
    ),
    "ZoningRules": SchemaRegistration(
        schema_name="ZoningRules",
        schema_version="1.0.0",
        model=ZoningRules,
        producer_services=("bedrock.engines.zoning_engine.get_zoning",),
        consumer_services=(
            "bedrock.engines.parcel_engine.generate_layout",
            "bedrock.pipelines.parcel_feasibility_pipeline",
        ),
        compatibility_modules=("contracts.zoning",),
        invariants=(
            "Zoning rules are parcel-scoped even when sourced from district-level systems.",
            "Layout-compatible zoning rules must include district, min_lot_size_sqft, max_units_per_acre, and complete front/side/rear setbacks.",
            "Canonical outbound field names are height_limit_ft and lot_coverage_max; legacy aliases are input-only.",
            "Overlay names must normalize to a deterministic de-duplicated string list.",
            "Setback and density fields must remain numerically derivable from the standards list.",
            "Standards must preserve citation metadata when available.",
        ),
    ),
    "LayoutResult": SchemaRegistration(
        schema_name="LayoutResult",
        schema_version="1.0.0",
        model=LayoutResult,
        producer_services=("bedrock.engines.parcel_engine.generate_layout",),
        consumer_services=(
            "bedrock.pipelines.parcel_feasibility_pipeline",
            "bedrock.feasibility_agent.score_layout",
        ),
        compatibility_modules=("contracts.layout", "GIS_lot_layout_optimizer.services.layout_models"),
        invariants=(
            "Layout results must carry parcel_id and layout_id.",
            "Unit counts are canonicalized as unit_count.",
            "Road and lot geometries must be GeoJSON payloads.",
        ),
    ),
    "FeasibilityResult": SchemaRegistration(
        schema_name="FeasibilityResult",
        schema_version="1.0.0",
        model=FeasibilityResult,
        producer_services=("bedrock.pipelines.parcel_feasibility_pipeline.score_layout",),
        consumer_services=("bedrock.orchestration.pipeline_runner",),
        compatibility_modules=("contracts.feasibility",),
        invariants=(
            "Feasibility outputs must reference a scenario and a layout.",
            "Feasible unit capacity is canonicalized as units with compatibility aliases for feasible_units and max_units.",
            "Financial projections are embedded directly in the FeasibilityResult contract.",
            "Ranking metadata and explanation blocks must be deterministic for identical inputs.",
            "Risk score and confidence are normalized to [0, 1].",
        ),
    ),
    "PipelineRun": SchemaRegistration(
        schema_name="PipelineRun",
        schema_version="1.0.0",
        model=PipelineRun,
        producer_services=("bedrock.api.pipeline_api.run_pipeline",),
        consumer_services=(
            "bedrock.api.runs_api",
            "bedrock.api.evaluation_api",
            "tests.pipeline.test_pipeline_run",
        ),
        compatibility_modules=("bedrock.api.pipeline_api",),
        invariants=(
            "PipelineRun must include run_id, status, parcel_id, zoning_result, and timestamp.",
            "Completed PipelineRun payloads must include layout_result and feasibility_result.",
            "Non-buildable PipelineRun payloads must set zoning_bypassed=true and omit layout_result and feasibility_result.",
            "PipelineRun.parcel_id must match nested zoning_result.parcel_id and layout_result.parcel_id when layout_result is present.",
            "PipelineRun.feasibility_result.layout_id must match PipelineRun.layout_result.layout_id when both are present.",
        ),
    ),
}


SERVICE_VALIDATION_RULES: Dict[str, ServiceValidationRule] = {
    "bedrock.engines.parcel_engine.get_parcel": ServiceValidationRule(
        service_name="bedrock.engines.parcel_engine.get_parcel",
        input_schema=None,
        output_schema="Parcel",
        rule_notes=(
            "Adapters may accept upstream area or area_sqft fields, but must emit Parcel.",
            "Geometry coercion must happen at the adapter boundary.",
        ),
    ),
    "bedrock.engines.zoning_engine.get_zoning": ServiceValidationRule(
        service_name="bedrock.engines.zoning_engine.get_zoning",
        input_schema="Parcel",
        output_schema="ZoningRules",
        rule_notes=(
            "District-only source payloads must be normalized into parcel-scoped ZoningRules.",
            "Layout-compatible completeness requires district, min_lot_size_sqft, max_units_per_acre, and front/side/rear setbacks.",
            "Services may accept max_height or max_lot_coverage as input aliases, but must emit height_limit_ft and lot_coverage_max.",
            "Overlay payloads must be normalized into the canonical overlays list.",
            "Standards lists and explicit numeric fields must agree.",
        ),
    ),
    "bedrock.engines.parcel_engine.generate_layout": ServiceValidationRule(
        service_name="bedrock.engines.parcel_engine.generate_layout",
        input_schema="ZoningRules",
        output_schema="LayoutResult",
        rule_notes=(
            "Legacy layout runtimes may emit units or lot_count, but Bedrock stores unit_count.",
            "Layout outputs that omit parcel_id must be enriched before leaving the adapter.",
        ),
    ),
    "bedrock.pipelines.parcel_feasibility_pipeline.score_layout": ServiceValidationRule(
        service_name="bedrock.pipelines.parcel_feasibility_pipeline.score_layout",
        input_schema="LayoutResult",
        output_schema="FeasibilityResult",
        rule_notes=(
            "Feasibility scoring must retain layout linkage and normalized risk metrics.",
        ),
    ),
    "bedrock.api.parcel_api.load_parcel": ServiceValidationRule(
        service_name="bedrock.api.parcel_api.load_parcel",
        input_schema=None,
        output_schema="Parcel",
        rule_notes=(
            "POST /parcel/load must emit the canonical Parcel contract.",
        ),
    ),
    "bedrock.api.zoning_api.lookup_zoning": ServiceValidationRule(
        service_name="bedrock.api.zoning_api.lookup_zoning",
        input_schema="Parcel",
        output_schema="ZoningRules",
        rule_notes=(
            "POST /zoning/lookup must emit canonical parcel-scoped ZoningRules, not district-only responses.",
            "API output must satisfy layout-compatible completeness before it is returned.",
            "API output must emit height_limit_ft and lot_coverage_max rather than any legacy alias.",
            "Overlay names must survive lookup normalization without duplicates.",
        ),
    ),
    "bedrock.api.layout_api.layout_search": ServiceValidationRule(
        service_name="bedrock.api.layout_api.layout_search",
        input_schema="ZoningRules",
        output_schema="LayoutResult",
        rule_notes=(
            "POST /layout/search must emit the canonical LayoutResult field names.",
        ),
    ),
    "bedrock.api.pipeline_api.run_pipeline": ServiceValidationRule(
        service_name="bedrock.api.pipeline_api.run_pipeline",
        input_schema=None,
        output_schema="PipelineRun",
        rule_notes=(
            "POST /pipeline/run must emit canonical PipelineRun output shape.",
            "Nested zoning/layout/feasibility objects must remain canonical contracts.",
            "API output must keep cross-stage parcel/layout linkage invariants.",
        ),
    ),
    "bedrock.api.runs_api.get_run": ServiceValidationRule(
        service_name="bedrock.api.runs_api.get_run",
        input_schema=None,
        output_schema="PipelineRun",
        rule_notes=(
            "GET /runs/{run_id} must emit canonical PipelineRun payloads.",
        ),
    ),
    "bedrock.api.experiments_api.create_experiment": ServiceValidationRule(
        service_name="bedrock.api.experiments_api.create_experiment",
        input_schema=None,
        output_schema="ExperimentRun",
        rule_notes=(
            "POST /experiments/create must emit canonical ExperimentRun payloads.",
        ),
    ),
    "bedrock.api.experiments_api.get_experiment": ServiceValidationRule(
        service_name="bedrock.api.experiments_api.get_experiment",
        input_schema=None,
        output_schema="ExperimentRun",
        rule_notes=(
            "GET /experiments/{experiment_id} must emit canonical ExperimentRun payloads.",
        ),
    ),
    "bedrock.services.pipeline_service.run": ServiceValidationRule(
        service_name="bedrock.services.pipeline_service.run",
        input_schema=None,
        output_schema="PipelineExecutionResult",
        rule_notes=(
            "PipelineExecutionResult is internal-only and must not be exposed directly over API boundaries.",
        ),
    ),
}


EXTENSION_CONTRACT_REGISTRY: Dict[str, ExtensionContractRegistration] = {
    "MarketData": ExtensionContractRegistration(
        schema_name="MarketData",
        schema_version="1.0.0",
        model=MarketData,
        contract_scope="feasibility_input",
        allowed_producers=("bedrock.api.feasibility_api", "bedrock.services.benchmark_harness"),
        allowed_consumers=("bedrock.services.feasibility_service", "bedrock.pipelines.parcel_feasibility_pipeline"),
        governance_status="approved_support_contract",
    ),
    "ScenarioEvaluation": ExtensionContractRegistration(
        schema_name="ScenarioEvaluation",
        schema_version="1.0.0",
        model=ScenarioEvaluation,
        contract_scope="feasibility_summary",
        allowed_producers=("bedrock.services.feasibility_service",),
        allowed_consumers=("bedrock.api.feasibility_api", "bedrock.services.benchmark_harness"),
        governance_status="approved_support_contract",
    ),
    "ExperimentRun": ExtensionContractRegistration(
        schema_name="ExperimentRun",
        schema_version="1.0.0",
        model=ExperimentRun,
        contract_scope="experiment_metadata",
        allowed_producers=("bedrock.services.experiment_run_service", "bedrock.api.experiments_api"),
        allowed_consumers=("bedrock.api.experiments_api", "bedrock.docs", "analytics"),
        governance_status="approved_support_contract",
    ),
    "LayoutCandidateBatch": ExtensionContractRegistration(
        schema_name="LayoutCandidateBatch",
        schema_version="1.0.0",
        model=LayoutCandidateBatch,
        contract_scope="layout_candidate_batch",
        allowed_producers=("bedrock.services.layout_service", "bedrock.api.layout_api"),
        allowed_consumers=("bedrock.services.pipeline_service", "bedrock.api.pipeline_api"),
        governance_status="approved_support_contract",
    ),
    "OptimizationRun": ExtensionContractRegistration(
        schema_name="OptimizationRun",
        schema_version="1.0.0",
        model=OptimizationRun,
        contract_scope="optimization_execution",
        allowed_producers=("bedrock.services.pipeline_service", "bedrock.api.pipeline_api"),
        allowed_consumers=("bedrock.api.pipeline_api", "analytics", "ui"),
        governance_status="approved_support_contract",
    ),
    "PipelineExecutionResult": ExtensionContractRegistration(
        schema_name="PipelineExecutionResult",
        schema_version="1.0.0",
        model=PipelineExecutionResult,
        contract_scope="internal_orchestration",
        allowed_producers=("bedrock.services.pipeline_service",),
        allowed_consumers=("bedrock.api.pipeline_api", "tests.pipeline"),
        governance_status="internal_support_contract",
    ),
}

CANONICAL_SERIALIZATION_FIELDS: Dict[str, tuple[str, ...]] = {
    "Parcel": (
        "schema_name",
        "schema_version",
        "parcel_id",
        "geometry",
        "jurisdiction",
        "crs",
        "area_sqft",
        "centroid",
        "bounding_box",
        "land_use",
        "slope_percent",
        "flood_zone",
        "zoning_district",
        "utilities",
        "access_points",
        "topography",
        "existing_structures",
        "metadata",
    ),
    "ZoningRules": (
        "schema_name",
        "schema_version",
        "parcel_id",
        "jurisdiction",
        "district",
        "district_id",
        "description",
        "overlays",
        "standards",
        "setbacks",
        "min_lot_size_sqft",
        "max_units_per_acre",
        "height_limit_ft",
        "lot_coverage_max",
        "min_frontage_ft",
        "road_right_of_way_ft",
        "allowed_uses",
        "citations",
        "metadata",
    ),
    "LayoutResult": (
        "schema_name",
        "schema_version",
        "layout_id",
        "parcel_id",
        "unit_count",
        "road_length_ft",
        "lot_geometries",
        "road_geometries",
        "open_space_area_sqft",
        "utility_length_ft",
        "score",
        "buildable_area_sqft",
        "metadata",
    ),
    "FeasibilityResult": (
        "schema_name",
        "schema_version",
        "scenario_id",
        "layout_id",
        "parcel_id",
        "units",
        "estimated_home_price",
        "construction_cost_per_home",
        "development_cost_total",
        "projected_revenue",
        "projected_cost",
        "projected_profit",
        "ROI",
        "profit_margin",
        "revenue_per_unit",
        "cost_per_unit",
        "rank",
        "risk_score",
        "requested_units",
        "constraint_violations",
        "confidence",
        "status",
        "financial_summary",
        "explanation",
        "assumptions",
        "metadata",
    ),
    "PipelineRun": (
        "schema_name",
        "schema_version",
        "run_id",
        "status",
        "parcel_id",
        "zoning_result",
        "layout_result",
        "feasibility_result",
        "near_feasible_result",
        "timestamp",
        "git_commit",
        "input_hash",
        "stage_runtimes",
        "zoning_bypassed",
        "bypass_reason",
    ),
}


def get_schema_registration(schema_name: str) -> SchemaRegistration:
    return SCHEMA_REGISTRY[schema_name]


def get_schema_model(schema_name: str) -> Type[BedrockModel]:
    registration = SCHEMA_REGISTRY.get(schema_name)
    if registration is not None:
        return registration.model
    extension = EXTENSION_CONTRACT_REGISTRY.get(schema_name)
    if extension is not None:
        return extension.model
    raise KeyError(schema_name)


def list_schema_registrations() -> Iterable[SchemaRegistration]:
    return SCHEMA_REGISTRY.values()


def get_service_validation_rule(service_name: str) -> ServiceValidationRule:
    return SERVICE_VALIDATION_RULES[service_name]


def list_service_validation_rules() -> Mapping[str, ServiceValidationRule]:
    return SERVICE_VALIDATION_RULES


def list_extension_contract_registrations() -> Mapping[str, ExtensionContractRegistration]:
    return EXTENSION_CONTRACT_REGISTRY


def get_canonical_serialization_fields(schema_name: str) -> tuple[str, ...]:
    return CANONICAL_SERIALIZATION_FIELDS[schema_name]
