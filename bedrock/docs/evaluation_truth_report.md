# Evaluation Truth Report

Generated: 2026-03-20 (America/Denver)

## 1. What is actually validated today

### Active evaluation suites

- HTTP full-chain harness: `scripts/http_validation.py` / `bedrock/scripts/run_http_validation.py`
  - Endpoints: `POST /parcel/load`, `/zoning/lookup`, `/layout/search`, `/feasibility/evaluate`, `/pipeline/run`
  - Dataset: `bedrock/benchmarks/http_validation_config.json`
  - Current suite: `http_full_chain_validation_v4_expanded`
  - Case volume: **60** total
    - **54 expected-success** production-like cases
    - **6 expected-failure** negative/edge cases
  - Jurisdictions represented: `Salt Lake City`, `Lehi`, `Draper`, `Ogden`, `Provo`

- PO-2 stabilization matrix: `bedrock/scripts/po2_stabilization_gate.py`
  - Production matrix: 21 cases (3 + 18)
  - Fixture matrix: 20 synthetic cases

### What is measured

- Chain success (all stages pass) and per-endpoint success rates
- Contract conformance per endpoint
- Runtime (avg/p95/max, warm)
- Error-class aggregation and unexpected 500 tracking
- Determinism checks (configured case subset, repeated runs)
- Expected-outcome matching (did expected success/failure match observed)

## 2. What is NOT validated

- Broad jurisdiction coverage across the full intended product footprint (still limited to a small set).
- External deployment/network path behavior (harness runs in-process via TestClient).
- Concurrency/load saturation behavior.
- Full real-parcel/APN diversity at production scale.
- Economic-ground-truth validity for feasibility values (contract-valid != market-validated).

## 3. Runtime reality

Measured from latest expanded HTTP run (`http_validation_report.json`):

- Total cases: 60
- Chain success: 57/60 = **0.95**
- Expected-outcome match: 57/60 = **0.95**
- Unexpected 500s: **0**
- `/pipeline/run` success: **0.95** (57/60)
- `/pipeline/run` warm p95: **0.13497s**
- `<60s` end-to-end objective: **met by large margin**

Per-endpoint warm p95:

- `/parcel/load`: 0.00189s
- `/zoning/lookup`: 0.08597s
- `/layout/search`: 0.05870s
- `/feasibility/evaluate`: 0.00164s
- `/pipeline/run`: 0.13497s

## 4. Reproducibility status

### What is persisted

- Expanded suite config and cases in `bedrock/benchmarks/http_validation_config.json`
- Latest report in `bedrock/benchmarks/http_validation_report.json`
- Baseline refreshed to current suite in `bedrock/benchmarks/http_validation_baseline.json`
- PO-2 latest and history persisted under `bedrock/benchmarks/po2_stabilization_*.json*`
- Stored pipeline artifacts in `bedrock/runs/*.json`

### What remains missing

- Strong run provenance bundle (commit SHA + environment + dependency lock fingerprint) attached to each report.
- CI-enforced multi-run determinism across a larger case subset (currently configured subset = 5 cases).
- Explicit anti-flake policy for occasional transient harness runs.

## 5. False confidence risks

- **Coverage-limited green:** success is high on expanded suite, but still not full production geography.
- **Fallback/normalization masking:** three negative cases expected to fail currently pass:
  - `edge-unsupported-ogden-001`
  - `edge-unsupported-provo-002`
  - `edge-invalid-self-intersection-003`
  This indicates current behavior can normalize/fallback inputs into successful runs, which may hide strict validation gaps.
- **Fixture/prod interpretation risk:** PO-2 includes fixture matrix; consumers must read production matrix metrics separately.

## 6. MVP Readiness Assessment

Binary assessment for current validated scope:

- execution: **yes**
  - `/pipeline/run` stable and safe-failing on observed negative paths; no crashes/500s in latest expanded run.

- runtime: **yes**
  - Runtime objectives are comfortably met for tested cases.

- reproducibility: **partial / leaning yes**
  - Baseline drift was corrected by re-baselining to current suite.
  - Remaining gaps: stronger provenance + wider deterministic replay coverage.

- coverage: **no (improved, not complete)**
  - Coverage materially improved (60 cases, mixed geometry, explicit negatives), but still not broad enough to claim full operational readiness.

---

## Expanded Dataset Description

`http_full_chain_validation_v4_expanded` contains:

- 54 positive production-like cases
  - Generated deterministically from runtime-viable anchors in `Salt Lake City`, `Lehi`, `Draper`
  - Geometry variations: rectangle, trapezoid, concave notch, irregular polygon
  - Size/translation variants for shape and placement diversity

- 6 negative/edge cases
  - Unsupported jurisdictions (Ogden/Provo)
  - Invalid geometry type (LineString)
  - Tiny parcel (zoning invalid)
  - Too-few-points polygon ring
  - Self-intersection polygon (currently passes unexpectedly)

## Determinism (Automated)

Determinism is now automated in HTTP harness config:

- Config section: `determinism`
- Current setup: 5 selected cases × 3 runs each
- Compared fields:
  - status code
  - zoning district
  - layout unit count
  - layout road length
  - feasibility ROI
  - feasibility projected profit
- Latest result: **5/5 consistent (1.0)**

## Confidence Statement (Updated)

Confidence is now **meaningfully improved** versus prior state:

- Broader case coverage (60 vs 10)
- Explicit edge/failure-path validation
- Automated determinism checks
- Baseline drift corrected to current suite

Current trust level:

- **Trusted for the expanded evaluated slice** (5 jurisdictions, mixed geometry, explicit edge cases, deterministic subset).
- **Not yet trusted for full production generalization** until jurisdiction breadth, external-runtime validation, and larger deterministic replay coverage are expanded further.
