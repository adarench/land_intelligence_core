Layout Intelligence Roadmap
# Layout Intelligence Roadmap

This roadmap defines the system responsible for generating subdivision layouts and estimating buildable unit counts for parcels.

The layout intelligence layer converts zoning constraints and parcel geometry into an optimal subdivision design.

This roadmap implements **Milestone 3 from the master roadmap**.

Master roadmap reference:

bedrock/docs/land_feasibility_roadmap.md

---

# Purpose

Provide a reliable system capable of generating subdivision layouts for residential development.

The layout engine must determine:

- how many lots can be created
- where roads must be placed
- how lots should be arranged
- whether zoning constraints are satisfied

The system must produce layouts that approximate real subdivision designs.

---

# Scope

This roadmap includes:

- layout strategy generation
- candidate layout ranking
- road graph generation
- subdivision solving
- layout scoring
- layout service APIs
- layout benchmarking

This roadmap does NOT include:

- parcel ingestion
- zoning extraction
- financial feasibility modeling

Those are handled in other roadmaps.

---

# Role in the Feasibility Pipeline

Pipeline order:

parcel
→ zoning
→ layout
→ feasibility

Architecture position:

Parcel Service
      │
      ▼
Zoning Service
      │
      ▼
Layout Service
      │
      ▼
Feasibility Service

The layout engine transforms zoning constraints into subdivision layouts.

---

# Core Data Model

LayoutResult


LayoutResult
layout_id
units
lot_geometries
road_geometries
road_length
score
metadata


Inputs


Parcel
ZoningRules


---

# Layout Pipeline

Layout generation pipeline:

parcel geometry
+
zoning rules
↓

strategy generation

↓

candidate layout generation

↓

graph generation

↓

subdivision solver

↓

layout scoring

↓

best layout selection

---

# Zoning Constraint Enforcement

The layout generation layer is responsible for converting `ZoningRules` into hard subdivision parameters before candidate layouts are created. Zoning compliance is therefore a generation-time requirement, not a reporting-time check.

Constraint mapping from `ZoningRules` to layout engine parameters:

- `min_lot_size_sqft` maps to the minimum allowable lot area for every generated lot polygon.
- `setbacks.front`, `setbacks.side`, and `setbacks.rear` map to the minimum buildable envelope that each lot must support. In practice, these setbacks define a minimum effective lot depth and width so the engine does not generate lots that are geometrically too shallow or too narrow to host a compliant structure.
- `max_units_per_acre` maps to the parcel-level density cap. The engine must use this value to bound the total number of lots or dwelling units considered during candidate generation and ranking.

The following constraints must be enforced during subdivision generation:

- Minimum lot size: no generated lot may fall below `min_lot_size_sqft`.
- Setback-derived lot depth: lots must be deep enough to accommodate the front and rear setbacks and wide enough to remain usable after side setbacks are applied.
- Density limits: the candidate search space must not exceed the maximum dwelling-unit yield allowed by `max_units_per_acre`.

Constraint validation must occur during layout generation rather than after because post-processing invalid candidates is insufficient. If the engine generates unconstrained layouts first and filters them later, it wastes search effort on infeasible solutions, distorts candidate ranking, and can select road or lot patterns that can never be repaired into compliance. Enforcing zoning constraints inside the generator keeps the search space valid, improves solver stability, and ensures the returned `LayoutResult` reflects a layout that is already consistent with zoning rather than a noncompliant sketch that failed a later audit.

This enforcement model means zoning is not an annotation on the output. It is an input that bounds subdivision geometry, candidate count, and layout viability from the start of the search process.

---

# Milestone LI-1 — Layout Service Wrapper

Goal

Convert the existing layout engine into a stable service interface.

Existing system components include:

- strategy generator
- ranking system
- graph generator
- subdivision solver
- scoring system

Service layer must orchestrate these components.

API

POST /layout/search

Input


{
"parcel": Parcel,
"zoning": ZoningRules,
"max_candidates": number
}


Output


LayoutResult


Definition of Done

The layout engine can be invoked through a stable API endpoint.

---

# Milestone LI-2 — Layout Engine Stability

Goal

Ensure layout generation is reliable across different parcel shapes.

Key stability issues include:

- geometry edge cases
- solver failures
- constraint violations
- performance degradation

Required capabilities

- geometry preprocessing
- solver error handling
- constraint validation
- candidate fallback strategies

Definition of Done

20 real parcels successfully processed without runtime failure.

---

# Milestone LI-3 — Layout Strategy Expansion

Goal

Improve layout generation strategies.

Existing strategies may include:

- grid layouts
- spine road layouts
- cul-de-sac layouts

Strategy generation should support:

- multiple candidate layouts
- different road network patterns
- constraint-aware strategies

Definition of Done

At least 3 layout strategies implemented and tested.

---

# Milestone LI-4 — Layout Scoring System

Goal

Evaluate candidate layouts to select the best design.

Scoring factors

- number of units
- road length efficiency
- zoning compliance
- lot regularity
- layout compactness

Example scoring function


score =
density_score

efficiency_score

constraint_penalty


Definition of Done

Layout scoring reliably ranks candidate layouts.

---

# Milestone LI-5 — Layout Benchmarking

Goal

Measure layout performance using real parcel datasets.

Benchmark dataset

20 real parcels.

Metrics

- unit count
- road length
- runtime
- constraint violations

Evaluation output


layout_id
units
road_length
score


Definition of Done

Benchmark results produced for all test parcels.

---

# Milestone LI-6 — Layout API Stabilization

Goal

Expose layout intelligence through a stable API contract.

Endpoint

POST /layout/search

Output


{
"layout_id": "...",
"units": number,
"lot_geometries": [...],
"road_geometries": [...],
"road_length": number,
"score": number
}


Definition of Done

Layout API is stable and usable by the feasibility service.

---

# Success Metrics

The layout system is considered operational when:

- layouts generated successfully across 20 parcels
- runtime < 60 seconds per parcel
- zoning constraints satisfied
- layout scoring produces consistent rankings

---

# Agent Ownership

Primary Agents

layout_research_agent  
layout_code_agent

Supporting Agents

evaluation_agent  
orchestrator_agent

---

# Dependencies

Parcel foundation must exist.

Reference

bedrock/docs/roadmaps/parcel_foundation_roadmap.md

Zoning intelligence must exist.

Reference

bedrock/docs/roadmaps/zoning_intelligence_roadmap.md

---
