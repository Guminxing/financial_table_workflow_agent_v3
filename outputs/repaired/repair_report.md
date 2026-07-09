# Repair Loop Report

- project: `financial_table_workflow_agent`  |  repair_version: `0.1`

## 1. Why Repair Was Needed

The Validity Critic reported `overall_status = failed`. The failing check was `missing_rate_after_join` caused by missing `close` values: `close` is a core price field required by return features and the label, so its missing rate must be 0. Until repaired, the panel cannot be considered analysis-ready.

## 2. Failed Checks From Critic

| check_name | category | description | evidence |
|---|---|---|---|
| missing_rate_after_join | data_quality | close missing rate must be 0; pe/pb/roe high missing is warning (low announce freq) | close_missing_rate=0.0067, fundamental_missing_rates={'pe': 0.8233, 'pb': 0.8233, 'roe': 0.8233}, industry_missing_rate=0.2 |

## 3. Repair Strategy

For `close` missing rows, the baseline **drops the entire row** rather than imputing.

Reasons:

- `close` is the core price field; `return_1d`, `return_5d`, `volatility_20d`, and `label_next_5d` all depend on it.
- For simulated data and a modeling panel, dropping 2/300 rows is more conservative than imputation and avoids fabricating price points.
- Imputation could introduce artificial return/volatility patterns that bias downstream modeling.

Note on real-world data:

- For real market data, a better approach is to re-fetch the original price series or use ticker-level time-series interpolation / adjusted-price re-pull, then re-run the executor.
- The current baseline deliberately chooses conservative row deletion.

Items not repaired (by design):

- `pe/pb/roe high missing rate` — low announce frequency is expected for fundamentals; not a failure, only a warning; no repair needed
- `industry_name missing` — reflects simulated data design (one ticker missing industry); kept as-is; downstream can encode as 'unknown'

## 4. Repair Result

- rows before: 300
- rows after: 298
- rows removed: 2
- close missing count after repair: 0
- primary key unique after repair: True
- label column preserved: True
- label not in approved features: True

Actions applied:

| action_id | strategy | target_columns | rows_removed | status |
|---|---|---|---|---|
| 1 | drop_rows_with_missing_core_price | ['close'] | 2 | applied |

## 5. Limitations

- This is a **deterministic baseline repair**; no LLM is called.
- For real market data, the ideal approach is to re-fetch original prices or repair via business rules, not just drop rows.
- Dependent fields (return/volatility/label) on remaining rows are NOT recomputed in this minimal repair; a full fix re-runs the executor on repaired inputs. For the current sample, dropping rows does not break existing per-ticker rolling windows because windows are min_periods=1.
- No model is trained in this stage.
- No investment advice is produced.

## 6. Next Step

Re-run the Validity Critic on the repaired panel to confirm the failure is resolved:

```bash
python src/run_critic.py --panel_path outputs/repaired/repaired_panel.csv --data_dictionary_path outputs/prepared/data_dictionary.json --execution_log_path outputs/prepared/execution_log.json --plan_path outputs/plans/workflow_plan.json --executor_source_path src/executor.py --calendar_path data/sample/calendar.csv --output_dir outputs/validation_repaired
```
