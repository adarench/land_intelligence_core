# Zoning Rules Governance Constraints (Current)

Last updated: 2026-03-19
Authority: `bedrock/contracts/zoning_rules.py`, `bedrock/contracts/validators.py`, `bedrock/services/zoning_rule_normalizer.py`, `bedrock/services/zoning_layout_translation.py`, `bedrock/api/zoning_api.py`

## Canonical Zoning Schema (Authoritative)

Schema identity:

- `schema_name = "ZoningRules"` (frozen)
- `schema_version = "1.0.0"` (frozen)

Required at canonical object level:

- `parcel_id: str`
- `district: str`

Required for zoning -> layout boundary:

- `district: str` and non-empty
- `min_lot_size_sqft: float` and `> 0`
- `max_units_per_acre: float` and `> 0`
- `setbacks.front: float` and `> 0`
- `setbacks.side: float` and `> 0`
- `setbacks.rear: float` and `> 0`

Optional canonical fields (must match type/unit constraints if present):

- `jurisdiction: str | null`
- `district_id: str | null`
- `description: str | null`
- `overlays: list[str]` (deduplicated, trimmed)
- `standards: list[DevelopmentStandard]`
- `setbacks: { front?: float>=0, side?: float>=0, rear?: float>=0 }`
- `height_limit_ft: float>=0 | null` (feet)
- `lot_coverage_max: float in [0,1] | null` (ratio, not percent)
- `min_frontage_ft: float>=0 | null` (feet)
- `road_right_of_way_ft: float>=0 | null` (feet)
- `allowed_uses: list[str]`
- `citations: list[str]`
- `metadata: EngineMetadata | null`

## Normalization and Naming Standards

- Canonical outbound names are fixed:
  - `district`
  - `height_limit_ft`
  - `lot_coverage_max`
  - `overlays`
  - `min_frontage_ft`
  - `allowed_uses`
- Input aliases are compatibility-only and must normalize into canonical names:
  - `code` / `zoning_district` -> `district`
  - `max_height` / `height_limit` / `max_building_height_ft` -> `height_limit_ft`
  - `max_lot_coverage` / `lot_coverage_limit` -> `lot_coverage_max`
  - `overlay` -> `overlays`
  - `min_lot_width_ft` -> `min_frontage_ft`
  - `allowed_use_types` -> `allowed_uses`
- `lot_coverage_max` percentages must normalize to fraction format (`45%` -> `0.45`).
- Area values must normalize to square feet where represented as lot-size constraints.

## Validation Rules (Enforced)

1. Structural strictness
- Unknown top-level keys are forbidden (`extra="forbid"`).
- Unknown nested keys in typed objects are forbidden.

2. Type and range checks
- Numeric zoning constraints reject invalid numbers.
- Setback values cannot be negative at schema level.
- Layout-safe required fields must be strictly positive.

3. Layout-compatibility gate
- `validate_zoning_rules_for_layout(...)` enforces required layout-safe fields.
- Any missing/invalid layout-safe field fails closed with `422`.

4. Parcel linkage
- `ZoningRules.parcel_id` must match the parcel being evaluated.

## Consumption Boundary (Layout Engine Alignment)

Fields consumed directly by layout runtime:

- `district`
- `min_lot_size_sqft`
- `max_units_per_acre`
- `setbacks.front`
- `setbacks.side`
- `setbacks.rear`
- `min_frontage_ft` (optional; derived if absent by translation layer)
- `road_right_of_way_ft` (optional; defaults to `32.0` if absent)

Governance constraint:

- No ad-hoc top-level zoning fields are permitted.
- Non-layout constraints must stay inside canonical optional fields (`standards`, `allowed_uses`, etc.) and must not bypass required layout-safe fields.
- Partial/ambiguous zoning cannot be promoted as layout-safe zoning.

## Typed Failure Model

- Missing required layout-safe fields: `incomplete_zoning_rules` (`422`)
- Present but invalid/out-of-bounds values: `invalid_zoning_rules` (`422`)
- No district match: `no_district_match` (`404`)
- Ambiguous district match: `ambiguous_district_match` (`409`)

## Example Valid Zoning Object

```json
{
  "schema_name": "ZoningRules",
  "schema_version": "1.0.0",
  "parcel_id": "parcel-001",
  "jurisdiction": "Salt Lake City",
  "district": "R-1-7000",
  "overlays": ["Hillside Overlay"],
  "setbacks": { "front": 20.0, "side": 8.0, "rear": 25.0 },
  "min_lot_size_sqft": 7000.0,
  "max_units_per_acre": 6.0,
  "height_limit_ft": 35.0,
  "lot_coverage_max": 0.45,
  "min_frontage_ft": 60.0,
  "road_right_of_way_ft": 32.0,
  "allowed_uses": ["single_family_residential"],
  "citations": ["SLC 21A.24"],
  "standards": [
    {
      "id": "R-1-7000:min_lot_size_sqft",
      "standard_type": "min_lot_size_sqft",
      "value": 7000,
      "units": "sqft"
    }
  ]
}
```

## Example Invalid Zoning Objects

Missing layout-safe field:

```json
{
  "parcel_id": "parcel-002",
  "district": "R-1",
  "max_units_per_acre": 5.0,
  "setbacks": { "front": 20.0, "side": 8.0, "rear": 20.0 }
}
```

Reason: missing `min_lot_size_sqft` -> `incomplete_zoning_rules`.

Invalid value:

```json
{
  "parcel_id": "parcel-003",
  "district": "R-1",
  "min_lot_size_sqft": 6000.0,
  "max_units_per_acre": 0.0,
  "setbacks": { "front": 20.0, "side": 8.0, "rear": 20.0 }
}
```

Reason: `max_units_per_acre` must be `> 0` for layout compatibility -> `invalid_zoning_rules`.

Unapproved ad-hoc field:

```json
{
  "parcel_id": "parcel-004",
  "district": "R-1",
  "min_lot_size_sqft": 6000.0,
  "max_units_per_acre": 5.0,
  "setbacks": { "front": 20.0, "side": 8.0, "rear": 20.0 },
  "lot_size_minimum": 6000.0
}
```

Reason: unknown top-level field (`lot_size_minimum`) is forbidden.

## Operational Rule

No partial zoning payload may be passed to layout search.
