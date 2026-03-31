# Repository Governance Rules

## Purpose

This document defines how the Land Feasibility Platform repositories MUST be used, modified, and coordinated.

This is a governance document. These rules are mandatory.

## Repository Naming Map

Governance names used in this document:

- `land_intelligence_core` = current workspace repo/runtime `bedrock`
- `land_intel_scraper` = current workspace repo/runtime `zoning_data_scraper`
- `CAD_generate` = current workspace repo/runtime `GIS_lot_layout_optimizer`

When there is any naming conflict, repository boundaries are determined by function, not by directory label.

## Pipeline Scope

The governed platform pipeline is:

`Parcel -> Zoning -> Layout -> Feasibility -> UI`

Control point:

- `land_intelligence_core` is the orchestrator and contract authority for the platform pipeline

Protected canonical contracts:

- `Parcel`
- `ZoningRules`
- `LayoutResult`
- `FeasibilityResult`

## 1. Repository Roles

### `land_intelligence_core`

Purpose:

- system orchestration
- pipeline execution sequencing
- canonical contract definitions
- API boundaries
- platform governance documentation
- run persistence and pipeline metadata
- feasibility evaluation

Belongs in this repo:

- Bedrock orchestration logic
- pipeline service and stage service coordination
- canonical contract models and validators
- API request/response enforcement
- documentation for system architecture, contracts, governance, and runtime state
- adapters that call domain repos without re-implementing their logic
- feasibility-stage implementation

Does NOT belong in this repo:

- raw zoning ingestion pipelines
- crawler, parser, extraction, or dataset publishing logic
- layout generation algorithms
- GIS solver internals
- duplicate parcel, zoning, or layout engines copied from other repos
- experimental outputs, local research artifacts, or generated design files

### `land_intel_scraper`

Purpose:

- zoning ingestion
- source discovery
- crawling
- parsing
- rule extraction
- zoning dataset publishing
- evidence and citation retention

Belongs in this repo:

- zoning source acquisition
- source normalization
- district and overlay dataset preparation
- zoning rule extraction and evidence lineage
- export artifacts that are explicit published zoning datasets
- helper services used to transform source material into governed zoning datasets

Does NOT belong in this repo:

- pipeline orchestration
- UI logic
- layout generation
- feasibility calculations
- Bedrock contract ownership
- planner-side runtime flow control

### `CAD_generate`

Purpose:

- layout engine
- GIS geometry processing
- subdivision and candidate generation
- layout search and export support

Belongs in this repo:

- layout search algorithms
- geometry and road network generation
- lot generation and scoring inputs required by the layout engine
- CAD and GIS export support for layout outputs
- planner-facing layout runtime implementation

Does NOT belong in this repo:

- canonical contract authority
- zoning ingestion or source crawling
- feasibility business logic
- orchestration of the full end-to-end pipeline
- duplicate zoning normalization logic beyond layout-local consumption needs

## 2. Source Of Truth Rules

Each pipeline layer has exactly one source of truth.

### Parcel

Source of truth:

- canonical `Parcel` contract and parcel-stage API behavior in `land_intelligence_core`

Current implementation authority:

- `bedrock/contracts/parcel.py`
- `bedrock/services/parcel_service.py`
- `bedrock/api/parcel_api.py`

Rule:

- all runtime parcel data crossing repo boundaries MUST be represented as canonical `Parcel`
- no other repo may define an alternative platform parcel contract

### Zoning

Source of truth split:

- zoning source acquisition, extraction, and published datasets: `land_intel_scraper`
- runtime canonical `ZoningRules` contract emitted to the pipeline: `land_intelligence_core`

Clarification:

- `land_intel_scraper` is the source of truth for zoning source material, normalized datasets, district/overlay assets, and extraction outputs
- `land_intelligence_core` is the source of truth for the runtime zoning contract presented to downstream services

Rule:

- `land_intel_scraper` MUST NOT own the pipeline contract
- `land_intelligence_core` MUST NOT duplicate scraper ingestion, crawling, parsing, or extraction pipelines
- `land_intelligence_core` may normalize scraper outputs into canonical `ZoningRules`, but it may not become a second zoning ingestion system

### Layout

Source of truth:

- `CAD_generate` ONLY

Rule:

- all layout generation logic, geometry generation, lot generation, and layout search behavior MUST originate from `CAD_generate`
- `land_intelligence_core` may call the layout engine and validate its outputs, but it must not implement a competing layout engine
- `land_intel_scraper` must not contain layout-generation logic

### Feasibility

Source of truth:

- `land_intelligence_core`

Current status:

