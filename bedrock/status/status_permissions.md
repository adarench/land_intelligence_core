# Status Permissions Policy

This document defines ownership and update permissions for the Bedrock status system.

## Canonical Status Paths

System layer:

- `bedrock/status/system_state.md`
- `bedrock/status/current_batch.md`
- `bedrock/status/milestone_tracker.md`

Agent layer:

- `bedrock/status/agents/<agent_name>_status.md`

## Ownership Rules

### `orchestrator_agent`

May update:

- `bedrock/status/system_state.md`
- `bedrock/status/current_batch.md`
- `bedrock/status/milestone_tracker.md`
- `bedrock/status/status_permissions.md`
- `bedrock/status/status_update_guidelines.md`
- `bedrock/status/README.md`

Must not update agent-owned status files unless performing an explicit administrative recovery task.

### Execution agents

Each execution agent may update only its own file:

- `parcel_agent` -> `bedrock/status/agents/parcel_agent_status.md`
- `zoning_agent` -> `bedrock/status/agents/zoning_agent_status.md`
- `layout_code_agent` -> `bedrock/status/agents/layout_code_agent_status.md`
- `layout_research_agent` -> `bedrock/status/agents/layout_research_agent_status.md`
- `feasibility_agent` -> `bedrock/status/agents/feasibility_agent_status.md`
- `evaluation_agent` -> `bedrock/status/agents/evaluation_agent_status.md`
- `docs_agent` -> `bedrock/status/agents/docs_agent_status.md`
- `data_governance_agent` -> `bedrock/status/agents/data_governance_agent_status.md`
- `refactor_agent` -> `bedrock/status/agents/refactor_agent_status.md`

Execution agents must not update:

- `bedrock/status/system_state.md`
- `bedrock/status/current_batch.md`
- `bedrock/status/milestone_tracker.md`
- another agent's status file

## Safe Update Principle

Status writes must be narrow and ownership-local.

- orchestrator updates global state
- agents update only their own local state
- no shared status file should be edited by multiple execution agents

## Conflict Rule

If an agent believes another status file is incorrect:

1. do not edit it
2. record the issue under `recent_system_changes` or `blockers` in the agent's own file
3. escalate the discrepancy to `orchestrator_agent`
