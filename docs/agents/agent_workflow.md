# Bedrock Agent Workflow

## Purpose

This document defines the operational workflow for the Bedrock multi-agent development system.

The goal is to enable multiple AI agents to collaborate on the Land Feasibility Platform while maintaining system stability and development velocity.

This workflow describes how tasks are planned, executed, validated, and integrated across agents.

---

# System Overview

The Bedrock system functions as a structured engineering organization.

Agents operate within defined roles and coordinate through the Orchestrator Agent.

The system is organized into three layers:

Strategic Control  
Domain Execution  
Platform Maintenance

---

# Agent Layers

## Strategic Control Layer

These agents manage planning, coordination, and system governance.

orchestrator_agent  
plan_agent  
data_governance_agent  
evaluation_agent  

These agents determine what work should be done and ensure system integrity.

---

## Domain Execution Layer

These agents implement the core platform capabilities.

parcel_agent  
zoning_agent  
layout_research_agent  
layout_code_agent  
feasibility_agent  

These agents build the functional systems of the platform.

---

## Platform Maintenance Layer

These agents maintain the health and clarity of the system.

docs_agent  
refactor_agent  

They ensure the system remains understandable and maintainable as it evolves.

---

# Development Workflow

The system follows a structured workflow for implementing new capabilities.

---

## Step 1: Identify Milestone

The process begins with a roadmap milestone.

Example milestone:

"Automated parcel feasibility evaluation"

Source:

bedrock/docs/land_feasibility_roadmap.md

---

## Step 2: Generate Implementation Plan

The Plan Agent converts the milestone into structured tasks.

Example tasks:

parcel_agent  
→ normalize parcel geometries

zoning_agent  
→ extract zoning constraints

layout_code_agent  
→ expose layout generation API

feasibility_agent  
→ compute development ROI

The plan defines the required development sequence.

---

## Step 3: Orchestrate Work

The Orchestrator Agent assigns tasks to the appropriate agents.

Example workflow:

parcel_agent prepares parcel geometry  
zoning_agent extracts zoning constraints  
layout_code_agent generates candidate layouts  
feasibility_agent evaluates financial outcomes

The orchestrator ensures that dependencies are satisfied.

---

## Step 4: Domain Implementation

Domain agents implement the required capabilities.

Examples:

parcel_agent implements geometry normalization

zoning_agent implements zoning rule extraction

layout_code_agent implements layout search API

feasibility_agent implements financial feasibility model

Each agent works within its permitted repository boundaries.

---

## Step 5: Evaluation

The Evaluation Agent validates system improvements.

Examples:

compare layout generation strategies  
benchmark ranking models  
validate financial projections

Evaluation ensures system improvements are measurable.

---

## Step 6: Documentation

The Docs Agent updates system documentation to reflect new capabilities.

Documentation includes:

API interfaces  
architecture diagrams  
pipeline descriptions

Accurate documentation ensures the system remains understandable.

---

## Step 7: Refactoring

The Refactor Agent improves code quality and maintainability.

Examples:

remove duplicate code  
simplify complex modules  
improve test coverage

Refactoring ensures the platform remains scalable.

---

# Execution Model

In early development stages, agents may be run manually in parallel.

Example workflow:

Terminal 1 → orchestrator_agent  
Terminal 2 → plan_agent  
Terminal 3 → layout_code_agent  
Terminal 4 → layout_research_agent  
Terminal 5 → zoning_agent  
Terminal 6 → parcel_agent  

Each agent operates within its defined domain.

The human operator supervises execution.

---

# Agent Collaboration

Agents collaborate through shared system artifacts.

Examples:

parcel outputs used by zoning analysis  
zoning constraints used by layout engine  
layout results used by feasibility evaluation

These artifacts form the platform's data pipeline.

---

# Escalation Workflow

Agents must escalate when:

domain boundaries are violated  
schema changes affect multiple systems  
system architecture must change

Escalation path:

agent → orchestrator_agent → human operator

---

# Human Operator Role

The human operator acts as the executive decision-maker.

Responsibilities include:

approving architectural changes  
prioritizing roadmap milestones  
resolving cross-agent conflicts

Agents operate autonomously within defined boundaries but defer to human oversight when necessary.

---

# Guiding Principle

The Bedrock system is designed to combine agent autonomy with structured coordination.

Agents should accelerate development while maintaining system coherence.

A well-coordinated multi-agent system can significantly increase development velocity without sacrificing stability.
