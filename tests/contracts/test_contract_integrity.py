from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BEDROCK_ROOT = ROOT / "bedrock"
GIS_ROOT = ROOT / "GIS_lot_layout_optimizer"
GIS_API_ROOT = GIS_ROOT / "apps" / "python-api"

for entry in reversed((str(GIS_API_ROOT), str(GIS_ROOT), str(BEDROCK_ROOT), str(ROOT))):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from contracts.feasibility import FeasibilityResult as LegacyFeasibilityResult
from contracts.feasibility_result import FeasibilityResult
from contracts.market_data import MarketData
from contracts.pipeline_run import PipelineRun
from contracts.scenario_evaluation import ScenarioEvaluation
from contracts.schema_registry import (
    CANONICAL_SERIALIZATION_FIELDS,
    EXTENSION_CONTRACT_REGISTRY,
    SCHEMA_REGISTRY,
    SERVICE_VALIDATION_RULES,
)
from contracts.validators import (
    build_layout_result,
    build_zoning_rules,
    build_zoning_rules_from_lookup,
    validate_contract,
    validate_feasibility_result_output,
    validate_feasibility_pipeline_contracts,
    validate_layout_result_output,
    validate_parcel_output,
    validate_pipeline_run_output,
    validate_service_output,
    validate_zoning_rules_for_layout,
    serialize_contract_canonical,
)
from GIS_lot_layout_optimizer.services.layout_models import LayoutResult as LegacyLayoutResult


