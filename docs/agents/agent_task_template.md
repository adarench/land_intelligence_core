# Bedrock Agent Task Template

## Purpose

This template is used to initialize work sessions with Bedrock agents.

It ensures that every agent session begins with the correct:

• role definition  
• system context  
• roadmap alignment  
• task definition  

Agents should always review their role definition before beginning work.

---

# Agent Identity

Agent Name:

<AGENT_NAME>

Role Definition:

bedrock/agents/<AGENT_NAME>.md

Before beginning work, load and follow the instructions defined in your role document.

Your behavior, permissions, and responsibilities are defined there.

---

# System Context

The project you are working on is the **Land Feasibility Platform**.

The platform pipeline is:

Parcel  
→ Zoning  
→ Layout  
→ Feasibility  
→ User Interface

You are responsible for work within your assigned domain.

You must respect agent boundaries defined in:

bedrock/orchestration/agent_permissions.md

---

# Relevant Roadmaps

Primary Roadmap:

bedrock/docs/land_feasibility_roadmap.md

Domain Roadmap:

<DOMAIN_ROADMAP_PATH>

Your task should advance the progress of the relevant roadmap.

---

# Current System State

Relevant repositories:

GIS_lot_layout_optimizer  
model_lab  
zoning_data_scraper  
bedrock  

You should inspect relevant code before proposing changes.

If context is missing, ask for clarification.

---

# Task Description

<INSERT_TASK_DESCRIPTION>

Examples:

Implement parcel normalization pipeline.

Expose layout generation API endpoint.

Train improved layout ranking model.

Evaluate layout strategy performance.

---

# Constraints

You must:

• follow agent permissions
• avoid modifying restricted systems
• maintain API compatibility
• document major changes

If your work requires violating these constraints, escalate to the orchestrator.

---

# Expected Output

You should produce one or more of the following:

• code changes  
• architecture proposal  
• experiment plan  
• evaluation report  
• documentation updates  

Clearly explain your reasoning.

---

# Definition of Done

The task is complete when:

• the implementation matches the task description  
• code compiles and runs  
• existing functionality is not broken  
• documentation is updated if needed  

---

# Escalation

Escalate if:

• required code is missing
• permissions block the task
• architectural decisions are required

Escalate to:

orchestrator_agent  
or the human operator.

---

# Begin

Review your role definition.

Review the relevant roadmap.

Then propose an implementation plan before making changes.
