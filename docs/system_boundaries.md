# System Boundaries

## Purpose

This document defines the system boundaries for the Land Feasibility Platform.

It separates:

- production systems
- research systems
- experimental archives

## Production Systems

Production systems are the active components used by the platform pipeline:

`parcel -> zoning -> layout -> feasibility`

Production systems:

- `bedrock`
- `GIS_lot_layout_optimizer`
- `zoning_data_scraper`

Production responsibilities:

- parcel ingestion and normalization
- zoning lookup and rule normalization
- layout search and geometry generation
- feasibility evaluation
- contracts, APIs, orchestration, and documentation

## Research Systems

Research systems support experimentation but are not themselves the production system boundary.

Research systems:

- `GIS_lot_layout_optimizer/model_lab`

Research responsibilities:

- offline experiments
- model training
- ranking and prior research
- evaluation datasets and benchmarks

Research systems may inform production changes, but they are not the production runtime boundary by default.

## Experimental Archives

Experimental archives are frozen historical repositories retained only for reference.

Experimental archives:

- `takeoff_archive`

Archive policy:

- reference only
- no production dependency
- no active agent development target
- no imports into active platform systems

Required note:

`takeoff_archive` is frozen legacy research code and is not part of the active Land Feasibility Platform.

## Boundary Rules

- Production architecture documentation must only describe active platform components.
- Research systems may be documented as adjacent or offline support systems.
- Experimental archives must be documented as non-production historical material.
- Archive code must not be treated as part of the active pipeline.