class ContractIntegrityTest(unittest.TestCase):
    def test_registry_contains_canonical_pipeline_schemas(self) -> None:
        self.assertEqual(
            set(SCHEMA_REGISTRY.keys()),
            {"Parcel", "ZoningRules", "LayoutResult", "FeasibilityResult", "PipelineRun"},
        )
        self.assertIn("bedrock.engines.zoning_engine.get_zoning", SERVICE_VALIDATION_RULES)
        self.assertIn("bedrock.engines.parcel_engine.generate_layout", SERVICE_VALIDATION_RULES)
        self.assertIn("bedrock.api.zoning_api.lookup_zoning", SERVICE_VALIDATION_RULES)
        self.assertIn("bedrock.api.layout_api.layout_search", SERVICE_VALIDATION_RULES)
        self.assertIn("bedrock.api.pipeline_api.run_pipeline", SERVICE_VALIDATION_RULES)
        self.assertIn("bedrock.api.runs_api.get_run", SERVICE_VALIDATION_RULES)
        self.assertIn("bedrock.api.experiments_api.create_experiment", SERVICE_VALIDATION_RULES)
        self.assertIn("bedrock.api.experiments_api.get_experiment", SERVICE_VALIDATION_RULES)
        self.assertIn("bedrock.services.pipeline_service.run", SERVICE_VALIDATION_RULES)
        self.assertEqual(set(CANONICAL_SERIALIZATION_FIELDS.keys()), set(SCHEMA_REGISTRY.keys()))

    def test_extension_contracts_are_approved_support_only(self) -> None:
        self.assertEqual(
            set(EXTENSION_CONTRACT_REGISTRY.keys()),
            {"MarketData", "ScenarioEvaluation", "ExperimentRun", "PipelineExecutionResult"},
        )
        self.assertNotIn("MarketData", SCHEMA_REGISTRY)
        self.assertNotIn("ScenarioEvaluation", SCHEMA_REGISTRY)
        self.assertEqual(
            EXTENSION_CONTRACT_REGISTRY["ScenarioEvaluation"].governance_status,
            "approved_support_contract",
        )
        self.assertEqual(
            EXTENSION_CONTRACT_REGISTRY["ExperimentRun"].governance_status,
            "approved_support_contract",
        )
        self.assertEqual(
            EXTENSION_CONTRACT_REGISTRY["PipelineExecutionResult"].governance_status,
            "internal_support_contract",
        )

    def test_parcel_fixture_validates_against_canonical_schema(self) -> None:
        payload = json.loads((BEDROCK_ROOT / "test_data" / "parcel_test_001.json").read_text())
        contract = validate_contract("Parcel", payload)
        self.assertEqual(contract.parcel_id, "parcel_test_001")
        self.assertEqual(contract.area_sqft, 435600)
        self.assertEqual(contract.area, 435600)

    def test_zoning_fixture_validates_and_synthesizes_standards(self) -> None:
        payload = json.loads((BEDROCK_ROOT / "test_data" / "zoning_test_001.json").read_text())
        contract = validate_contract("ZoningRules", payload)
        self.assertEqual(contract.district, "R-1")
        self.assertEqual(contract.setbacks.front, 25)
        self.assertGreaterEqual(len(contract.standards), 6)

    def test_bedrock_compatibility_shapes_are_compatible(self) -> None:
        parcel = validate_contract(
            "Parcel",
            {
                "parcel_id": "compat-parcel",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]],
                },
                "area": 100000.0,
                "jurisdiction": "test_county",
                "zoning_district": "R-1",
                "utilities": ["water", "sewer"],
                "access_points": [{"type": "Point", "coordinates": [0.0, 0.5]}],
                "topography": {"slope_percent": 2.0},
                "existing_structures": [],
                "metadata": {"source_engine": "bedrock.stub.parcel_engine", "source_run_id": "stub"},
            },
        )
        zoning_rules = build_zoning_rules(
            parcel.parcel_id,
            {
                "id": "stub-r1",
                "jurisdiction_id": f"stub:{parcel.jurisdiction}",
                "code": "R-1",
                "description": "Deterministic stub zoning district for test execution.",
                "metadata": {"source_engine": "bedrock.stub.zoning_engine", "source_run_id": "stub"},
            },
            [
                {
                    "id": "stub-min-lot-size",
                    "district_id": "stub-r1",
                    "standard_type": "min_lot_size_sqft",
                    "value": 8000,
                    "units": "sqft",
                    "conditions": [],
                    "citation": "stub",
                    "metadata": {"source_engine": "bedrock.stub.zoning_engine", "source_run_id": "stub"},
                },
                {
                    "id": "stub-max-units-per-acre",
                    "district_id": "stub-r1",
                    "standard_type": "max_units_per_acre",
                    "value": 5,
                    "units": "du/ac",
                    "conditions": [],
                    "citation": "stub",
                    "metadata": {"source_engine": "bedrock.stub.zoning_engine", "source_run_id": "stub"},
                },
            ],
            jurisdiction=parcel.jurisdiction,
        )
        layout = build_layout_result(
            parcel.parcel_id,
            {
                "layout_id": "layout-compat",
                "lot_count": 10,
                "road_length": 1200.0,
                "street_network": [],
                "lot_geometries": [],
                "open_space_area": 1000.0,
                "utility_length": 500.0,
            },
        )
        feasibility = LegacyFeasibilityResult(
            scenario_id="scenario-compat",
            parcel_id=parcel.parcel_id,
            layout_id=layout.layout_id,
            max_units=layout.lot_count,
            risk_score=0.2,
            confidence=0.9,
        )
        validate_feasibility_pipeline_contracts(parcel, zoning_rules, layout, feasibility)

    def test_legacy_layout_runtime_output_can_be_promoted(self) -> None:
        legacy = LegacyLayoutResult(
            layout_id="legacy-layout",
            units=12,
            road_length=900.0,
            lot_geometries=[],
            road_geometries=[],
            score=0.81,
            metadata={"source": "legacy-layout-service"},
        )
        promoted = build_layout_result("parcel-legacy", legacy.model_dump())
        self.assertEqual(promoted.unit_count, 12)
        self.assertEqual(promoted.parcel_id, "parcel-legacy")
        self.assertEqual(promoted.road_length_ft, 900.0)

    def test_service_validator_accepts_legacy_zoning_service_shape(self) -> None:
        validated = validate_service_output(
            "bedrock.engines.zoning_engine.get_zoning",
            {
                "zoning": {
                    "id": "stub-r1",
                    "jurisdiction_id": "stub:test_county",
                    "code": "R-1",
                    "description": "Deterministic stub zoning district for test execution.",
                },
                "standards": [
                    {
                        "id": "stub-min-lot-size",
                        "district_id": "stub-r1",
                        "standard_type": "min_lot_size_sqft",
                        "value": 8000,
                        "units": "sqft",
                    }
                    ,
                    {
                        "id": "stub-max-density",
                        "district_id": "stub-r1",
                        "standard_type": "max_units_per_acre",
                        "value": 5,
                        "units": "du/ac",
                    },
                    {
                        "id": "stub-front-setback",
                        "district_id": "stub-r1",
                        "standard_type": "front_setback_ft",
                        "value": 25,
                        "units": "ft",
                    },
                    {
                        "id": "stub-side-setback",
                        "district_id": "stub-r1",
                        "standard_type": "side_setback_ft",
                        "value": 8,
                        "units": "ft",
                    },
                    {
                        "id": "stub-rear-setback",
                        "district_id": "stub-r1",
                        "standard_type": "rear_setback_ft",
                        "value": 20,
                        "units": "ft",
                    },
                ],
                "jurisdiction": "test_county",
            },
            parcel_id="service-shape",
        )
        self.assertEqual(validated.parcel_id, "service-shape")
        self.assertEqual(validated.district, "R-1")

    def test_service_validator_accepts_canonical_zoning_rules_shape(self) -> None:
        validated = validate_service_output(
            "bedrock.api.zoning_api.lookup_zoning",
            {
                "parcel_id": "service-shape-canonical",
                "jurisdiction": "test_county",
                "district": "R-1",
                "min_lot_size_sqft": 6000.0,
                "max_units_per_acre": 5.0,
                "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
            },
        )
        self.assertEqual(validated.parcel_id, "service-shape-canonical")
        self.assertEqual(validated.district, "R-1")

    def test_lookup_response_can_be_promoted_to_canonical_zoning_rules(self) -> None:
        parcel = validate_contract(
            "Parcel",
            {
                "parcel_id": "parcel-zoning-api",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]],
                },
                "area_sqft": 100000.0,
                "jurisdiction": "Lehi",
            },
        )
        canonical = build_zoning_rules_from_lookup(
            parcel,
            {
                "jurisdiction": "Lehi",
                "district": "TH-5",
                "rules": {
                    "district": "TH-5",
                    "overlays": ["Foothill Overlay", "Foothill Overlay", "  Wildlife Corridor  "],
                    "min_lot_size_sqft": 6000.0,
                    "max_units_per_acre": 5.0,
                    "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                },
            },
        )
        self.assertEqual(canonical.parcel_id, "parcel-zoning-api")
        self.assertEqual(canonical.district, "TH-5")
        self.assertEqual(canonical.overlays, ["Foothill Overlay", "Wildlife Corridor"])
        self.assertGreaterEqual(len(canonical.standards), 5)

    def test_zoning_rules_overlay_alias_is_normalized(self) -> None:
        contract = validate_contract(
            "ZoningRules",
            {
                "parcel_id": "overlay-parcel",
                "district": "R-1",
                "overlay": "Airport Overlay",
                "min_lot_size_sqft": 6000.0,
                "max_units_per_acre": 5.0,
                "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
            },
        )
        self.assertEqual(contract.overlays, ["Airport Overlay"])

    def test_zoning_rules_accepts_legacy_height_and_coverage_aliases_but_serializes_canonically(self) -> None:
        contract = validate_contract(
            "ZoningRules",
            {
                "parcel_id": "alias-parcel",
                "district": "R-1",
                "min_lot_size_sqft": 6000.0,
                "max_units_per_acre": 5.0,
                "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                "max_height": 35.0,
                "max_lot_coverage": 0.45,
            },
        )
        serialized = contract.model_dump()
        self.assertEqual(contract.height_limit_ft, 35.0)
        self.assertEqual(contract.lot_coverage_max, 0.45)
        self.assertIn("height_limit_ft", serialized)
        self.assertIn("lot_coverage_max", serialized)
        self.assertNotIn("max_height", serialized)
        self.assertNotIn("max_lot_coverage", serialized)

    def test_zoning_rules_validate_consistently_across_supported_jurisdictions(self) -> None:
        cases = [
            ("slc-r1", "Salt Lake City", "R-1-7000", ["Foothill Overlay"]),
            ("lehi-th", "Lehi", "TH-5", []),
            ("draper-r3", "Draper", "R3", ["Sensitive Lands Overlay"]),
        ]
        for parcel_id, jurisdiction, district, overlays in cases:
            frozen = validate_contract(
                "ZoningRules",
                {
                    "parcel_id": parcel_id,
                    "jurisdiction": jurisdiction,
                    "district": district,
                    "overlays": overlays,
                    "min_lot_size_sqft": 6000.0,
                    "max_units_per_acre": 5.0,
                    "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                },
            )
            self.assertEqual(frozen.parcel_id, parcel_id)
            self.assertEqual(frozen.jurisdiction, jurisdiction)
            self.assertEqual(frozen.district, district)
            self.assertEqual(frozen.overlays, overlays)

    def test_zoning_rules_layout_validator_rejects_incomplete_rules(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_lot_size_sqft"):
            validate_zoning_rules_for_layout(
                {
                    "parcel_id": "incomplete-zoning",
                    "district": "R-1",
                    "max_units_per_acre": 5.0,
                    "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                }
            )

    def test_zoning_rules_layout_validator_rejects_zero_setbacks(self) -> None:
        with self.assertRaisesRegex(ValueError, "setbacks.side"):
            validate_zoning_rules_for_layout(
                {
                    "parcel_id": "invalid-zoning-zero",
                    "district": "R-1",
                    "min_lot_size_sqft": 6000.0,
                    "max_units_per_acre": 5.0,
                    "setbacks": {"front": 25.0, "side": 0.0, "rear": 20.0},
                }
            )

    def test_zoning_rules_layout_validator_rejects_non_positive_density(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_units_per_acre"):
            validate_zoning_rules_for_layout(
                {
                    "parcel_id": "invalid-zoning-density",
                    "district": "R-1",
                    "min_lot_size_sqft": 6000.0,
                    "max_units_per_acre": 0.0,
                    "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                }
            )

    def test_parcel_output_validator_rejects_invalid_area(self) -> None:
        with self.assertRaisesRegex(ValueError, "area_sqft"):
            validate_parcel_output(
                {
                    "parcel_id": "bad-parcel",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]],
                    },
                    "jurisdiction": "test_county",
                    "area_sqft": 0.0,
                }
            )

    def test_layout_result_output_validator_rejects_missing_layout_id(self) -> None:
        with self.assertRaisesRegex(Exception, "layout_id"):
            validate_layout_result_output(
                {
                    "parcel_id": "parcel-1",
                    "units": 4,
                    "road_length": 200.0,
                    "lot_geometries": [],
                    "road_geometries": [],
                }
            )

    def test_feasibility_result_output_validator_rejects_missing_financial_core(self) -> None:
        with self.assertRaisesRegex(ValueError, "projected_revenue"):
            validate_feasibility_result_output(
                {
                    "scenario_id": "scenario-missing",
                    "layout_id": "layout-missing",
                    "parcel_id": "parcel-missing",
                    "units": 3,
                    "risk_score": 0.2,
                    "confidence": 0.8,
                    "projected_cost": 100.0,
                    "projected_profit": 20.0,
                }
            )

    def test_canonical_serialization_keys_are_locked(self) -> None:
        parcel_payload = {
            "parcel_id": "parcel-lock",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]],
            },
            "area_sqft": 1000.0,
            "jurisdiction": "test_county",
        }
        zoning_payload = {
            "parcel_id": "parcel-lock",
            "district": "R-1",
            "min_lot_size_sqft": 6000.0,
            "max_units_per_acre": 5.0,
            "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
            "max_height": 35.0,
            "max_lot_coverage": 0.45,
        }
        layout_payload = {
            "layout_id": "layout-lock",
            "parcel_id": "parcel-lock",
            "units": 4,
            "road_length": 400.0,
            "lot_geometries": [],
            "road_geometries": [],
            "open_space_area": 100.0,
            "utility_length": 30.0,
            "score": 0.75,
        }
        feasibility_payload = {
            "scenario_id": "scenario-lock",
            "layout_id": "layout-lock",
            "parcel_id": "parcel-lock",
            "units": 4,
            "projected_revenue": 1600000.0,
            "projected_cost": 1200000.0,
            "projected_profit": 400000.0,
            "ROI": 0.3333333,
            "risk_score": 0.2,
            "confidence": 0.9,
        }
        pipeline_run_payload = {
            "run_id": "run-lock",
            "parcel_id": "parcel-lock",
            "zoning_result": zoning_payload,
            "layout_result": layout_payload,
            "feasibility_result": feasibility_payload,
            "timestamp": "2026-03-20T00:00:00Z",
        }
        self.assertEqual(
            set(serialize_contract_canonical("Parcel", parcel_payload).keys()),
            set(CANONICAL_SERIALIZATION_FIELDS["Parcel"]),
        )
        self.assertEqual(
            set(serialize_contract_canonical("ZoningRules", zoning_payload).keys()),
            set(CANONICAL_SERIALIZATION_FIELDS["ZoningRules"]),
        )
        self.assertEqual(
            set(serialize_contract_canonical("LayoutResult", layout_payload).keys()),
            set(CANONICAL_SERIALIZATION_FIELDS["LayoutResult"]),
        )
        self.assertEqual(
            set(serialize_contract_canonical("FeasibilityResult", feasibility_payload).keys()),
            set(CANONICAL_SERIALIZATION_FIELDS["FeasibilityResult"]),
        )
        self.assertEqual(
            set(serialize_contract_canonical("PipelineRun", pipeline_run_payload).keys()),
            set(CANONICAL_SERIALIZATION_FIELDS["PipelineRun"]),
        )

        with self.assertRaisesRegex(ValueError, "setbacks.rear"):
            validate_zoning_rules_for_layout(
                {
                    "parcel_id": "incomplete-zoning",
                    "district": "R-1",
                    "min_lot_size_sqft": 6000.0,
                    "max_units_per_acre": 5.0,
                    "setbacks": {"front": 25.0, "side": 8.0},
                }
            )

    def test_pipeline_run_output_validator_enforces_cross_stage_linkage(self) -> None:
        valid = validate_pipeline_run_output(
            {
                "run_id": "run-001",
                "parcel_id": "parcel-001",
                "zoning_result": {
                    "parcel_id": "parcel-001",
                    "district": "R-1",
                    "min_lot_size_sqft": 6000.0,
                    "max_units_per_acre": 5.0,
                    "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                },
                "layout_result": {
                    "layout_id": "layout-001",
                    "parcel_id": "parcel-001",
                    "unit_count": 4,
                    "road_length_ft": 120.0,
                    "lot_geometries": [],
                    "road_geometries": [],
                },
                "feasibility_result": {
                    "scenario_id": "scenario-001",
                    "layout_id": "layout-001",
                    "parcel_id": "parcel-001",
                    "units": 4,
                    "projected_revenue": 1200000.0,
                    "projected_cost": 900000.0,
                    "projected_profit": 300000.0,
                    "risk_score": 0.2,
                    "confidence": 0.9,
                },
                "timestamp": "2026-03-20T00:00:00Z",
            }
        )
        self.assertIsInstance(valid, PipelineRun)
        unsupported = validate_pipeline_run_output(
            {
                "run_id": "run-unsupported",
                "status": "unsupported",
                "parcel_id": "parcel-001",
                "zoning_result": {
                    "parcel_id": "parcel-001",
                    "district": "R-1",
                    "min_lot_size_sqft": 6000.0,
                    "max_units_per_acre": 5.0,
                    "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                },
                "layout_result": None,
                "feasibility_result": None,
                "timestamp": "2026-03-20T00:00:00Z",
            }
        )
        self.assertEqual(unsupported.status, "unsupported")
        with self.assertRaisesRegex(ValueError, "layout_result is required when status is completed"):
            validate_pipeline_run_output(
                {
                    "run_id": "run-completed-missing-layout",
                    "status": "completed",
                    "parcel_id": "parcel-001",
                    "zoning_result": {
                        "parcel_id": "parcel-001",
                        "district": "R-1",
                        "min_lot_size_sqft": 6000.0,
                        "max_units_per_acre": 5.0,
                        "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                    },
                    "layout_result": None,
                    "feasibility_result": None,
                    "timestamp": "2026-03-20T00:00:00Z",
                }
            )
        with self.assertRaisesRegex(ValueError, "must be null when status is not completed"):
            validate_pipeline_run_output(
                {
                    "run_id": "run-unsupported-with-layout",
                    "status": "unsupported",
                    "parcel_id": "parcel-001",
                    "zoning_result": {
                        "parcel_id": "parcel-001",
                        "district": "R-1",
                        "min_lot_size_sqft": 6000.0,
                        "max_units_per_acre": 5.0,
                        "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                    },
                    "layout_result": {
                        "layout_id": "layout-001",
                        "parcel_id": "parcel-001",
                        "unit_count": 4,
                        "road_length_ft": 120.0,
                        "lot_geometries": [],
                        "road_geometries": [],
                    },
                    "feasibility_result": None,
                    "timestamp": "2026-03-20T00:00:00Z",
                }
            )
        with self.assertRaisesRegex(ValueError, "zoning_result.parcel_id"):
            validate_pipeline_run_output(
                {
                    "run_id": "run-002",
                    "parcel_id": "parcel-001",
                    "zoning_result": {
                        "parcel_id": "parcel-999",
                        "district": "R-1",
                        "min_lot_size_sqft": 6000.0,
                        "max_units_per_acre": 5.0,
                        "setbacks": {"front": 25.0, "side": 8.0, "rear": 20.0},
                    },
                    "layout_result": {
                        "layout_id": "layout-001",
                        "parcel_id": "parcel-001",
                        "unit_count": 4,
                        "road_length_ft": 120.0,
                        "lot_geometries": [],
                        "road_geometries": [],
                    },
                    "feasibility_result": {
                        "scenario_id": "scenario-001",
                        "layout_id": "layout-001",
                        "parcel_id": "parcel-001",
                        "units": 4,
                        "projected_revenue": 1200000.0,
                        "projected_cost": 900000.0,
                        "projected_profit": 300000.0,
                        "risk_score": 0.2,
                        "confidence": 0.9,
                    },
                    "timestamp": "2026-03-20T00:00:00Z",
                }
            )

    def test_feasibility_contract_accepts_financial_fields_and_legacy_aliases(self) -> None:
        market_data = MarketData.model_validate(
            {
                "estimated_home_price": 480000.0,
                "cost_per_home": 260000.0,
                "road_cost_per_ft": 300.0,
            }
        )
        self.assertEqual(market_data.construction_cost_per_home, 260000.0)

        contract = FeasibilityResult.model_validate(
            {
                "scenario_id": "scenario-financial",
                "parcel_id": "parcel-financial",
                "layout_id": "layout-financial",
                "max_units": 35,
                "estimated_home_price": 480000.0,
                "construction_cost_per_home": 260000.0,
                "development_cost_total": 4200000.0,
                "projected_revenue": 16800000.0,
                "projected_cost": 14500000.0,
                "projected_profit": 2300000.0,
                "ROI": 0.158,
                "risk_score": 0.2,
                "confidence": 0.9,
            }
        )

        self.assertEqual(contract.units, 35)
        self.assertEqual(contract.max_units, 35)
        self.assertEqual(contract.projected_profit, 2300000.0)
        self.assertAlmostEqual(contract.ROI or 0.0, 0.158, places=6)
        self.assertEqual(contract.financial_summary["projected_cost"], 14500000.0)

    def test_scenario_evaluation_contract_supports_ranked_layouts(self) -> None:
        scenario = ScenarioEvaluation.model_validate(
            {
                "parcel_id": "parcel-rank",
                "layout_count": 1,
                "best_layout_id": "layout-rank",
                "best_roi": 0.58,
                "best_profit": 1240000.0,
                "best_units": 8,
                "layouts_ranked": [
                    {
                        "scenario_id": "scenario-rank",
                        "parcel_id": "parcel-rank",
                        "layout_id": "layout-rank",
                        "units": 8,
                        "estimated_home_price": 420000.0,
                        "construction_cost_per_home": 250000.0,
                        "development_cost_total": 120000.0,
                        "projected_revenue": 3360000.0,
                        "projected_cost": 2120000.0,
                        "projected_profit": 1240000.0,
                        "ROI": 0.58,
                        "profit_margin": 0.36,
                        "revenue_per_unit": 420000.0,
                        "cost_per_unit": 265000.0,
                        "rank": 1,
                        "risk_score": 0.2,
                        "confidence": 0.9,
                        "explanation": {
                            "primary_driver": "home_price",
                            "cost_breakdown": {"construction": 2000000.0, "development": 120000.0},
                            "revenue_breakdown": {"units": 8, "price_per_home": 420000.0},
                        },
                    }
                ],
            }
        )
        self.assertEqual(scenario.best_layout_id, "layout-rank")
        self.assertEqual(scenario.layouts_ranked[0].rank, 1)
        self.assertEqual(scenario.layouts_ranked[0].explanation.primary_driver, "home_price")

    def test_feasibility_result_rejects_unapproved_fields(self) -> None:
        with self.assertRaises(Exception):
            FeasibilityResult.model_validate(
                {
                    "scenario_id": "scenario-bad",
                    "parcel_id": "parcel-bad",
                    "layout_id": "layout-bad",
                    "units": 4,
                    "risk_score": 0.2,
                    "confidence": 0.9,
                    "unapproved_field": "drift",
                }
            )


if __name__ == "__main__":
    unittest.main()
