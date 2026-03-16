# Bedrock Agent Daily Loop

## Purpose

This document defines the daily operating cycle for the Bedrock multi-agent development system.

The goal is to enable one human operator to coordinate multiple AI agents working on the Land Feasibility Platform.

The loop ensures that:

• development remains aligned with the roadmap  
• agents do not conflict with each other  
• progress is continuously evaluated  
• system quality remains high  

This workflow allows a small team (or a single operator) to manage a large number of coordinated development agents.

---

# System Overview

The Bedrock system operates as a structured engineering organization.

Agents work within defined domains while coordinating through the orchestrator and roadmap system.

Each development cycle consists of:

Planning  
Execution  
Evaluation  
Stabilization

---

# Daily Operating Loop

## Step 1 — Load System Context

At the start of each session:

Review:

bedrock/docs/land_feasibility_roadmap.md

and relevant domain roadmaps.

Confirm the next milestone to advance.

Example milestones:

Parcel normalization pipeline  
Zoning rule extraction  
Layout generation API  
Feasibility evaluation pipeline  

This ensures development stays aligned with business goals.

---

## Step 2 — Generate Execution Plan

Start a session with the Plan Agent.

The Plan Agent should:

• read the roadmap  
• identify the next milestone  
• break it into tasks  

Example output:

parcel_agent  
→ implement parcel geometry normalization

zoning_agent  
→ implement zoning rule extraction

layout_code_agent  
→ expose layout generation API

feasibility_agent  
→ implement financial evaluation pipeline

This becomes the task queue for the session.

---

## Step 3 — Launch Domain Agents

Run domain agents to execute tasks.

Typical agents running in parallel:

parcel_agent  
zoning_agent  
layout_code_agent  
layout_research_agent  
feasibility_agent  

Agents may be run in separate terminals or sessions.

Example:

Terminal 1 → parcel_agent  
Terminal 2 → zoning_agent  
Terminal 3 → layout_code_agent  
Terminal 4 → layout_research_agent  
Terminal 5 → feasibility_agent  

Each agent receives tasks using the task template.

---

## Step 4 — Monitor Progress

The human operator supervises execution.

Responsibilities include:

• reviewing agent outputs  
• resolving agent questions  
• approving code changes  
• ensuring agents follow permissions  

Agents may escalate when:

system context is missing  
permissions block work  
architecture decisions are required

---

## Step 5 — Evaluate Improvements

After implementation tasks complete, run the Evaluation Agent.

Evaluation tasks may include:

• comparing layout strategy performance  
• validating ranking models  
• verifying financial projections  

Evaluation ensures improvements are measurable and not regressions.

---

## Step 6 — Update Documentation

Run the Docs Agent to ensure that documentation reflects system changes.

Documentation updates may include:

architecture diagrams  
API documentation  
pipeline descriptions  

Accurate documentation ensures the system remains understandable.

---

## Step 7 — Refactor and Stabilize

Run the Refactor Agent to improve code quality.

Tasks may include:

removing duplicate code  
simplifying complex modules  
improving test coverage  

Refactoring prevents long-term codebase degradation.

---

# Weekly Loop

At least once per week:

Run the Plan Agent and Orchestrator Agent to reassess roadmap progress.

Review:

milestones completed  
system architecture stability  
next roadmap priorities  

This keeps development aligned with the long-term platform strategy.

---

# Human Operator Role

The human operator acts as the executive decision-maker for the system.

Responsibilities include:

• approving architectural changes  
• prioritizing roadmap milestones  
• resolving cross-agent conflicts  
• supervising agent outputs  

The human operator ensures the system evolves in the correct direction.

---

# Escalation Workflow

Agents escalate issues using the following chain:

Agent  
→ Orchestrator Agent  
→ Human Operator

Escalation occurs when:

permissions are violated  
architecture must change  
system assumptions break

---

# Guiding Principle

The Bedrock agent system is designed to combine:

agent autonomy  
structured coordination  
human oversight

This allows a small team to achieve extremely high development velocity without losing system coherence.
