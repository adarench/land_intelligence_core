# Scoring Upgrade Evaluation Report

## Dataset
- Cases: 72
- Shared success cases used for ranking analysis: 71

## Performance Comparison (OLD vs NEW)
- Ranking Spearman rho: 0.096915
- Top-10 overlap: 30.00%
- Top-20 overlap: 30.00%
- Top-30 overlap: 40.00%

## Correlation Quality
- OLD score vs unit yield: 0.865112
- NEW score vs unit yield: 0.404221
- Delta: -0.460892
- OLD score vs (-road efficiency): 0.756760
- NEW score vs (-road efficiency): 0.686330
- Delta: -0.070430

## Stability Across Runs (NEW)
- Runs compared: 3
- Max layout_score span across 3 runs: 0.000000
- Mean layout_score span across 3 runs: 0.000000
- Max rank shift across runs: 0
- Spearman run1 vs run2: 1.000000
- Spearman run1 vs run3: 1.000000

## Runtime Impact
- OLD avg runtime: 0.071499 s
- NEW avg runtime: 0.118130 s
- Avg delta: 0.046631 s (65.22%)
- OLD p95 runtime: 0.090116 s
- NEW p95 runtime: 0.173763 s
- p95 delta: 0.083647 s (92.82%)

## Regressions / Edge Cases
- New failures added: 1 (layout_case_014:A)
- Strict worse-all cases (score down + units down + road efficiency worse): 51
- Large rank shifts (|shift|>=10): 53

## Recommendation
- Decision: **REJECT**
- Reasons: low_rank_consistency, unit_yield_correlation_dropped, road_efficiency_correlation_dropped, runtime_regression_over_15pct