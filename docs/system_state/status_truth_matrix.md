# Canonical Status Truth Matrix

As of: `2026-03-19`
Owner: `data_governance_agent`
Scope: roadmap + architecture + system-state reconciliation

## Contradiction Register

1. Zoning readiness mismatch
- claim A: architecture says minimum milestone jurisdiction coverage includes Salt Lake City, Lehi, Draper (`docs/architecture/pipeline_overview.md:191`)
- claim B: latest stabilization gate report shows broad zoning failure (`district_resolution_rate=0.1304`, `zoning_success_rate=0.1304`) (`bedrock/benchmarks/po2_stabilization_latest.json:3043`, `bedrock/benchmarks/po2_stabilization_latest.json:3078`)
- canonical truth: coverage is `implemented` but `validated` remains failing against gate thresholds

2. Validation scope mismatch
- claim A: milestone tracker says canonical endpoint validation passed for foundation scope (`bedrock/status/milestone_tracker.md:34`)
- claim B: latest gate report blocks progression (`po2_gate_passed=false`, `pipeline_success_rate=0.1304`, `zoning_success_rate=0.1304`) (`bedrock/benchmarks/po2_stabilization_latest.json:3037`, `bedrock/benchmarks/po2_stabilization_latest.json:3075`, `bedrock/benchmarks/po2_stabilization_latest.json:3078`)
- canonical truth: foundation-level endpoint existence is `implemented`; stabilization validation is `pending/gated`

## Canonical Status Matrix

Status vocabulary:
- `implemented`: code/API path exists and executes in at least one controlled path
- `validated`: passes runtime integration evidence for intended scope
- `pending`: not yet validated or explicitly incomplete

| Milestone | Implemented | Validated | Pending | Canonical Evidence |
|---|---|---|---|---|
| M1 Real Parcel Inputs | yes | partial | yes | `bedrock/api/parcel_api.py`, `docs/roadmaps/milestone_1_real_parcel_inputs.md:9-16` |
| M2 Zoning Intelligence | yes | no (gate-failing) | yes | `bedrock/api/zoning_api.py`, `bedrock/benchmarks/po2_stabilization_latest.json:3078` |
| M3 Layout Feasibility | yes | partial | yes | `bedrock/api/layout_api.py`, `bedrock/benchmarks/po2_stabilization_latest.json:3063`, `bedrock/benchmarks/po2_stabilization_latest.json:3075` |
| M4 Economic Feasibility | yes | partial | yes | `bedrock/api/feasibility_api.py`, `bedrock/benchmarks/po2_stabilization_latest.json:3063`, `bedrock/benchmarks/po2_stabilization_latest.json:3075` |
| PO-2 Pipeline Execution | yes | no (gate-failing) | yes | `bedrock/api/pipeline_api.py`, `bedrock/benchmarks/po2_stabilization_latest.json:3037` |
| WORK_BATCH_001 Foundation | yes | yes (foundation scope) | yes (stabilization gate blocked) | `bedrock/status/milestone_tracker.md:11-12`, `bedrock/benchmarks/po2_stabilization_latest.json:3037` |

## Governance Update Policy

1. Single source of truth
- `docs/system_state/status_truth_matrix.md` is the canonical status authority.
- Any conflicting claim in roadmap/architecture/system-state docs is non-authoritative until reconciled here.

2. Required status tuple
- Every milestone status update must include all three fields:
- `implemented`
- `validated`
- `pending_items` (explicit list)

3. Evidence standard
- Every status change must cite:
- at least one code path
- at least one test/report artifact (or explicit note: `validation pending`)

4. Change control
- Do not mark `validated` unless integration evidence exists for the declared scope.
- If runtime evidence regresses, status must be downgraded in this matrix first, then propagated.

5. Propagation rule
- Update order is fixed:
- `docs/system_state/status_truth_matrix.md` first
- then affected roadmap files
- then architecture/system-state summaries

6. Allowed labels
- Only use `implemented`, `validated`, `pending` in summary status rows.
- Avoid ambiguous labels such as `under development`, `foundation complete`, or `operational` without the tuple above.
