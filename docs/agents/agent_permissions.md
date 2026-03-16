# Bedrock Agent Permissions

## Purpose

This document defines editing permissions and boundaries for all Bedrock agents.

The goal is to ensure that agents operate within clearly defined domains and cannot unintentionally modify systems owned by other agents.

This file functions as the governance layer of the Bedrock agent system.

---

# Permission Model

Agents operate under three permission levels:

### Read

Agent may read files but cannot modify them.

### Modify

Agent may edit files within the defined repository or directory.

### Restricted

Agent may not read or modify these areas without escalation.

---

# Global Restricted Areas

No agent may modify the following without explicit human approval:

takeoff_archive/  
bedrock/orchestration/agent_permissions.md  
bedrock/docs/land_feasibility_roadmap.md  

These files represent system governance and product direction.

---

# Orchestrator Agent

Permissions

Modify

bedrock/orchestration/  
bedrock/docs/  
bedrock/agents/  

Read

All repositories

Restricted

GIS_lot_layout_optimizer/  
model_lab/  
zoning_data_scraper/  

The orchestrator coordinates work but does not directly implement system features.

---

# Plan Agent

Permissions

Modify

bedrock/planning/  
bedrock/docs/  

Read

All repositories

Restricted

GIS_lot_layout_optimizer/  
model_lab/  
zoning_data_scraper/  

The plan agent creates execution plans but does not implement code.

---

# Data Governance Agent

Permissions

Modify

bedrock/contracts/  
bedrock/schemas/  
bedrock/docs/  

Read

All repositories

Restricted

GIS_lot_layout_optimizer/  
model_lab/  
zoning_data_scraper/  

The governance agent defines data contracts but does not modify domain logic.

---

# Evaluation Agent

Permissions

Modify

bedrock/evaluation/  
bedrock/experiments/  
bedrock/docs/  

Read

GIS_lot_layout_optimizer/  
model_lab/  
zoning_data_scraper/  

Restricted

Direct modification of production pipelines.

Evaluation must not alter system behavior.

---

# Parcel Agent

Permissions

Modify

GIS_lot_layout_optimizer/parcel/  
bedrock/parcel/  

Read

All repositories

Restricted

model_lab/  
zoning_data_scraper/  

Parcel processing must remain isolated to geometry preparation.

---

# Zoning Agent

Permissions

Modify

zoning_data_scraper/  
bedrock/zoning/  

Read

GIS_lot_layout_optimizer/  
model_lab/  

Restricted

takeoff_archive/  

The zoning agent owns zoning data ingestion and rule extraction.

---

# Layout Research Agent

Permissions

Modify

model_lab/  
bedrock/experiments/  

Read

GIS_lot_layout_optimizer/  

Restricted

Production layout engine modifications.

Research experiments must remain isolated from production systems.

---

# Layout Code Agent

Permissions

Modify

GIS_lot_layout_optimizer/  
bedrock/layout_service/  

Read

model_lab/  
zoning_data_scraper/  

Restricted

Experimental training pipelines.

Production layout services must remain stable.

---

# Feasibility Agent

Permissions

Modify

bedrock/feasibility/  
bedrock/financial_models/  

Read

GIS_lot_layout_optimizer/  
model_lab/  
zoning_data_scraper/  

Restricted

Core layout generation logic.

Feasibility systems consume layout results but do not modify layout algorithms.

---

# Docs Agent

Permissions

Modify

bedrock/docs/  
bedrock/architecture/  
bedrock/agents/  

Read

All repositories

Restricted

Direct modification of production systems.

Documentation must reflect system state but not alter system behavior.

---

# Refactor Agent

Permissions

Modify

GIS_lot_layout_optimizer/  
model_lab/  
bedrock/  

Read

All repositories

Restricted

Feature introduction.

Refactoring must not introduce new functionality.

---

# Escalation Rules

Agents must escalate to the Orchestrator Agent or the Human Operator when:

• modifying another agent's domain  
• introducing schema changes  
• modifying production APIs  
• altering system architecture  

Escalation ensures coordination across the agent system.

---

# Human Operator Authority

The Human Operator retains ultimate authority over:

system architecture  
roadmap direction  
repository structure  
agent permissions  

Agents must defer to the human operator for high-impact decisions.

---

# Guiding Principle

Clear boundaries enable multiple agents to operate safely within the same system.

Agent autonomy should increase productivity without compromising system stability.
