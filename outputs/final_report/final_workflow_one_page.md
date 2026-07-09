# Financial Table Analysis-Ready Workflow — One-Page Summary

**Project:** `financial_table_workflow_agent` (isomorphic to an NTU clinical table capstone).

## Goal

Turn raw, messy financial tables (price, volume, fundamentals, industry, trading calendar) into one **analysis-ready modeling panel** for 5-day return prediction / factor analysis — **data preparation only, no investment advice, no model training**. The hard part is preventing look-ahead bias and label leakage, not cleaning cells.

## Five Modules

1. **Data Profiler** — profiles schema, missing values, dates, security codes, duplicates, anomalies, and cross-table inconsistencies (e.g. `trade_date` vs `date`, `ticker` vs `stock_code`, fundamentals announcement lag).
2. **Workflow Planner** — reads the profile + a downstream analysis goal and emits an ordered, leakage-aware plan (13 steps, 12 validation checks, 8 features + 1 label).
3. **Code Executor** — executes the plan with pandas into a ticker-date panel, grouping rolling/pct_change by ticker and aligning fundamentals by `announce_date`.
4. **Validity Critic** — reviews the panel for future-function / label leakage / announce-date alignment / time-based split (not ordinary quality checks).
5. **Repair Loop** — consumes Critic failures, emits explainable repairs, and the Critic is re-run to verify (closed loop).

## Closed-Loop Result

- Initial `prepared_panel.csv`: **300 rows**.
- The Critic found **2 rows with missing `close`** (a core price field) and reported status **failed**.
- The Repair Loop **deleted those 2 rows** (conservative: drop, not impute), producing `repaired_panel.csv` with **298 rows**.
- Re-running the Critic gave status **passed_with_warnings** (0 failed; remaining warnings are expected pe/pb/roe sparsity and one missing industry, not failures).
- The label `label_next_5d` is **not** in the approved feature columns — label leakage is prevented by construction.

> initial 300 rows -> Critic failed (close missing 2 rows) -> Repair removed 2 rows -> 298 rows -> re-run Critic passed_with_warnings; label label_next_5d kept out of approved features

## Why It Matters

This is a **task-aware analysis-ready workflow**, not a table checker: it plans around a modeling goal, prevents future-function and label leakage by construction, and self-corrects via a critic → repair → re-critic loop. The methodology (financial future-function ≈ clinical time leakage) transfers to clinical cohort preparation.

## Next Steps

- Multi Planner Voting; LLM Planner/Critic/Repair; rule vs single-agent vs multi-agent baseline comparison. (All offline, no investment advice.)
