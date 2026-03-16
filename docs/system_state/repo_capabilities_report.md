# Repository Capabilities Report

## SYSTEM OVERVIEW

The workspace contains three repository roots:

1. `GIS_lot_layout_optimizer`
2. `takeoff_archive`
3. `zoning_data_scraper`

These repos are adjacent parts of a broader land feasibility intelligence stack, but they are not yet wired together as a single platform.

- `GIS_lot_layout_optimizer` is the parcel-first subdivision feasibility and layout optimization product.
- `zoning_data_scraper` is the zoning evidence ingestion, normalization, and retrieval backbone.
- `takeoff_archive` is nominally a construction takeoff system, but it also contains a substantial parcel reconstruction and parcel-level feasibility engine that overlaps with land intelligence concerns.

Observed system pattern:

- Parcel geometry intake and spatial planning live in `GIS_lot_layout_optimizer`.
- Regulatory evidence intake and citation-oriented querying live in `zoning_data_scraper`.
- Document-derived spatial intelligence, OCR, parcel reconstruction, and some parcel feasibility analysis live in `takeoff_archive`.

The overall platform shape is clear, but the architecture is still repository-centric rather than platform-centric. Cross-repo integration appears conceptual, not implemented as stable code dependencies.

## REPOSITORY BREAKDOWN

### 1. `GIS_lot_layout_optimizer`

Primary purpose:

- Utah parcel lookup, normalization, subdivision layout generation, yield optimization, and planner UI for land feasibility.

Repository structure:

- `ai_subdivision/`: core geometry and subdivision engine.
- `apps/python-api/`: FastAPI backend, parcel services, persistence, export generation.
- `apps/web/`: Next.js planner, map intake, run history, proxy API routes.
- `model_lab/`: experimental ML, graph priors, strategy models, offline training datasets.

Core modules:

- Parcel intake and normalization:
  - `apps/python-api/services/arcgis_parcel_client.py`
  - `apps/python-api/services/parcel_service.py`
  - `apps/python-api/services/parcel_adapter.py`
  - `apps/web/services/parcels/*`
- Optimization and planning:
  - `apps/python-api/services/optimization_service.py`
  - `ai_subdivision/subdivision.py`
  - `ai_subdivision/yield_optimizer.py`
  - `ai_subdivision/street_network.py`
  - `apps/python-api/services/concept_instruction_service.py`
- Alternate production layout engine:
  - `apps/python-api/services/layout_engine/*`
- Persistence and exports:
  - `apps/python-api/services/persistence.py`
  - `ai_subdivision/dxf_export.py`
  - `ai_subdivision/geojson_export.py`
  - `ai_subdivision/geometry.py`
- Planner and map UI:
  - `apps/web/app/*`
  - `apps/web/components/*`
  - `apps/web/lib/*`

Data pipelines:

- Parcel search pipeline:
  - Web map or APN request
  - Next.js API proxy
  - FastAPI parcel service
  - PostGIS lookup if available
  - UGRC ArcGIS fallback if cache misses
  - JSON fallback if live lookup fails
  - Canonical parcel record returned to planner
- Optimization pipeline:
  - Parcel geometry
  - Constraint resolution
  - optional concept-text interpretation
  - topology candidate generation
  - subdivision simulation
  - candidate ranking by lot yield
  - result persistence
  - DXF/STEP/GeoJSON export generation
- Offline model pipeline:
  - simulated or historical layout data
  - feature extraction
  - graph prior / strategy ranker training
  - model artifact output in `model_lab/models/`

Algorithms:

- Heuristic street network generation across topology families such as parallel, spine, loop, and cul-de-sac in `ai_subdivision`, plus more experimental generators in `apps/python-api/services/layout_engine/graph_generator.py`.
- Yield optimization by enumerating candidate street networks and selecting the layout with best lot count in `ai_subdivision/yield_optimizer.py`.
- Topology-agnostic lot subdivision via road buffering, buildable strip creation, strip slicing, overlap deduplication, and validation in `apps/python-api/services/layout_engine/lot_subdivision.py`.
- Prior-guided layout search using graph priors in `apps/python-api/services/layout_engine/layout_search.py`.
- Lightweight prompt parsing and topology preference inference in `apps/python-api/services/concept_instruction_service.py`.

Unused or experimental code:

