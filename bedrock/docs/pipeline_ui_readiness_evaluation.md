# Pipeline UI Readiness Evaluation

Dataset size (real production parcels from po2_stabilization_latest.json): 21

## Executive Summary
- pipeline success rate: 1.000
- major blockers: jurisdiction coverage missing for Provo/Murray/Cottonwood Heights in this dataset
- readiness for UI usage: NO

## Failure Breakdown Table
| Stage | Count | % |
|---|---:|---:|
| Zoning | 0 | 0.0% |
| Layout | 0 | 0.0% |
| Feasibility | 0 | 0.0% |

## Jurisdiction Performance Table
| Jurisdiction | Parcels | Success % | Primary Failure |
|---|---:|---:|---|
| Salt Lake City | 7 | 100.0% | None |
| Provo | 0 | 0.0% | None |
| Lehi | 7 | 100.0% | None |
| Draper | 7 | 100.0% | None |
| Murray | 0 | 0.0% | None |
| Cottonwood Heights | 0 | 0.0% | None |
| Other | 0 | 0.0% | None |

## Layout Failure Analysis
- No layout failures observed in this dataset/run.

## Zoning Integrity Checks
- stub fallback usage count: 0
- jurisdiction fallback usage count: 0
- non-normalized district names: 0
- incomplete ZoningRules objects: 0

## Runtime Metrics
- avg runtime: 0.1084s
- p95 runtime: 0.0996s
- max runtime: 0.9297s
- target (<60s): met

## Regression Check
- success rate delta vs previous run: 0.0
- failures fixed: []
- new failures introduced: []
