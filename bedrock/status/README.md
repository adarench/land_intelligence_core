# Bedrock Status Architecture

This directory is the canonical status layer for the Bedrock agent system.

It separates status ownership into two layers:

- System layer: updated only by `orchestrator_agent`
- Agent layer: each agent updates only its own file

## Structure

- `system_state.md`
- `current_batch.md`
- `milestone_tracker.md`
- `status_permissions.md`
- `status_update_guidelines.md`
- `agents/`

## Ownership Model

- `orchestrator_agent` owns the system layer
- each execution agent owns exactly one file under `agents/`

## Legacy Note

Older files under `bedrock/docs/status/` may still exist for historical reference.
They are not the source of truth for operational status going forward.