- `model_lab/` is explicitly experimental and isolated from production by repository rules.
- `model_lab/experiments/` contains 11 offline experiment files and reports.
- `apps/python-api/services/layout_engine/*` looks like a second production-oriented layout engine beside `ai_subdivision`, which suggests active overlap rather than a fully consolidated runtime path.
- `.pgdata-subdivision/`, JSON caches, and export folders indicate local-dev persistence mixed into the repo.

Assessment:

- This is the most productized land-feasibility repo in the workspace.
- The main technical risk is duplication between the legacy `ai_subdivision` engine and the newer `services/layout_engine` stack.

### 2. `takeoff_archive`

Primary purpose:

- Originally a deterministic construction takeoff and provenance graph system.
- Now also contains a large document-derived parcel reconstruction, lot assignment, and parcel intelligence subsystem relevant to land feasibility.

Repository structure:

- `src/`: canonical Construction World Graph library and CLI tools.
- `demo-ui/`: Next.js demo app with the active perception, derivation, parcel, OCR, scale, and trust-gate pipelines.
- `project-level-docs/`: extensive design notes, audits, phase reports, and validation documents.
- `training_data/`: offline datasets and trained model artifacts.
- `demo-output/`, `.pipeline-cache`, corpora, and raw/labeled data directories: accumulated run artifacts and datasets.

Core modules:

- Canonical graph and provenance model:
  - `src/graph.ts`
  - `src/types.ts`
  - `src/derivation.ts`
  - `src/validation.ts`
  - `src/query-engine.ts`
  - `src/context-graph.ts`
- Runtime pipeline orchestration:
  - `demo-ui/src/pipeline/canonical-pipeline.ts`
  - `demo-ui/src/pipeline/run-manager.ts`
  - `demo-ui/src/pipeline/pdf-preflight.ts`
- Geometry and derivation:
  - `demo-ui/src/derive/*`
  - `demo-ui/src/asset/*`
  - `demo-ui/src/group/*`
  - `demo-ui/src/takeoff/*`
  - `demo-ui/src/scale/*`
- Parcel intelligence:
  - `demo-ui/src/engine/parcel/*`
  - `demo-ui/src/engine/geometry/*`
  - `demo-ui/src/engine/analysis/*`
  - `demo-ui/src/vision/*`
- UI, validation, and exports:
  - `demo-ui/src/workspace/*`
  - `demo-ui/src/explorer/*`
  - `demo-ui/src/validation/*`
  - `demo-ui/src/export/*`
  - `demo-ui/src/capability-gate/*`

Data pipelines:

- Core takeoff pipeline:
  - PDF input
  - scale inference
  - vector geometry extraction
  - OCR lot detection
  - binding with priors
  - derivation of quantities, takeoffs, and rollups
  - trust/capability gating
- Parcel intelligence pipeline:
  - Project graph
  - sheet geometry normalization
  - OCR token ingress
  - planar graph / face generation
  - lot token classification and assignment
  - parcel clustering and reconciliation
  - utility assignment
  - feasibility, cost, and risk analysis
- Human review pipeline in root library:
  - TLGB / OGB proposal generation
  - manual review CLI
  - accepted proposal commit into canonical world graph

Algorithms:

- PDF vector extraction, OCR-assisted geometry recovery, and sheet classification.
- Binding using sheet priors, style evidence, and rule-based defaults.
- Semantic grouping and asset assembly by connectivity and style similarity.
- Parcel solving with spatial indexing, face containment, OCR token filtering, parcel clustering, and provenance backfill in `demo-ui/src/engine/parcel/parcel-engine.ts`.
- Scale inference with training and inference code in `demo-ui/src/scale/*`.
- Deterministic world-graph derivation and provenance tracing in `src/*`.

Unused or experimental code:

- This repo has a large amount of archival and experimental surface area.
- `demo-ui/scripts/` contains at least 72 debug, diagnose, audit, forensic, test, demo, or temporary scripts.
- `project-level-docs/` contains 157 markdown files, many of them phase notes and one-off audits.
- Multiple derivation generations remain in tree simultaneously:
  - legacy takeoffs
  - Phase 4
  - Phase 5
  - Phase 5.1 / 5.3
- `training_data/` and several Python training scripts indicate active ML experimentation.
- `demo-output/`, `expanded-plat-corpus*`, and `.pipeline-cache` are run-history and research artifact stores, not clean product boundaries.

