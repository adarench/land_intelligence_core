# Contract Governance Report

Date: 2026-03-20
Scope: Core contracts (`Parcel`, `ZoningRules`, `LayoutResult`, `FeasibilityResult`) and orchestration artifacts (`PipelineRun`, `PipelineExecutionResult`, `ExperimentRun`).

## 1. Core Contracts Status

### Parcel
Status: stable

Evidence:
- Canonical model is explicit and strict (`extra=forbid` via `BedrockModel`): `bedrock/contracts/parcel.py`, `bedrock/contracts/base.py`.
- API boundary enforces canonical response model and validation: `bedrock/api/parcel_api.py`.
- Registry and service rules include `Parcel`: `bedrock/contracts/schema_registry.py`.

Issues:
- No blocking schema instability identified.

### ZoningRules
Status: stable

Evidence:
- Canonical model with alias normalization + typed constraints: `bedrock/contracts/zoning_rules.py`.
- Layout-safety gate enforced before downstream use: `validate_zoning_rules_for_layout(...)` in `bedrock/contracts/validators.py`.
- API boundary enforces canonical `ZoningRules` on success: `bedrock/api/zoning_api.py`.

Issues:
- Error payloads (`422`) are intentionally non-contract diagnostic objects (acceptable but not canonical contract payloads).

### LayoutResult
Status: stable

Evidence:
- Canonical model with compatibility aliases (`units`/`lot_count` -> `unit_count`) in `bedrock/contracts/layout_result.py`.
- API boundary validates request and output: `bedrock/api/layout_api.py`.
- Registry and serialization lock include `LayoutResult`: `bedrock/contracts/schema_registry.py`.

Issues:
- No blocking schema instability identified.

### FeasibilityResult
Status: stable

Evidence:
- Canonical model with explicit required financial fields and alias compatibility in `bedrock/contracts/feasibility_result.py`.
- Output validator enforces required non-null financial core: `validate_feasibility_result_output(...)` in `bedrock/contracts/validators.py`.
- API boundary returns canonical `FeasibilityResult`: `bedrock/api/feasibility_api.py`.

Issues:
- No blocking schema instability identified.

## 2. Orchestration Artifacts

### PipelineRun
Stability: stable and externally safe (API/UI/analytics)

Canonical structure (current):
- `schema_name: "PipelineRun"`
- `schema_version: "1.0.0"`
- `run_id: str`
- `parcel_id: str`
- `zoning_result: ZoningRules`
- `layout_result: LayoutResult`
- `feasibility_result: FeasibilityResult`
- `timestamp: str`

Evidence:
- Canonical model: `bedrock/contracts/pipeline_run.py`
- API response model: `bedrock/api/pipeline_api.py`
- Registry + service rule + serialization field lock: `bedrock/contracts/schema_registry.py`
- Cross-object linkage validation: `validate_pipeline_run_output(...)` in `bedrock/contracts/validators.py`
- Exact-field and service-rule enforcement tests: `tests/pipeline/test_pipeline_run.py`, `tests/contracts/test_contract_integrity.py`

### PipelineExecutionResult
Stability: stable for internal service use only; not yet governed as external contract

Current structure (internal):
- `run_id: str`
- `status: str`
- `feasibility: FeasibilityResult`

Evidence:
- Defined only in service layer: `bedrock/services/pipeline_service.py`
- Not registered in `SCHEMA_REGISTRY` or `EXTENSION_CONTRACT_REGISTRY`: `bedrock/contracts/schema_registry.py`

Assessment:
- Safe for internal orchestration return value.
- Not safe as a public/external contract until registry + serialization lock + explicit API boundary usage are added.

### ExperimentRun
Stability: unstable due duplicate definitions and missing governance

Observed definitions:
- API/service model: `bedrock/services/experiment_run_service.py` (`experiment_id`, `run_ids`, `config`, `metrics`)
- Benchmark-local dataclass: `bedrock/services/layout_benchmark_service.py` (`run_id`, `dataset`, `algorithm_variant`, `metrics`, `timestamp`)

Recommendation: refactor
- Keep `ExperimentRun` as a separate support contract (not core pipeline contract), but consolidate to one canonical definition.
- Current dual definition is a shadow-contract risk.

## 3. Contract Violations

1. Raw contract bypass at runs retrieval endpoint
- `GET /runs/{run_id}` returns unvalidated raw JSON (`dict`) from store.
- Evidence: `bedrock/api/runs_api.py` (`get_run` returns `dict`; `store.load_run(...)`).

2. Raw contract bypass at experiments retrieval endpoint
- `GET /experiments/{experiment_id}` returns unvalidated raw JSON (`dict`).
- Evidence: `bedrock/api/experiments_api.py` (`get_experiment` returns `dict`).

3. Evaluation service coupled to non-canonical persisted shape
- Reads `run["feasibility"]` instead of canonical `PipelineRun.feasibility_result`.
- Evidence: `bedrock/services/pipeline_run_evaluation_service.py`.

4. Shadow contract name collision (`PipelineRun`)
- Canonical `PipelineRun` contract exists in `bedrock/contracts/pipeline_run.py`.
- Separate telemetry dataclass `PipelineRun` exists in `bedrock/orchestration/pipeline_runner.py`.
- This is legal code-wise but governance-risky naming overlap.

5. Shadow contract duplication (`ExperimentRun`)
- Two incompatible `ExperimentRun` structures in different services.
- Evidence: `bedrock/services/experiment_run_service.py`, `bedrock/services/layout_benchmark_service.py`.

6. Governance coverage gap
- `PipelineExecutionResult` and `ExperimentRun` are not registry-governed with exact serialization locks.
- Evidence: `bedrock/contracts/schema_registry.py`.

## 4. Required Actions (Minimal Set)

1. Enforce response validation at retrieval APIs
- Change `GET /runs/{run_id}` to `response_model=PipelineRun` and validate loaded payload before return.
- Change `GET /experiments/{experiment_id}` to `response_model=<canonical ExperimentRun>` and validate loaded payload before return.

2. Govern support orchestration contracts
- Add `PipelineExecutionResult` and `ExperimentRun` as support contracts in contract governance (prefer `EXTENSION_CONTRACT_REGISTRY` unless promoted to canonical).
- Define canonical serialization fields and add drift checks.

3. Remove shadow contract duplication
- Keep one `ExperimentRun` contract definition and rename benchmark-local dataclass to avoid contract-name collision (or map it explicitly to canonical `ExperimentRun`).
- Rename telemetry `PipelineRun` in `pipeline_runner.py` (for example `PipelineTelemetryRun`) to avoid ambiguity with canonical `PipelineRun`.

4. Align analytics readers with canonical run schema
- Update `PipelineRunEvaluationService` to consume canonical `PipelineRun` fields (with explicit adapter for legacy persisted payloads).

5. Add guard tests
- Add exact-key tests for `PipelineExecutionResult` and `ExperimentRun` once governed.
- Add API contract tests for `GET /runs/{run_id}` and `GET /experiments/{experiment_id}` to fail on shape drift.

## Stability Conclusion

- Core contracts (`Parcel`, `ZoningRules`, `LayoutResult`, `FeasibilityResult`) are currently stable.
- `PipelineRun` is now stable and suitable for external use.
- `PipelineExecutionResult` is internal-stable but not externally governed.
- `ExperimentRun` is currently unstable due to duplicate definitions and should be refactored into a single governed support contract.
