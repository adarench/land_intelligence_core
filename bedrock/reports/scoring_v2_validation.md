# Scoring v2 Validation Report

Decision: **REJECT**
Reject reasons: yield_correlation_below_0_7, dominance_violations_present, severe_rank_instability, widespread_worse_all_cases

## Metric Summary
| Metric | Value | Target | Pass |
|---|---:|---:|:---:|
| Yield correlation Corr(score, units) | 0.404221 | >= 0.8 (hard floor 0.7) | ❌ |
| Efficiency correlation Corr(score, -road_eff_per_unit) | 0.686330 | >= 0.6 | ✅ |
| Composite value correlation Corr(score, value_proxy) | 0.632677 | higher is better | ✅ |
| Spearman vs baseline | 0.096915 | >= 0.7 | ❌ |
| Top-10 overlap | 30.0% | >= 60% | ❌ |
| Top-20 overlap | 30.0% | n/a | ✅ |
| Top-30 overlap | 40.0% | n/a | ✅ |

## Dominance / Sanity
- Dominance violations: **245** (hard fail if > 0)

## Ranking Stability
- Spearman: 0.096915
- Top-10/20/30 overlap: 30.0% / 30.0% / 40.0%

## Monotonicity Tests
- Units monotonicity tests: 23 cases, failures: 8, pass rate: 65.2%
- Efficiency monotonicity tests: 0 cases, failures: 0, pass rate: n/a

## Regression Signals
- Strictly worse-all cases: 51
- Large rank shifts (|shift| >= 10): 53

## Runtime Comparison
- Avg runtime: 0.071499s -> 0.118130s (65.2%)
- P95 runtime: 0.090116s -> 0.173763s (92.8%)

## Reasoning
- Reject triggered by multiple hard gates: low yield correlation, dominance violations, severe rank instability, and widespread worse-all regressions.
- Runtime regression also exceeds allowed +25% threshold.