Assessment:

- This is the most algorithmically ambitious repo.
- It contains both a reusable provenance/graph core and a large experimental runtime that now overlaps directly with land parcel intelligence.
- It should be treated as both an archive and an R&D lab, not a single cohesive application.

### 3. `zoning_data_scraper`

Primary purpose:

- Statewide Utah zoning source discovery, crawl, parse, rules-based extraction, coverage auditing, and evidence-first query application.

Repository structure:

- `src/zoning/`: main package.
- `apps/cli/`, `apps/workers/`: app scaffolding, though most logic currently lives in package modules and CLI.
- `docs/`: architecture, schema, parsing strategy, operations.
- `tests/`: pipeline and export verification.
- `zoning_data*`, `zoning_dataset*`, `exports/`: accumulated crawl and export outputs.

Core modules:

- CLI and orchestration:
  - `src/zoning/cli.py`
  - `src/zoning/services.py`
- Source discovery and crawl:
  - `src/zoning/sources/discover/*`
  - `src/zoning/sources/crawlers/*`
- Parsing:
  - `src/zoning/sources/parsers/*`
- Extraction:
  - `src/zoning/sources/extractors/*`
- Persistence and storage:
  - `src/zoning/models/entities.py`
  - `src/zoning/db/*`
  - `src/zoning/storage/*`
- Exports and audits:
  - `src/zoning/exports/*`
  - `src/zoning/audit/*`
- Retrieval app:
  - `src/zoning/webapp/*`

Data pipelines:

- Jurisdiction discovery pipeline:
  - seed jurisdictions
  - seed sources
  - crawl source URLs
  - persist artifacts and content hashes
- Parsing pipeline:
  - raw artifact read from local or Azure blob storage
  - type-specific parser
  - normalized document and section output
  - supersession/change tracking
- Extraction pipeline:
  - latest jurisdiction documents
  - rules-based fact extraction
  - structured rows for districts, overlays, standards, permissions, approvals, citations
  - extraction run bookkeeping and conflict flagging
- Retrieval pipeline:
  - question tokenization and term expansion
  - evidence retrieval from structured facts and sections
  - extractive answer composition with provenance cards

Algorithms:

- Source-specific crawling including ArcGIS, HTML, and PDF source types.
- Rules-based extraction using regex patterns for zoning districts, overlays, setbacks, density, parking, lot size, lot width, height, and approval paths in `src/zoning/sources/extractors/rules.py`.
- Evidence retrieval using keyword expansion, stopword filtering, lightweight jurisdiction inference, result scoring, and deduplication in `src/zoning/webapp/retrieval.py`.
- Change detection using content hashes and document supersession in `src/zoning/services.py`.

Unused or experimental code:

- The repo contains 23 top-level dataset/export directories such as `zoning_data_priority_v2` through `zoning_data_priority_v9b` and `zoning_dataset_v1` through `zoning_dataset_v8_sample`.
- Those directories are valuable artifacts, but they also indicate repeated dataset iteration inside the code repository.
- `apps/cli` and `apps/workers` exist as directories, but the package CLI and service layer are the real operational center of gravity.
- The optional LLM synthesis layer in the web app is additive, not central to the current architecture.

Assessment:

- This is the clearest ingestion-oriented backend in the workspace.
- It has a stronger data-lifecycle model than the other repos, but its extraction layer is still primarily heuristic and regex driven.

## MODULE CAPABILITIES

### Parcel and geometry intelligence

- Strongest in `GIS_lot_layout_optimizer` for authoritative parcel retrieval and subdivision optimization.
- Strongest in `takeoff_archive/demo-ui` for document-derived parcel reconstruction, lot binding, and parcel-level intelligence from plan sets.
- `zoning_data_scraper` has source-layer geospatial awareness through ArcGIS sources and GIS-first export logic, but not parcel planning logic.

### Optimization and simulation

- `GIS_lot_layout_optimizer` is the only repo with a coherent lot-yield optimization loop and exportable subdivision outputs.
- `takeoff_archive` has parcel feasibility analysis, infrastructure cost, and development risk computation, but not street-layout optimization.
- `zoning_data_scraper` does not optimize; it supplies regulatory evidence that should constrain optimization.

### Document and evidence processing

