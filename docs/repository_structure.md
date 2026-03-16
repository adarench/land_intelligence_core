# Repository Structure

## Purpose

This document defines the repository boundaries for the Land Feasibility Platform.

It separates:

- core production repositories
- research repositories
- legacy and archived repositories

## Core Production Repositories

### `bedrock/`

Role:

- platform orchestration
- canonical contracts
- public APIs
- milestone services
- platform documentation

Active use:

- production system documentation
- active pipeline orchestration
- contract governance

### `GIS_lot_layout_optimizer/`

Role:

- active layout runtime
- parcel and subdivision workflow support
- planner-facing runtime integration

Active use:

- production layout search and related runtime support

### `zoning_data_scraper/`

Role:

- zoning datasets
- zoning overlay lookup support
- rule normalization inputs
- evidence and extraction infrastructure

Active use:

- production zoning lookup support
- active data-source layer for Bedrock zoning services

## Research Repositories And Subsystems

### `GIS_lot_layout_optimizer/model_lab/`

Status:

- research system

Role:

- offline experiments
- model training
- layout search research

Allowed use:

- experimentation
- evaluation
- offline analysis

Not treated as:

- primary production API surface
- canonical contract authority

## Legacy / Archived Repositories

### `takeoff_archive/`

Status:

- experimental legacy archive

Allowed use:

- reference only
- historical review
- documentation context

Forbidden:

- production dependency
- active agent development target
- source of imports for active platform systems
- refactoring or extension without explicit human instruction

Required note:

`takeoff_archive` is frozen legacy research code and is not part of the active Land Feasibility Platform.

## Active Platform Scope

The active Land Feasibility Platform is built around:

- `bedrock`
- `GIS_lot_layout_optimizer`
- `model_lab`
- `zoning_data_scraper`

`takeoff_archive/` is outside the active platform boundary.
