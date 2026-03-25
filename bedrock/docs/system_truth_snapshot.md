# System Truth Snapshot

Last updated: 2026-03-20
Canonical scope: this file is the single source of truth for current system state.
Evidence baseline:
- `bedrock/services/*`
- `bedrock/api/*`
- `bedrock/apps/python-api/main.py`
- `bedrock/benchmarks/po2_stabilization_latest.json` (`generated_at=2026-03-19T20:18:48.465683+00:00`)
- `bedrock/scripts/po2_stabilization_gate.py`

## 1) Current Pipeline Reality

Pipeline chain exists and is executable end-to-end in code:
`parcel -> zoning -> layout -> feasibility`

What is working in current measured scope:
- Orchestrator executes all four stages and persists run artifacts.
- PO-2 gate artifact shows for current matrix scope:
  - `po2_gate_passed=true`
  - `pipeline_success_rate=1.0`
  - `zoning_success_rate=1.0`
  - `failed_stage_counts={}`
  - `failed_error_counts={}`

Reality boundary:
- Current gate pass is measured on:
  - `representative_jurisdictions` (3 cases)
  - `supported_buildable_matrix` (18 cases)
- Total measured cases: `21`

## 2) System Components Status

| Component | Status | Reality |
|---|---|---|
| Parcel | implemented | Ingestion + normalization + persistence (`ParcelService`, `ParcelStore`, `/parcel/*`). |
| Zoning | implemented | Lookup + normalization + validation + translation integration in layout path. |
| Layout | implemented | Candidate generation + constraints + deterministic ranking + translation gate handling. |
| Feasibility | partial | Baseline deterministic financial evaluation is implemented; advanced calibration/sensitivity not baseline runtime requirement. |
| Pipeline orchestration | implemented | Stage-orchestrated execution with typed stage errors and run persistence. |
| Runs / experiments | implemented | Run persistence/listing and experiment grouping endpoints/services exist. |
| API surface | inconsistent | Endpoints exist, but mounting is split: standalone apps per service + composite app that currently excludes layout/evaluation routers. |
| UI | missing | No mounted product UI surface; repository contains backend python-api apps only. |

## 3) Known Gaps

1. API mounting inconsistency
- Composite app (`bedrock/apps/python-api/main.py`) mounts parcel/zoning/feasibility/pipeline/runs/experiments, but not layout/evaluation.

2. Representation observability gap
- Translation layer has usability classes (`layout_safe`, `partially_usable`, `non_usable`), but gate artifact does not publish class-level counts.

3. Coverage boundary
- Current GREEN gate evidence is bounded to two matrices (`representative_jurisdictions`, `supported_buildable_matrix`) in the latest PO-2 artifact.

4. Status artifact drift
- Several status/docs files reported stale blocked metrics from an older gate run and contradicted latest benchmark artifacts.

## 4) Removed / Invalid Claims

The following claims were invalid and are removed from canonical status:

1. "PO-2 gate is failing with pipeline_success_rate=0.13043478260869565 and zoning_success_rate=0.13043478260869565" as current truth.
- This is stale for current latest artifact.

2. "System progression is currently blocked by PO-2 gate failure" as current truth.
- Not true for latest PO-2 artifact (`po2_gate_passed=true`).

3. Broad completion claims outside measured scope.
- Any claim that current gate pass proves unsupported-jurisdiction or non-buildable-population readiness is invalid.

4. Claims that imply fallback-only success without measured fallback metrics.
- Fallback usage is not reported in current gate artifact; such completion claims are invalid.

## 5) Canonical Status Declaration

PO-2 status is: **PARTIAL**.

Justification:
- **GREEN** for the currently measured PO-2 matrices (`po2_gate_passed=true`, zero failed stages/errors, all primary rates 1.0).
- **Not full GREEN system-wide** because API mounting is inconsistent and gate evidence is explicitly scope-bounded (coverage and representation observability gaps remain).