- `zoning_data_scraper` is strongest for evidence retention, normalized documents, structured extraction, and provenance-first retrieval.
- `takeoff_archive` is strongest for PDF vector extraction, OCR, and document-derived geometry inference.
- `GIS_lot_layout_optimizer` is weakest here; it mostly consumes parcel APIs rather than processing planning documents.

### Provenance and auditability

- `takeoff_archive` root graph library is the strongest canonical provenance model.
- `zoning_data_scraper` is strong at source, artifact, citation, and change lineage.
- `GIS_lot_layout_optimizer` tracks run history and exports, but its provenance model is shallower than the other two repos.

### ML and offline experimentation

- `GIS_lot_layout_optimizer/model_lab` explores graph priors and strategy ranking.
- `takeoff_archive/demo-ui/src/scale/training` and `training_data/` support scale and structural ML experiments.
- `zoning_data_scraper` is mostly rules-based today, with lighter optional LLM scoring or synthesis layers.

## CROSS REPO DEPENDENCIES

Current code-level dependency state:

- No direct package imports across repository boundaries were found.
- The three repos are operationally independent.

Conceptual dependency map:

- `zoning_data_scraper` should be an upstream regulatory evidence provider for `GIS_lot_layout_optimizer`.
  - Today, `GIS_lot_layout_optimizer` stores `zoningCode` on parcels, but zoning constraints are still mostly local planner inputs rather than evidence-driven rules.
- `takeoff_archive` parcel intelligence overlaps with both other repos.
  - It can infer parcels and lot metadata from plan documents.
  - That capability could feed parcel feasibility or validation into `GIS_lot_layout_optimizer`.
  - Its provenance and evidence graph concepts are also compatible with `zoning_data_scraper`.
- `GIS_lot_layout_optimizer` and `takeoff_archive` both reason about parcels, lots, road geometry, and feasibility, but through separate models and runtimes.

Practical interpretation:

- The workspace behaves like three parallel capability silos.
- Integration opportunities are obvious, but stable shared contracts are mostly missing.

## DUPLICATED SYSTEMS

### 1. ArcGIS and public source ingestion

- `GIS_lot_layout_optimizer` has a custom ArcGIS parcel client for UGRC parcel services.
- `zoning_data_scraper` has ArcGIS crawling and parsing for zoning map sources.

Duplication:

- Source-type handling
- HTTP access patterns
- ArcGIS response normalization
- public-record crawl concerns such as staleness and source metadata

### 2. Parcel modeling and parcel intelligence

- `GIS_lot_layout_optimizer` defines parcel normalization, caching, and optimization-time parcel constraints.
- `takeoff_archive` defines parcel entities, parcel reconstruction, parcel reconciliation, utility assignment, feasibility, and risk analysis.

Duplication:

- parcel identity concepts
- lot numbering and parcel coverage reasoning
- parcel-level feasibility outputs
- spatial relationship logic

### 3. Geometry and topology utilities

- `GIS_lot_layout_optimizer` has Shapely-based road, lot, and polygon operations.
- `takeoff_archive` has TS geometry normalization, polygonization, segment graph, spatial indexing, and parcel face extraction.

Duplication:

- planar topology work
- polygon/face generation
- spatial indexing
- geometry normalization and filtering

Note:

- The languages differ, so literal code reuse is limited, but the duplicated abstractions are real.

### 4. Provenance and evidence tracking

- `takeoff_archive` has the Construction World Graph, assumptions, quantities, trace, and explanation systems.
- `zoning_data_scraper` has artifacts, documents, sections, citations, extraction runs, and evidence retrieval cards.

Duplication:

- evidence identity
- source attribution
- trace/explanation mechanics
- change and review concepts

### 5. Offline training and validation harnesses

- `GIS_lot_layout_optimizer/model_lab` trains graph priors and rankers.
- `takeoff_archive` trains scale and structural models and has many evaluation scripts.

Duplication:

- dataset generation
- feature extraction
- model artifact management
- ad hoc validation scripts

### 6. Local file-backed caches and run artifacts

- `GIS_lot_layout_optimizer` keeps JSON fallback stores and exports in-repo.
- `takeoff_archive` keeps `.pipeline-cache`, `demo-output`, and corpora in-repo.
- `zoning_data_scraper` keeps repeated dataset snapshots and exports in-repo.

Duplication:

- local artifact stores
- weakly governed run-history persistence
- versioned data snapshots mixed with source code

## ARCHITECTURE RECOMMENDATIONS

