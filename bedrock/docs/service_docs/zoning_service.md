# Zoning Service

Last updated: 2026-03-17
Code: `bedrock/services/zoning_service.py`, `bedrock/api/zoning_api.py`, `bedrock/services/zoning_rule_normalizer.py`

## Purpose

Resolve parcel geometry to zoning district and normalize zoning attributes into layout-safe canonical `ZoningRules`.

## Inputs

Service (`ZoningService.lookup`):
- `parcel: Parcel`

API:
- `POST /zoning/lookup` body:
  - `parcel: Parcel`

## Outputs

- `ZoningLookupResult` internally (`jurisdiction`, `district`, `rules`).
- API returns `rules` as canonical `ZoningRules`.

## API Endpoints

- `POST /zoning/lookup` -> `ZoningRules`
  - `404` `no_district_match` when no jurisdiction/district match.
  - `409` `ambiguous_district_match` when multiple matches.
  - `422` `incomplete_zoning_rules` when layout-critical fields missing.
  - `422` `invalid_zoning_rules` when fields exist but violate layout-safe constraints.

## Dependencies

- Overlay lookup + district matching (`zoning_data_scraper.services.zoning_overlay.lookup_zoning_district`)
- Scraper normalization (`zoning_data_scraper.services.rule_normalization.normalize_zoning_rules`)
- Canonical rules normalization (`bedrock/services/zoning_rule_normalizer.py`)
- Contract/layout-safe validators (`bedrock/contracts/validators.py`, `validate_zoning_rules(...)`)

## Known Limitations

- Data completeness depends on available zoning datasets.
- Jurisdiction fallback defaults are used for some missing values; not all jurisdictions have robust fallback coverage.
- Jurisdictions with artifact-only layers can fail closed as `incomplete_zoning_rules`.
- Non-ideal raw data can still result in 422 even after sanitization.

## System State vs Roadmap

- Complete:
  - Overlay-backed lookup, deterministic normalization, strict typed validation and errors.
- In progress:
  - Dataset hardening and broader clean-feature coverage.
- Missing:
  - Uniform, full-fidelity zoning coverage without fallback dependence.
