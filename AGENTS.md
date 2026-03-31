# Repository Guidelines

## Project Structure & Module Organization

This workspace centers on the land pipeline: Parcel -> Zoning -> Layout -> Feasibility -> UI. Active root code lives in `pipeline/` and `services/`, with validation coverage in `tests/`. Platform contracts, APIs, and orchestration live under `bedrock/`. Zoning ingestion and lookup scaffolding live under `zoning_data_scraper/` with package code in `zoning_data_scraper/src/`. Documentation and governance rules are in `docs/`. Treat `takeoff_archive/` as frozen legacy material; do not add new implementation work there.

## Build, Test, and Development Commands

Create a virtualenv first, then install the active packages:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ./bedrock -e ./zoning_data_scraper
```

Run the main validation suite from the repo root with `pytest`. Use targeted runs while iterating, for example:

```bash
pytest tests/pipeline/test_pipeline_run.py
pytest tests/zoning/test_zoning_lookup.py
pytest tests/layout/test_layout_api.py
```

For local zoning app work, use `zoning app serve --host 127.0.0.1 --port 8787` from the activated environment.

## Coding Style & Naming Conventions

Use Python 3.11+ for `bedrock/` and 3.9+ compatibility for `zoning_data_scraper/`. Follow existing Python style: 4-space indentation, type hints, `snake_case` for functions/modules, `PascalCase` for models, and concise docstrings only where they add real context. Keep canonical pipeline contracts in `bedrock/contracts/` and avoid duplicating domain models across packages.

## Testing Guidelines

Pytest is the source of truth. Place tests under the matching domain folder in `tests/` (`tests/pipeline/`, `tests/zoning/`, `tests/layout/`, etc.) and name files `test_<behavior>.py`. Any pipeline-facing change should include execution evidence, not just documentation: a passing pytest case, API response, or persisted run artifact proving the full stage connection.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects, sometimes with a scoped prefix, for example `docs: reorganize platform documentation` or `Add Bedrock platform integration and validation updates`. Keep commits focused and outcome-based. PRs should include:

- a short problem/solution summary
- linked issue or roadmap reference when applicable
- exact test commands run
- screenshots or JSON/API output for UI or pipeline behavior changes

## Repository Boundaries

Do not move ingestion logic into `bedrock/`, and do not reimplement layout engine internals in the root pipeline. Follow `docs/repo_rules.md` when work crosses repo boundaries.
