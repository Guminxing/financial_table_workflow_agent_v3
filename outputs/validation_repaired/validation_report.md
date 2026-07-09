# Validity Critic Report

- project: `financial_table_workflow_agent`  |  critic_version: `0.1`

## 1. Overall Status

- **overall_status**: `passed_with_warnings`
- total checks: 15
- passed: 14
- warnings: 1
- failed: 0

## 2. What This Critic Checks

This stage is **not** ordinary table-quality checking. It checks whether the prepared panel satisfies downstream modeling validity requirements — especially **future-function (look-ahead bias)** and **label leakage** — plus temporal validity and data-leakage risk.

## 3. Input Files

- prepared_panel: `outputs/repaired/repaired_panel.csv`
- data_dictionary: `outputs/prepared/data_dictionary.json`
- execution_log: `outputs/prepared/execution_log.json`
- workflow_plan: `outputs/plans/workflow_plan.json`
- executor_source: `src/executor.py`
- calendar: `data/sample/calendar.csv`

## 4. Key Validation Results

| check_name | category | severity | status | recommendation |
|---|---|---|---|---|
| primary_key_uniqueness | data_quality | error | passed | No action needed. |
| required_columns_exist | schema | error | passed | No action needed. |
| label_role_is_correct | label_leakage | error | passed | No action needed. |
| label_not_in_approved_features | label_leakage | error | passed | No action needed. |
| no_future_named_columns_in_features | label_leakage | error | passed | No action needed. |
| approved_features_have_valid_roles | label_leakage | error | passed | No action needed. |
| fundamentals_aligned_by_announce_date | look_ahead_bias | error | passed | No action needed. |
| report_date_not_used_for_alignment | look_ahead_bias | error | passed | No action needed. |
| rolling_features_past_only_static_check | look_ahead_bias | error | passed | No action needed (static check). |
| label_created_with_future_shift | label_leakage | error | passed | No action needed. |
| trading_calendar_alignment | time_alignment | warning | passed | No action needed. |
| price_volume_sanity | data_quality | error | passed | No action needed. |
| missing_rate_after_join | data_quality | warning | warning | Acceptable for baseline; consider imputation or wider ann... |
| source_flags_consistency | data_quality | warning | passed | No action needed. |
| time_based_split_required | temporal_validity | error | passed | Enforce time-based split in downstream modeling. |

## 5. Leakage and Temporal Validity

- **label_not_in_approved_features**: `passed` — label_next_5d must not be in approved feature columns
  - evidence: approved=['return_1d', 'return_5d', 'volatility_20d', 'turnover_20d', 'pe', 'pb', 'roe', 'industry_name'], label_in_features=False
  - recommendation: No action needed.
- **no_future_named_columns_in_features**: `passed` — Approved features must not contain future/next/label/target names
  - evidence: approved=['return_1d', 'return_5d', 'volatility_20d', 'turnover_20d', 'pe', 'pb', 'roe', 'industry_name'], forbidden_hits=[]
  - recommendation: No action needed.
- **fundamentals_aligned_by_announce_date**: `passed` — For rows with fundamentals, announce_date <= date
  - evidence: violation_count=0, missing_announce_but_has_fund=0, n_fund_rows=53
  - recommendation: No action needed.
- **report_date_not_used_for_alignment**: `passed` — executor should use announce_date + merge_asof, not report_date
  - evidence: executor_has_announce_date=True, executor_has_merge_asof=True, report_date_used_in_merge=False, panel_has_report_date_column=False
  - recommendation: No action needed.
- **rolling_features_past_only_static_check**: `passed` — rolling features grouped by ticker, no non-label shift(-k) found
  - evidence: groupby_ticker_present=True, rolling_present=True, shift_negative_lines=['"""生成 label_next_5d = close.shift(-5)/close - 1，按 ticker 分组。', '.shift(-5) / df["close"] - 1', '"description": "Future 5-day return = close.shift(-5)/close-1; MUST be excluded from feature columns"},', 'lines.append("`label_next_5d` = future 5-day return = `close.shift(-5)/close - 1`, grouped by ticker.")'], non_label_shift_negative_lines=[]
  - recommendation: No action needed (static check).
- **label_created_with_future_shift**: `passed` — label_next_5d should be created with shift(-5) and marked as label only
  - evidence: label_uses_shift_neg5=True, label_role_in_dict=label
  - recommendation: No action needed.
- **time_based_split_required**: `passed` — workflow_plan must require time-based train/test split
  - evidence: plan_has_time_based_split_check=True, assumption_mentions=['时间序列样本不得随机打乱，必须按时间做 train/test 切分。']
  - recommendation: Enforce time-based split in downstream modeling.

## 6. Data Quality Findings

- **primary_key_uniqueness**: `passed` — date + ticker must be unique
  - evidence: duplicate_count=0
- **missing_rate_after_join**: `warning` — close missing rate must be 0; pe/pb/roe high missing is warning (low announce freq)
  - evidence: close_missing_rate=0.0, fundamental_missing_rates={'pe': 0.8221, 'pb': 0.8221, 'roe': 0.8221}, industry_missing_rate=0.198, warnings=['pe/pb/roe high missing (>20%)', 'industry_name missing']
- **price_volume_sanity**: `passed` — open/high/low/close > 0; volume >= 0; turnover >= 0
  - evidence: non_positive_price_count=0, negative_volume_count=0, negative_turnover_count=0
- **source_flags_consistency**: `passed` — source_* flags consistent with underlying columns
  - evidence: issues=[]
- **trading_calendar_alignment**: `passed` — All panel dates must be trading days
  - evidence: n_panel_dates=60, n_non_trading=0

Notes:
- pe/pb/roe sparsity is expected (low announce frequency); flagged as warning, not failure.
- industry_name missing reflects the simulated data design (one ticker missing industry).

## 7. Approved Feature Columns

- label_column: `label_next_5d`
- approved_feature_columns:
  - `return_1d`
  - `return_5d`
  - `volatility_20d`
  - `turnover_20d`
  - `pe`
  - `pb`
  - `roe`
  - `industry_name`
- excluded_columns:
  - `date`
  - `ticker`
  - `open`
  - `high`
  - `low`
  - `close`
  - `volume`
  - `turnover`
  - `label_next_5d`
  - `source_price_available`
  - `source_volume_available`
  - `source_fundamental_available`
  - `source_industry_available`
  - `announce_date`

## 8. Limitations

- Current Critic is a deterministic baseline; no LLM is called.
- Judgment on whether rolling fully avoids future data partly relies on static source checks.
- No model is trained in this stage.
- No real business data is used; only simulated sample data.
- No investment advice is produced.

## 9. Next Stage

- **Multi Planner Voting**: multiple planners produce plans, vote/pick best.
- **LLM Planner / LLM Critic**: replace rule-based components with LLM-driven ones.
- **Baseline comparison**: rule-based vs single-agent vs multi-agent + critic.
