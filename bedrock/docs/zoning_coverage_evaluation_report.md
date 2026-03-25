# Zoning Coverage Evaluation Report

Generated: 2026-03-20T20:40:14Z

## Metrics
- zoning_success_rate: 0.75
- pipeline_success_rate: 0.75
- district_coverage_rate: 0.75
- identified_district_count: 3
- pipeline_runtime_p95_s: 0.12666845800000104
- target_pipeline_success_rate: 0.9
- target_met: False

## Jurisdiction Metrics
- Draper: pipeline_success_rate=1.000, zoning_success_rate=1.000, district_coverage_rate=1.000, rule_completeness_rate=1.000
- Lehi: pipeline_success_rate=1.000, zoning_success_rate=1.000, district_coverage_rate=1.000, rule_completeness_rate=1.000
- Provo: pipeline_success_rate=0.000, zoning_success_rate=0.000, district_coverage_rate=0.000, rule_completeness_rate=0.000
- Salt Lake City: pipeline_success_rate=1.000, zoning_success_rate=1.000, district_coverage_rate=1.000, rule_completeness_rate=1.000

## Failure Breakdown By Stage
- zoning.lookup: 10

## Priority Missing Districts
- Provo::UNRESOLVED: 10

## Problematic Rule Definitions
- missing:min_lot_size_sqft: 10
- missing:max_units_per_acre: 10
- missing:setbacks.front: 10
- missing:setbacks.side: 10
- missing:setbacks.rear: 10
