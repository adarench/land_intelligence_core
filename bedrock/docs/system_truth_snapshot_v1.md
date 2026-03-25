# System Truth Snapshot v1

Status: CANONICAL
Effective date: 2026-03-20
Authority: This document overrides prior status snapshots, roadmap status summaries, and conflicting operational notes.
Evidence baseline:
- `bedrock/docs/system_truth_snapshot.md`
- `bedrock/docs/evaluation_truth_report.md`
- `bedrock/docs/contract_governance_report.md`
- live code in `bedrock/api/*`, `bedrock/services/*`, `bedrock/contracts/*`, `bedrock/apps/python-api/main.py`
- latest gate artifact `bedrock/benchmarks/po2_stabilization_latest.json`

## 1. System Overview (Truth)

The system is a working Bedrock pipeline with contract-enforced execution:
`parcel -> zoning -> layout -> feasibility`

What the system actually is today:
- End-to-end pipeline execution exists and runs successfully through `POST /pipeline/run`.
- Core domain contracts are implemented and actively enforced for `Parcel`, `ZoningRules`, `LayoutResult`, and `FeasibilityResult`.
- Successful pipeline runs are persisted to `bedrock/runs/*.json` and can be listed and retrieved through the Runs API.
- Experiment grouping exists on top of stored runs and computes aggregate metrics from persisted artifacts.
- Evaluation is operational and currently shows all-green results for the tested production slice.

What the system is not today:
- It is not a fully unified API surface. The composite Bedrock app does not expose `layout` or `evaluation` routers.
- It is not fully contract-governed at the orchestration support layer. `PipelineRun` is governed, but retrieval and analytics still depend on legacy persisted field names. `ExperimentRun` is not yet a governed canonical contract.
- It is not fully reproducible. Stored outputs exist, but execution provenance is incomplete.
- It is not broadly validated beyond the current measured slice.

## 2. Pipeline Status

Status: OPERATIONAL

Declaration:
- The pipeline is operational end-to-end.
- `POST /pipeline/run` executes parcel loading, zoning lookup, layout search, and feasibility evaluation in strict order.
- Successful pipeline executions persist a full run artifact.
- Latest measured evaluation shows 100% success for the current production matrices.

Scope boundary:
- Operational status is proven for the currently evaluated slice only.
- Current production validation scope is 21 cases across 3 jurisdictions (`Salt Lake City`, `Lehi`, `Draper`).
- This is not proof of broad jurisdiction coverage.

Operational notes:
- Pipeline API returns canonical `PipelineRun`.
- Internally, pipeline persistence still writes legacy raw keys: `parcel`, `zoning`, `layout`, `feasibility`.
- `pipeline_api` adapts that raw persisted shape into canonical `PipelineRun` fields: `parcel_id`, `zoning_result`, `layout_result`, `feasibility_result`.

## 3. API Surface Status

Status: INCOMPLETE

Actually exposed in the composite Bedrock app (`bedrock/apps/python-api/main.py`):
- `POST /parcel/load`
- parcel retrieval routes from `parcel_api`
- `POST /zoning/lookup`
- `POST /feasibility/evaluate`
- `POST /pipeline/run`
- `GET /runs`
- `GET /runs/{run_id}`
- `POST /experiments/create`
- `GET /experiments/{experiment_id}`

Implemented but not exposed by the composite Bedrock app:
- `POST /layout/search`
- `POST /evaluation/benchmark`

Declaration:
- The API surface is usable for pipeline execution, persisted-run browsing, and experiment creation.
- The API surface is not complete because the composite app omits layout and evaluation endpoints.

Gap:
- A client using only the main Bedrock app cannot access all implemented service surfaces.

## 4. Contract Status

Status: STABLE FOR CORE CONTRACTS, UNSTABLE FOR SUPPORT CONTRACTS

Stable core contracts:
- `Parcel`
- `ZoningRules`
- `LayoutResult`
- `FeasibilityResult`

Stable governed orchestration contract:
- `PipelineRun`

Unstable or incomplete support contracts:
- `PipelineExecutionResult`
  - internal service model only
  - not registry-governed as an external/support contract
- `ExperimentRun`
  - active API/service model exists
  - not yet governed in the contract registry
  - duplicate naming still exists with benchmark-local `ExperimentRun`

Contract reality gaps:
1. Persisted run storage shape is not the same as canonical `PipelineRun` shape.
   - stored keys: `parcel`, `zoning`, `layout`, `feasibility`
   - canonical API contract keys: `parcel_id`, `zoning_result`, `layout_result`, `feasibility_result`
