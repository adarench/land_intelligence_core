# Governance Decision: Partial/Conditional Zoning Representation

Decision date: `2026-03-19`  
Owner: `data_governance_agent`  
Scope: `Parcel -> ZoningRules -> LayoutResult -> FeasibilityResult`

## Decision

Approved option: **A**

Keep canonical contracts unchanged and add an internal zoning usability classification + translation layer.

Rationale:

- avoids breaking the current canonical pipeline contract chain
- preserves strict deterministic `ZoningRules` requirements for layout compatibility
- allows richer real-world zoning interpretation internally without leaking unstable fields into public contracts
- aligns with current fail-closed zoning boundary behavior (`incomplete_zoning_rules`, `invalid_zoning_rules`)

Option B (additive extension/versioning) is deferred until there is a proven consumer need to expose partial-rule payloads across service boundaries.

## Option Evaluation

### Option A (approved)

- keep `ZoningRules` (`schema_version=1.0.0`) unchanged
- add internal classification and translation interfaces
- emit canonical `ZoningRules` only when rules are layout-usable
- for partial/non-usable cases, fail closed at zoning boundary with deterministic error payload

Risk: partial zoning richness is not externally portable yet.  
Mitigation: preserve extracted context internally in classification objects and logs.

### Option B (deferred)

- introduce additive extension contract or `ZoningRules` compat version for partial rules
- requires migration plan, validator branching, API compatibility matrix expansion, and gate updates

Risk: schema drift and downstream ambiguity if introduced prematurely.

## Approved Representation Pattern

### 1) Canonical public handoff (unchanged)

`ZoningRules` remains the only approved cross-service zoning contract for layout/feasibility.

Usable output condition:

- district present
- `min_lot_size_sqft > 0`
- `max_units_per_acre > 0`
- `setbacks.front|side|rear > 0`

### 2) Internal usability classification (new governance interface)

Allowed internal interface name:

- `ZoningUsabilityAssessment` (internal only; not canonical pipeline contract)

Approved internal shape:

```json
{
  "parcel_id": "string",
  "jurisdiction": "string|null",
  "district": "string|null",
  "usability_class": "usable|partial|non_usable",
  "blocking_reasons": ["string"],
  "missing_fields": ["string"],
  "derived_assumptions": ["string"],
  "source_evidence_refs": ["string"],
  "translated_zoning_rules": { "...canonical ZoningRules..." },
  "assessment_version": "1.0.0"
}
```

Field rules:

- `translated_zoning_rules` is required only for `usability_class = usable`
- `blocking_reasons` is required for `partial` and `non_usable`
- `missing_fields` must use canonical field paths (example: `setbacks.front`)
- `assessment_version` is internal interface versioning, independent of `ZoningRules.schema_version`

### 3) Approved semantic classes

- `usable`: complete and valid for layout; may continue to layout stage as canonical `ZoningRules`
- `partial`: interpretable but not sufficient for deterministic layout constraints; must not enter layout stage
- `non_usable`: no reliable zoning interpretation for deterministic subdivision; must not enter layout stage

## Backward Compatibility Requirements

1. Canonical contract freeze

- no removals/renames/type changes for fields in `Parcel`, `ZoningRules`, `LayoutResult`, `FeasibilityResult`
- no canonical field alias expansion that changes outbound serialization

2. API stability

- `POST /zoning/lookup` success payload stays canonical `ZoningRules`
- `POST /zoning/lookup` partial/non-usable cases continue fail-closed with stable error codes:
  - `incomplete_zoning_rules`
  - `invalid_zoning_rules`

3. Validator stability

- `validate_zoning_rules_for_layout(...)` remains strict and unchanged in behavior
- no validator change may allow partial/non-usable zoning to pass into layout

4. Serialization invariants

- canonical serialization keys remain locked by registry field lists
- no additional zoning usability fields are allowed in canonical `ZoningRules` payloads

## Migration Policy (if Option B is later escalated)

Option B requires explicit governance escalation with:

1. extension contract registration in `EXTENSION_CONTRACT_REGISTRY`
2. additive-only schema design (no canonical break)
3. API compatibility strategy (`Accept-Version` or explicit endpoint split)
4. staged rollout:
  - dual-write internal classification + extension
  - dual-validate
  - gate verification
  - consumer opt-in cutover
5. deprecation policy with dated removal windows

No Option B rollout is allowed without a signed migration plan.

## Forbidden Changes

- adding `usable/partial/non_usable` fields directly into canonical `ZoningRules`
- changing `ZoningRules.schema_version` from `1.0.0` without migration policy approval
- allowing layout service to consume non-usable/partial zoning payloads
- replacing deterministic fail-closed zoning errors with silent fallback success payloads

## Acceptance Checks For Orchestrator/Evaluation Gates

1. Contract safety checks

- canonical contract tests must pass with unchanged public schemas
- no drift in canonical serialization keys

2. Boundary behavior checks

- `usable` assessments produce canonical `ZoningRules` and may proceed
- `partial` and `non_usable` assessments do not proceed to layout stage
- zoning boundary emits deterministic error shape for blocked cases

3. Stabilization gate checks (existing)

- use `bedrock/benchmarks/po2_stabilization_latest.json`
- required thresholds remain:
  - `pipeline_success >= 0.80`
  - `zoning_success >= 0.90`

4. New governance telemetry checks (required once classification is implemented)

- `zoning_usability_assessment_coverage = 100%`
- `partial_or_non_usable_leak_rate_to_layout = 0%`
- `classification_determinism = stable for identical inputs`
