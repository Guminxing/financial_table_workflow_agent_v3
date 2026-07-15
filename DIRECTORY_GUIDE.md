# Directory Guide — financial_table_workflow_agent (v3)

This file explains the responsibility of every top-level directory so a
reviewer can tell at a glance what is **run code**, what is **test code**,
what is **real test data**, what is **user runtime data**, what is
**output**, and what is **documentation**.

## Directory map

| Directory / File | Type | Committed to Git | Purpose |
|---|---|---|---|
| `src/` | Run code | Yes | Workflow stages, agents, real-data adapter, CLI entry points |
| `tests/` | Test code | Yes | Unit + integration tests for the Remediation Agent |
| `test_data/real_market_sample/` | Real test data (fixture) | Yes | Small, reproducible real A-share fixture for tests & minimal demo |
| `data/real_market/` | Real runtime data | No | Real market data the user downloads at runtime |
| `outputs_real/` | Run results | No | Pipeline + Agent artifacts produced by formal runs |
| `docs/` | Documentation | Yes | Stage design docs and operation guides |
| `prompts/` | Documentation | Yes | LLM prompt templates reserved for future LLM stages |
| `README.md` | Documentation | Yes | Project overview + quick start |
| `DIRECTORY_GUIDE.md` | Documentation | Yes | This file |
| `CODE_STRUCTURE.md` | Documentation | Yes | Code structure, module responsibilities, execution call chain |
| `.gitignore` | Config | Yes | Ignore rules for runtime data / outputs / caches / secrets |

## What goes where

- **Run code (`src/`)** — the only code that the formal pipeline executes.
  It never reads `test_data/` by default; formal runs default to
  `data/real_market/` (input) and `outputs_real/` (output).
- **Test code (`tests/`)** — automated tests. Integration tests use the
  committed real fixture under `test_data/real_market_sample/`. Fault
  scenarios (missing rows, duplicates, no_progress, max_rounds) are
  injected into **temporary copies** of the fixture, never into the
  committed fixture itself.
- **Real test data (`test_data/real_market_sample/`)** — a tiny, real,
  reproducible A-share dataset (ticker 600519, 2024-01-01..2024-01-10)
  fetched via the project's own real-data adapter. Used only for tests
  and the minimal demo. It is **not** a representative market sample and
  is **not** investment data.
- **User runtime data (`data/real_market/`)** — real market data the user
  downloads with `src/run_fetch_real_data.py`. Not committed; each user
  fetches their own.
- **Run results (`outputs_real/`)** — all pipeline/agent artifacts
  (profiles, plans, prepared panel, validation, repaired, final report,
  session logs). Not committed; regenerated each run.
- **Documentation (`docs/`, `prompts/`, `README.md`, this file, [CODE_STRUCTURE.md](CODE_STRUCTURE.md))** —
  design and operation docs.

## Removed in v3

The following synthetic-data artifacts existed in earlier versions and
have been **removed** from v3:

- `data/sample/` — synthetic sample CSVs.
- `src/generate_sample_data.py` — synthetic data generator.
- The "auto-generate sample data when input is missing" fallback in
  `PipelineRunner` / `run_profile.py`.
- Tracked `outputs/` artifacts produced from synthetic data.

Formal run code now **fails loudly** (with an actionable error) when the
input directory is missing, empty, or lacks CSVs — it never silently
falls back to synthetic data.