### Shared libraries that should exist

### 1. Canonical land-intelligence data contracts

Extract a shared contract layer first, even if code remains polyglot.

Should define:

- parcel
- lot
- zoning district
- development standard
- evidence citation
- artifact/document/section
- feasibility result
- optimization constraint

Reason:

- This is the highest-value extraction because all three repos already model overlapping entities differently.

### 2. Shared provenance and evidence model

Candidate inputs:

- `takeoff_archive/src/*` graph and derivation concepts
- `zoning_data_scraper` artifact/document/citation lineage

Should cover:

- evidence IDs
- source references
- section/page pointers
- assumptions
- derivation chains
- review decisions

Reason:

- Provenance exists in two repos already, but not as a platform-wide contract.

### 3. Shared public-source adapter library

Should unify:

- ArcGIS source handling
- HTTP retry and hashing
- content normalization metadata
- staleness/change detection

Likely extraction sources:

- `GIS_lot_layout_optimizer/apps/python-api/services/arcgis_parcel_client.py`
- `zoning_data_scraper/src/zoning/sources/crawlers/*`
- `zoning_data_scraper/src/zoning/sources/parsers/arcgis.py`

### 4. Shared parcel intelligence library

Should consolidate:

- parcel identity
- lot ID normalization
- parcel coverage metrics
- feasibility summary schemas
- utility-to-lot assignment contracts

Likely extraction sources:

- `GIS_lot_layout_optimizer` parcel normalization and planner models
- `takeoff_archive/demo-ui/src/engine/parcel/*`

### 5. Shared validation and trust-gate framework

Should unify:

- evaluation reports
- confidence tiers
- gate pass/degraded states
- dataset split metadata
- run summaries

Likely extraction sources:

- `takeoff_archive/demo-ui/src/capability-gate/*`
- `takeoff_archive/demo-ui/src/validation/*`
- `GIS_lot_layout_optimizer/model_lab/*`

### Missing architectural layers

### 1. Platform integration layer

Missing today:

- a service or contract layer that composes parcel, zoning, and document intelligence into one feasibility decision flow

Effect:

- `GIS_lot_layout_optimizer` cannot automatically consume evidence-grade zoning constraints.
- `zoning_data_scraper` cannot directly drive optimization inputs.
- `takeoff_archive` parcel insights remain isolated in an archive/R&D repo.

### 2. Canonical domain model for land feasibility

Missing today:

- a stable platform model joining parcel geometry, zoning constraints, infrastructure/takeoff signals, and optimization outputs

Effect:

- Each repo carries its own parcel/evidence schema.

### 3. Workflow orchestration layer

Missing today:

- job orchestration for crawl -> parse -> extract -> parcel intelligence -> feasibility -> optimization

Effect:

- Pipelines are CLI- and script-driven instead of platform-driven.

### 4. Artifact and dataset governance layer

Missing today:

- separation between source code, experiment outputs, caches, and benchmark datasets

Effect:

- all repos accumulate operational artifacts in-tree
- active code and historical data are hard to separate

### 5. Unified observability and lineage layer

Missing today:

- consistent run IDs, stage timing, error taxonomies, and lineage across repos

Effect:

- each repo reports progress differently
- cross-system debugging would be difficult

### 6. Policy/rules execution layer

Missing today:

- a proper zoning-policy execution layer that converts extracted standards into machine-usable feasibility constraints

Effect:

- zoning evidence is searchable, but not yet a first-class computational constraint system for planning.

### 7. Shared experiment-to-production promotion layer

Missing today:

- formal model registry, feature versioning, promotion criteria, and deprecation path for experimental engines

Effect:

- `GIS_lot_layout_optimizer` has both `ai_subdivision` and `services/layout_engine`
- `takeoff_archive` carries multiple pipeline generations at once
- promotion status is inferred from docs and naming instead of enforced by architecture

## CONCLUSION

The workspace already contains most of the ingredients for a serious land feasibility intelligence platform:

- authoritative parcel intake and optimization
- zoning evidence ingestion and retrieval
- document-derived parcel reconstruction and feasibility analysis
- provenance-heavy graph and audit concepts

What it lacks is not capability breadth, but architectural consolidation.

The clearest next platform step is not refactoring individual repos. It is defining the shared domain contracts and integration layers that let these repos act as one system instead of three parallel capability silos.
