# 第三阶段：Code Executor

## 1. 为什么需要 Executor

第一阶段的 Profiler 回答了"数据长什么样"，
第二阶段的 Planner 回答了"该按什么顺序做什么"，
但都没有真正动数据。

Code Executor 的作用就是：**把 plan 真正执行成一张 analysis-ready 宽表**。
它把 `workflow_plan.json` 里的步骤翻译成 pandas 代码，逐 步落地，
最终产出 `prepared_panel.csv` 及配套的数据字典、执行日志、执行报告。

没有 Executor，plan 就只是一份文档；有了 Executor，数据准备才真正闭环。

---

## 2. Executor 和 Planner 的关系

```
profile.json + analysis_goal → [Workflow Planner] → workflow_plan.json
                                                              │
                                                              ▼
raw CSV ────────────────────────────────────────► [Code Executor]
                                                              │
                                                              ▼
                                          prepared_panel.csv + data_dictionary.json
                                          + execution_log.json + execution_report.md
```

- **Planner**：决策层，输出"做什么、为什么、风险是什么"（结构化 plan）。
- **Executor**：执行层，按 plan 的 step 顺序与约束，把原始表加工成宽表。
- Executor **消费** plan，但不重新规划；它尊重 plan 的 `depends_on`、
  `actions`、防未来函数约束（rolling 按 ticker 分组、财务用 announce_date、标签隔离）。

---

## 3. 输入输出

### 输入

- `data/real_market/` 下的 5 张 CSV：price / volume / fundamentals / industry / calendar
- `outputs_real/plans/workflow_plan.json`

### 输出

- `outputs_real/prepared/prepared_panel.csv`：analysis-ready 日频 ticker-date panel
- `outputs_real/prepared/data_dictionary.json`：字段口径说明（role = primary_key / raw_input / feature / label / source_flag）
- `outputs_real/prepared/execution_log.json`：执行步骤、警告、质量检查、最终表摘要
- `outputs_real/prepared/execution_report.md`：人类可读执行报告

### 运行命令

```bash
python src/run_executor.py --input_dir data/real_market --plan_path outputs_real/plans/workflow_plan.json --output_dir outputs_real/prepared
```

---

## 4. 当前确定性 baseline 的作用

`src/executor.py` 中的 `CodeExecutor` 是**确定性 baseline**：

- 不调用任何 LLM API，离线可运行。
- 按 plan 的 11 个核心步骤顺序执行：加载 → 字段标准化 → 日期解析 → 主键去重 →
  交易日对齐 → 合并 price+volume → 生成特征 → 财务 announce_date 对齐 →
  合并 industry → 生成标签 → 最终质量检查。
- 每步把状态写入 `execution_log.json`，便于追溯与审计。
- 不训练模型、不输出投资建议、不连接真实券商系统。

它是后续"LLM 生成代码 → 执行"模式的参照实现：
当 LLM 生成的执行代码不确定时，可以用 baseline 的输出做对照。

---

## 5. 如何避免未来函数

Executor 在多处显式防止 look-ahead bias：

1. **行情特征**：`return_1d`/`return_5d` 用 `pct_change(k)`（k>0，只用当前及过去价格）；
   `volatility_20d`/`turnover_20d` 用 `rolling(20)`（历史窗口）。
   所有 rolling/pct_change 都 `groupby('ticker')`，禁止跨标的泄漏。
2. **财务对齐**：pe/pb/roe 用 `pd.merge_asof(direction='backward')` 按 **announce_date** 对齐，
   某日 `t` 只能用 `announce_date <= t` 的最近一条财务数据；**严禁用 report_date**。
3. **标签隔离**：`label_next_5d = close.shift(-5)/close - 1` 是未来信息，
   在 `data_dictionary.json` 中标注 `role = label`，并要求训练时从特征列排除。
4. **时间切分**：plan 的 validation_plan 已要求按时间做 train/test 切分，不得随机打乱
   （由下一阶段 Validity Critic 强制检查）。

---

## 6. 为什么 fundamentals 必须按 announce_date 对齐

财务数据有**公告滞后**：`report_date`（如季报截止日 2024-03-31）通常在
`announce_date`（如 2024-06-20）才对外披露。

如果在 `report_date` 就把 pe/pb/roe 当作"已知"对齐到日频 panel，
那么在 `report_date ~ announce_date` 之间，模型实际上用到了**未来才知道的信息**，
这就是 look-ahead bias。

正确做法：以 `announce_date` 作为"可用日"，对每个 ticker 做 as-of merge，
保证某日只能用到该日及之前已公告的财务数据。Executor 的
`_align_fundamentals` 正是按此实现，并在 log 中记录 `report_date_not_used: True`。

---

## 7. 当前限制

- 去重策略默认**保留最后一条**，需人工确认（已在 log/report 标注）。
- 未训练任何预测模型。
- 未运行完整 Validity Critic（下一阶段）。
- 未做多 Agent 投票。
- 未做 Streamlit 可视化。
- 不输出任何投资建议。
- baseline 为确定性规则，遇到更复杂 schema 时扩展性有限（后续可由 LLM 生成代码增强）。

---

## 8. 下一阶段 Validity Critic 要检查什么

基于 `workflow_plan.json` 的 `validation_plan.checks`，Critic 应至少检查：

- `primary_key_uniqueness`：(date, ticker) 唯一
- `label_not_in_features` / `no_future_return_in_features`：标签与未来收益列不得进入特征
- `rolling_window_uses_past_only`：rolling/pct_change 不得使用 `shift(-k)`（k>0）
- `fundamentals_aligned_by_announce_date`：财务确实按 announce_date 滞后对齐
- `trading_calendar_alignment`：panel 日期均为交易日
- `time_based_train_test_split_required`：按时间切分，不随机打乱
- `duplicate_row_handling`：重复主键已处理
- `negative_or_zero_price_check` / `negative_volume_or_turnover_check`：价格>0、成交量≥0
- `suspicious_industry_values`：行业缺失/拼写异常已标记
- `missing_rate_after_join`：join 后缺失率可接受

Critic 读取 `prepared_panel.csv` + `execution_log.json`，对照上述检查项给出 pass/fail。

---

## 9. 和临床 capstone 的迁移关系

金融 prepared table 与临床 analysis-ready cohort table 同构：

| 金融场景 | 临床场景 | 共性问题 |
|---|---|---|
| `prepared_panel.csv`（ticker-date 宽表） | analysis-ready cohort table（patient-time 宽表） | 多源拼成的可建模宽表 |
| announce_date 对齐（财务可用日） | prediction time cut-off（只能用预测时点前信息） | as-of 时间对齐 / 防时间泄漏 |
| `label_next_5d` 隔离（未来收益只作标签） | outcome leakage 防控（未来结局只作标签） | 标签隔离 / 防 label leakage |
| (date, ticker) 主键 | (time, patient_id) 主键 | 实体-时间主键 |

迁移要点：把 Executor 的"标准化→对齐→特征→标签→质量检查"流水线抽象成与领域无关的接口，
后续可同时产出金融 panel 与临床 cohort 两类 analysis-ready 表。