2. `GET /runs/{run_id}` returns raw stored JSON without response-model validation.
3. `GET /experiments/{experiment_id}` returns raw stored JSON without response-model validation.
4. `PipelineRunEvaluationService` reads legacy persisted keys (`feasibility`) instead of canonical `PipelineRun.feasibility_result`.

Final declaration:
- Core pipeline contracts are stable.
- Public orchestration contract governance is not complete.

## 5. Evaluation Status

Status: TRUSTED FOR TESTED SLICE ONLY

Trusted facts:
- HTTP validation reports 10/10 full-chain success with contract conformance 1.0.
- PO-2 production matrix reports:
  - `pipeline_success_rate = 1.0`
  - `zoning_success_rate = 1.0`
  - `district_accuracy = 1.0`
  - `rule_completeness_rate = 1.0`
  - `fallback_usage_rate = 0.0`
  - `stub_zoning_rate = 0.0`
  - `synthetic_dataset_rate = 0.0`
  - `pipeline_runtime_seconds.p95 = 0.06525`
- Determinism spot checks in the evaluation report show identical outputs across repeated runs for the same input.

Limits on trust:
- Evaluation scope is narrow.
- Validation is concentrated in 3 production jurisdictions and expected-success cases.
- External deployed/network path behavior is not part of this proof.
- Benchmark baselines are still weakly governed and stale artifacts exist.

Final declaration:
- Evaluation is trusted as evidence that the current tested slice is healthy.
- Evaluation is not sufficient proof of broad MVP readiness.

## 6. Reproducibility Status

Status: INSUFFICIENT

What is sufficient today:
- Successful pipeline outputs are persisted.
- Run artifacts can be retrieved exactly as stored.
- Benchmark and gate artifacts are file-backed and timestamped.

What is insufficient:
- No immutable run manifest ties execution to exact git commit, dependency state, and environment fingerprint.
- Persisted `PipelineRun` artifacts do not include full execution provenance such as request controls and stage runtimes.
- Retrieval and analytics depend on legacy persisted keys rather than the canonical `PipelineRun` field set.
- Artifact freshness is not fully governed; stale benchmark artifacts still exist alongside current green artifacts.

Final declaration:
- Reproducibility is not strong enough to declare sufficient.

## 7. MVP Readiness Matrix

| Category | Status | Notes |
|---|---|---|
| Execution | GREEN | End-to-end pipeline is operational and succeeds on the current tested production slice. |
| Runtime (<60s) | GREEN | Current measured p95 is ~0.065s to ~0.077s for the tested slice, well below 60s. |
| Contracts | YELLOW | Core contracts and `PipelineRun` are stable; support contract governance and retrieval validation remain incomplete. |
| API Surface | YELLOW | Main Bedrock app is missing mounted layout and evaluation endpoints. |
| Reproducibility | RED | Stored outputs exist, but provenance and contract-aligned replay are incomplete. |
| Evaluation | YELLOW | Trusted for the measured slice only; not broad enough for full MVP confidence. |

## 8. UI Readiness Gate Decision

FINAL: UI BLOCKED

Exact reasons:
1. The composite Bedrock API surface is incomplete.
   - `POST /layout/search` and `POST /evaluation/benchmark` are implemented but not exposed by the main app.
2. Reproducibility is insufficient.
   - Run artifacts do not carry enough execution provenance for reliable UI-facing auditability.
3. Support contract governance is incomplete.
   - `ExperimentRun` is not governed canonically.
   - Runs retrieval returns raw stored payloads rather than validated canonical contracts.
4. Evaluation is scope-limited.
   - Current green status proves only the tested slice, not broad operating readiness.

Strict implication:
- UI integration work that depends on canonical Bedrock status should not begin until the API surface, reproducibility, and support-contract gaps are closed.
- UI archaeology and reuse analysis are allowed; product integration is not ready.

## 9. Next Actions (Strict)

1. Complete the composite Bedrock API surface by mounting `layout_api` and `evaluation_api` in `bedrock/apps/python-api/main.py`.
2. Enforce canonical retrieval contracts by validating `GET /runs/{run_id}` against `PipelineRun` and governing `ExperimentRun` with a single canonical definition.
3. Align persistence and analytics with canonical orchestration contracts, or explicitly version the legacy raw run-storage shape and add adapters everywhere it is consumed.
4. Add reproducibility provenance to run and evaluation artifacts: exact request controls, stage runtimes, git commit, and environment fingerprint.
5. Expand evaluation beyond the current 3-jurisdiction success slice before declaring MVP or UI readiness.
