# Financial Table Data Profile Report

- project: `financial_table_workflow_agent`  |  profile_version: `0.1`
- tables: **5**
- total issues found: **6**

## calendar.csv

- file: `data/sample/calendar.csv`
- shape: 94 rows × 2 cols
- date_columns: `['date']`
- id_columns: `[]`
- numeric_columns: `['is_trading_day']`
- duplicate_rows_count: 0

### date_range

| column | min | max |
|---|---|---|
| date | 2024-01-02 | 2024-04-04 |

### missing_summary

| column | missing_count | missing_rate |
|---|---|---|
| date | 0 | 0.00% |
| is_trading_day | 0 | 0.00% |

### numeric_stats

| column | min | max | mean | std |
|---|---|---|---|---|
| is_trading_day | 0.0 | 1.0 | 0.6383 | 0.4831 |

### potential_issues

- (none)

## fundamentals.csv

- file: `data/sample/fundamentals.csv`
- shape: 20 rows × 6 cols
- date_columns: `['report_date', 'announce_date']`
- id_columns: `['ticker']`
- numeric_columns: `['pe', 'pb', 'roe']`
- duplicate_rows_count: 0
- duplicate_key_candidate: `['report_date', 'ticker']` → 0 dups

### date_range

| column | min | max |
|---|---|---|
| report_date | 2023-12-31 | 2024-09-30 |
| announce_date | 2024-02-19 | 2024-12-08 |

### missing_summary

| column | missing_count | missing_rate |
|---|---|---|
| report_date | 0 | 0.00% |
| announce_date | 0 | 0.00% |
| ticker | 0 | 0.00% |
| pe | 0 | 0.00% |
| pb | 0 | 0.00% |
| roe | 1 | 5.00% |

### numeric_stats

| column | min | max | mean | std |
|---|---|---|---|---|
| pe | -12.5 | 50.47 | 32.0855 | 15.4048 |
| pb | 0.5 | 7.91 | 3.9605 | 2.3 |
| roe | -4.42 | 28.99 | 15.4837 | 10.4642 |

### potential_issues

- warning: column 'pe' has negative value (min=-12.5); possible loss-making company, verify if intended

## industry.csv

- file: `data/sample/industry.csv`
- shape: 5 rows × 2 cols
- date_columns: `[]`
- id_columns: `['ticker', 'industry_name']`
- numeric_columns: `[]`
- duplicate_rows_count: 0

### missing_summary

| column | missing_count | missing_rate |
|---|---|---|
| ticker | 0 | 0.00% |
| industry_name | 1 | 20.00% |

### potential_issues

- (none)

## price.csv

- file: `data/sample/price.csv`
- shape: 302 rows × 6 cols
- date_columns: `['trade_date']`
- id_columns: `['ticker']`
- numeric_columns: `['open', 'high', 'low', 'close']`
- duplicate_rows_count: 2
- duplicate_key_candidate: `['trade_date', 'ticker']` → 2 dups

### date_range

| column | min | max |
|---|---|---|
| trade_date | 2024-01-02 | 2024-03-25 |

### missing_summary

| column | missing_count | missing_rate |
|---|---|---|
| trade_date | 0 | 0.00% |
| ticker | 0 | 0.00% |
| open | 3 | 0.99% |
| high | 1 | 0.33% |
| low | 1 | 0.33% |
| close | 2 | 0.66% |

### numeric_stats

| column | min | max | mean | std |
|---|---|---|---|---|
| open | 33.5434 | 130.4758 | 66.5996 | 32.68 |
| high | 35.0681 | 130.9888 | 67.9981 | 33.3621 |
| low | 32.6754 | 125.8429 | 65.0585 | 31.9891 |
| close | 34.1103 | 129.2403 | 66.7021 | 32.7564 |

### potential_issues

- warning: 2 fully duplicated rows found
- warning: duplicate key on ['trade_date', 'ticker']: 2 duplicates

## volume.csv

- file: `data/sample/volume.csv`
- shape: 295 rows × 4 cols
- date_columns: `['date']`
- id_columns: `['stock_code']`
- numeric_columns: `['volume', 'turnover']`
- duplicate_rows_count: 0
- duplicate_key_candidate: `['date', 'stock_code']` → 0 dups

### date_range

| column | min | max |
|---|---|---|
| date | 2024-01-02 | 2024-03-25 |

### missing_summary

| column | missing_count | missing_rate |
|---|---|---|
| date | 0 | 0.00% |
| stock_code | 0 | 0.00% |
| volume | 4 | 1.36% |
| turnover | 5 | 1.69% |

### numeric_stats

| column | min | max | mean | std |
|---|---|---|---|---|
| volume | 126802.0 | 9976907.0 | 5047728.3986 | 2764005.19 |
| turnover | 6532267.27 | 1301782010.94 | 409489577.1455 | 314084339.9958 |

### potential_issues

- (none)

## Cross-Table Findings

### possible_date_columns

- `calendar.csv` → `date`
- `fundamentals.csv` → `report_date`
- `fundamentals.csv` → `announce_date`
- `price.csv` → `trade_date`
- `volume.csv` → `date`

### possible_security_id_columns

- `fundamentals.csv` → `ticker`
- `industry.csv` → `ticker`
- `industry.csv` → `industry_name`
- `price.csv` → `ticker`
- `volume.csv` → `stock_code`

### schema_inconsistencies

- **date_column_name_mismatch** (price.csv, volume.csv): columns `['trade_date', 'date']` — date fields have different names but likely same semantics
- **security_id_column_name_mismatch** (price.csv, volume.csv): columns `['ticker', 'stock_code']` — security id fields have different names but likely same semantics
- **fundamentals_lag** (fundamentals.csv): columns `['report_date', 'announce_date']` — financial data has announcement lag; report_date is NOT the available-as-of date

### join_key_suggestions

- `price.csv` [trade_date, ticker] ⟷ `volume.csv` [date, stock_code]
  - reason: date/security id fields have different names but likely represent the same keys

### global_potential_issues

- fundamentals.csv has both report_date and announce_date; use announce_date (not report_date) as the available-as-of date to avoid look-ahead bias
- calendar.csv can be used as the trading-day alignment reference (is_trading_day flag)
- price.csv and volume.csv may have non-overlapping (date, ticker) keys; verify coverage before joining