- the designated source of truth is `land_intelligence_core`, but the final fully integrated production feasibility layer is currently not implemented as a completed platform stage

Rule:

- all official feasibility evaluation behavior belongs in `land_intelligence_core`
- no other repo may create an alternative platform feasibility contract or parallel production feasibility pipeline

## 3. Duplicate Logic Is Forbidden

The following are explicitly forbidden:

- duplicate parcel normalization pipelines across repos
- duplicate zoning rule extraction pipelines across repos
- duplicate layout engines across repos
- duplicate feasibility calculators across repos
- duplicated contract definitions for protected pipeline models
- cross-repo copy-paste implementations used to bypass proper interfaces

Required behavior:

- shared behavior MUST be consumed through governed contracts, adapters, published datasets, or service APIs
- if a repo needs functionality owned by another repo, it MUST call or consume the owned boundary rather than reimplement it

## 4. Commit Rules

Every commit MUST follow these rules:

- one concern per commit
- one pipeline stage or one governance concern per commit
- no mixed cross-repo commits that bundle unrelated changes
- no committing generated artifacts
- no committing experiment outputs
- no committing temporary notebooks, debug dumps, local caches, screenshots, benchmark scratch files, or exported geometry unless the artifact is an explicitly governed fixture

Commit messages MUST:

- be descriptive
- identify the affected stage or governance area
- make the change intent unambiguous

Required commit message pattern:

- `parcel: ...`
- `zoning: ...`
- `layout: ...`
- `feasibility: ...`
- `pipeline: ...`
- `contracts: ...`
- `docs: ...`

Forbidden commit patterns:

- vague messages such as `fix`, `updates`, `misc`, `wip`
- combining zoning and layout changes in one commit
- combining scraper changes and Bedrock orchestration changes in one commit unless the commit is exclusively a coordinated contract-interface update approved by the orchestrator

## 5. Branching Strategy

Branch rules:

- `main` = stable, validated branch
- all active development MUST occur on `dev` or task-specific feature branches
- direct commits to `main` are forbidden unless validation is complete and the merge is explicitly approved

Required branch types:

- `dev`
- `feature/<stage>-<task>`
- `fix/<stage>-<issue>`
- `docs/<topic>`

Validation rule:

- no branch may merge to `main` without passing the validation relevant to the affected pipeline stage

## 6. Agent Interaction Rules

Agent scope rules:

- agents MUST modify only their owned domain
- agents MUST NOT edit another domain repo to bypass ownership boundaries
- cross-domain changes require orchestrator approval before implementation
- any structural change MUST update documentation in the same work cycle

Ownership model:

- `land_intelligence_core` ownership: orchestration, contracts, APIs, feasibility, governance docs
- `land_intel_scraper` ownership: zoning ingestion, zoning datasets, evidence lineage
- `CAD_generate` ownership: layout generation, geometry engine, CAD/GIS export support

Cross-domain change rule:

- if a task changes a repo boundary, a service interface, or a contract handoff, the orchestrator must approve the change before code is merged

Documentation rule:

- any structural change, interface change, ownership change, or contract-adjacent change MUST update the relevant docs before completion

## 7. Contract Protection

The following contracts are protected:

- `Parcel`
- `ZoningRules`
- `LayoutResult`
- `FeasibilityResult`

Protection rules:

- no repo may redefine these contracts independently
- no field may be added, removed, renamed, or semantically repurposed without data governance approval
- no compatibility alias may be introduced casually
- no API may emit a shadow schema in place of the protected contract

Schema change policy:

- schema changes require explicit data governance review
- downstream consumers must be identified before approval
- migration or backward-compatibility behavior must be documented before implementation

If a schema change is not approved:

- it MUST NOT be merged

## 8. Coordination Rules Between Repositories

Required interaction model:

- `land_intelligence_core` orchestrates
- `land_intel_scraper` supplies zoning datasets and evidence-backed zoning inputs
- `CAD_generate` supplies layout generation

Forbidden interaction model:

- UI calling scraper internals directly for pipeline execution
- UI calling layout internals directly for official pipeline execution
- scraper importing layout runtime logic
- layout repo implementing its own official zoning ingestion path
- core repo silently embedding domain logic that belongs to another repo

## 9. Governance Enforcement

If a proposed change creates any of the following, it must be blocked:

- duplicate logic across repos
- contract drift
- unclear ownership
- mixed runtime paths for the same pipeline stage
- undocumented structural changes
- direct `main` branch edits without validation

When there is ambiguity:

- default to the stricter boundary
- escalate to orchestrator and data governance rather than inventing a local exception
