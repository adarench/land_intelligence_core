# Status Update Guidelines

These rules define how agents should maintain status safely and consistently.

## Required Fields For Every Agent Status File

Each agent-owned file must contain:

- `owner`
- `status`
- `current_task`
- `last_completed_milestone`
- `next_planned_step`
- `blockers`
- `recent_system_changes`
- `last_updated`

## Update Frequency

Agents should update their status file:

- when starting a new assigned task
- after completing a meaningful subtask
- when blocked
- before handing control back to the orchestrator

Do not update status on every tiny edit.
Update when the state meaningfully changes.

## Formatting Rules

- use Markdown
- keep a stable section order
- use short factual bullet points
- prefer timestamps or explicit dates when useful
- do not rewrite the entire file if only one section changed
- do not include speculative claims as completed work

## Safe Update Pattern

When updating an agent file:

1. read the current contents
2. preserve the existing section structure
3. change only the fields that actually changed
4. keep prior context brief
5. leave ownership and path declarations intact

## Global File Rules

Execution agents must never modify:

- `bedrock/status/system_state.md`
- `bedrock/status/current_batch.md`
- `bedrock/status/milestone_tracker.md`

Only `orchestrator_agent` may change those files.

## Recommended Status Values

Use one of:

- `idle`
- `working`
- `blocked`
- `awaiting_review`
- `completed`

## Example Safe Entry Style

```md
## Current Task

- implement canonical zoning lookup normalization

## Blockers

- waiting on governance approval for new field names
```
