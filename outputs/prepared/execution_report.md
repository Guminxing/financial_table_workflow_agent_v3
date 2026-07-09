# Code Execution Report

- project: `financial_table_workflow_agent`  |  executor_version: `0.1`

## 1. Input Files

- input_dir: `data/sample`
- raw tables: price.csv, volume.csv, fundamentals.csv, industry.csv, calendar.csv

## 2. Workflow Plan Used

- plan: `data/sample`
- planner steps followed: load_raw_tables → standardize_column_names → parse_and_validate_dates → validate_primary_keys → align_with_trading_calendar → merge_price_and_volume → compute_price_volume_features → align_fundamentals_by_announce_date → merge_industry → create_future_return_label → final_missing_and_quality_checks

## 3. Executed Steps

| # | step | status |
|---|---|---|
| 1 | load_raw_tables | ok |
| 2 | standardize_column_names | ok |
| 3 | parse_and_validate_dates | ok |
| 4 | validate_primary_keys | ok |
| 5 | align_with_trading_calendar | ok |
| 6 | merge_price_and_volume | ok |
| 7 | compute_price_volume_features | ok |
| 8 | align_fundamentals_by_announce_date | ok |
| 9 | merge_industry | ok |
| 10 | create_future_return_label | ok |
| 11 | final_missing_and_quality_checks | ok |

## 4. Output Table Summary

- rows: 300
- columns: 22
- tickers: 5
- date range: 2024-01-02 ~ 2024-03-25
- primary key unique: True

### column missing summary

| column | missing_rate |
|---|---|
| date | 0.00% |
| ticker | 0.00% |
| open | 1.00% |
| high | 0.33% |
| low | 0.33% |
| close | 0.67% |
| volume | 3.00% |
| turnover | 3.33% |
| return_1d | 3.00% |
| return_5d | 9.67% |
| volatility_20d | 3.33% |
| turnover_20d | 0.00% |
| pe | 82.33% |
| pb | 82.33% |
| roe | 82.33% |
| industry_name | 20.00% |
| label_next_5d | 9.67% |
| source_price_available | 0.00% |
| source_volume_available | 0.00% |
| source_fundamental_available | 0.00% |
| source_industry_available | 0.00% |
| announce_date | 82.33% |

## 5. Generated Features

- `return_1d`
- `return_5d`
- `volatility_20d`
- `turnover_20d`
- `pe`
- `pb`
- `roe`
- `industry_name`

All rolling/pct_change features are grouped by ticker and use historical windows only (no future data).

## 6. Label Definition

`label_next_5d` = future 5-day return = `close.shift(-5)/close - 1`, grouped by ticker.

- It is a **future-looking** value and can only be used as a **supervised-learning label**.
- It **must NOT** be used as a feature. Exclude it from the feature matrix before training.

## 7. Fundamental Data Alignment

pe/pb/roe are aligned to the daily panel via `pd.merge_asof(direction='backward')` on **announce_date**, grouped by ticker.

- For each row date `t`, only the most recent fundamental record with `announce_date <= t` is used.
- `report_date` is **NOT** used as the available-as-of date, which avoids **look-ahead bias**.

## 8. Warnings and Limitations

- This is a **deterministic baseline executor**; no LLM is called.
- Dedup strategy defaults to **keep last**; this needs **manual confirmation**.
- Warnings:
  - price.csv: 2 duplicate (date, ticker) rows; strategy=keep_last (needs manual confirmation)
  - merge_price_and_volume: 5 panel row(s) have no volume data (left join)
  - merge_industry: 60 panel row(s) have missing/abnormal industry_name
- No model is trained in this stage.
- No full Validity Critic is run in this stage (planned for the next stage).
- No investment advice is produced.
