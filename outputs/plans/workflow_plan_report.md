# Workflow Plan Report

- project: `financial_table_workflow_agent`  |  planner_version: `0.1`

## 1. Analysis Goal

构建一个用于 5 日收益率预测或因子分析的股票/ETF 日频建模宽表。要求每一行是 ticker-date，特征只能使用当前日期及之前可获得的信息，生成 return_1d、return_5d、volatility_20d、turnover_20d、pe、pb、roe、industry 等字段，标签为未来 5 日收益率 label_next_5d，并检查是否存在未来函数或数据泄漏。

## 2. Detected Data Context

- tables: `['calendar.csv', 'fundamentals.csv', 'industry.csv', 'price.csv', 'volume.csv']`
- main_entity: ticker-date panel
- target_table_type: analysis-ready financial panel table
- downstream_task_type: factor_analysis_or_5d_return_prediction

### date_fields

| table | column |
|---|---|
| calendar.csv | date |
| fundamentals.csv | report_date |
| fundamentals.csv | announce_date |
| price.csv | trade_date |
| volume.csv | date |

### security_id_fields

| table | column |
|---|---|
| fundamentals.csv | ticker |
| industry.csv | ticker |
| industry.csv | industry_name |
| price.csv | ticker |
| volume.csv | stock_code |

## 3. Key Issues From Profiler

- 主表为日频 ticker-date panel，主键为 (date, ticker)。
- 所有特征必须只使用预测时点 t 及之前可获得的信息，禁止使用未来数据。
- price 与 volume 的日期字段命名不一致（trade_date vs date），需先统一为 date 再合并。
- price 与 volume 的证券代码字段命名不一致（ticker vs stock_code），需先统一为 ticker 再合并。
- fundamentals 同时存在 report_date 与 announce_date，存在公告滞后；财务字段（pe/pb/roe）只能基于 announce_date 对齐到日频 panel，严禁直接用 report_date 作为可用日期，否则会引入 look-ahead bias。
- 存在 calendar.csv，可作为交易日对齐依据（is_trading_day 标志），用于剔除非交易日记录并补齐交易日序列。
- price 与 volume 的 (date, ticker) 覆盖可能不一致，合并后需检查缺失并决定 left/inner join 策略。
- label_next_5d 为未来 5 日收益率，仅作标签用途，不得进入特征列；训练时必须从 feature columns 中排除。
- 时间序列样本不得随机打乱，必须按时间做 train/test 切分。

## 4. Planned Workflow Steps

| step_id | name | category | priority | reason |
|---|---|---|---|---|
| 1 | load_raw_tables | ingestion | high | 统一加载原始表，作为后续清洗与对齐的输入基线。 |
| 2 | standardize_column_names | schema_standardization | high | profiler 发现 price 与 volume 的日期/代码字段命名不一致，必须先统一字段名才能按 (date, ticker) 合并。 |
| 3 | parse_and_validate_dates | type_normalization | high | 日期是 panel 主键与对齐依据，必须先转成 datetime 才能做滚动与对齐。 |
| 4 | validate_primary_keys | data_quality | high | profiler 发现 price.csv 存在重复 (trade_date, ticker) 主键，若不去重会导致 join 后行数膨胀与标签错位。 |
| 5 | align_with_trading_calendar | time_alignment | medium | 用交易日历对齐，剔除非交易日记录，保证 panel 时间轴一致。 |
| 6 | merge_price_and_volume | join | high | 行情与成交是日频 panel 的主体，需合并成一张 (date, ticker) 宽表。profiler 已给出 join key 建议。 |
| 7 | compute_price_volume_features | feature_engineering | high | 生成收益与波动特征。rolling 窗口必须只使用历史数据，否则构成 look-ahead bias。 |
| 8 | align_fundamentals_by_announce_date | time_alignment | high | 财务数据有公告滞后，必须基于 announce_date 对齐，确保某日只能用到该日及之前已公告的财务数据。 |
| 9 | merge_industry | join | medium | 补充行业字段作为分类特征；profiler 发现行业存在缺失/拼写异常需标记。 |
| 10 | create_future_return_label | label_engineering | high | 生成预测标签。标签本质是未来信息，必须严格隔离于特征之外，否则构成 label leakage。 |
| 11 | final_missing_and_quality_checks | data_quality | medium | join 与特征工程后需复查缺失/重复/异常，确保 panel 可用。 |
| 12 | leakage_and_validity_checks | validation_planning | high | 在交付建模前，必须由 Validity Critic 做泄漏与有效性审查。 |
| 13 | export_analysis_ready_outputs | export | medium | 产出最终 analysis-ready 产物与配套文档，供下游建模使用。 |

### Step 1: load_raw_tables

- category: ingestion  |  priority: high  |  depends_on: []
- input_tables: `[]`
- output_tables: `['raw_price', 'raw_volume', 'raw_fundamentals', 'raw_industry', 'raw_calendar']`
- reason: 统一加载原始表，作为后续清洗与对齐的输入基线。
- actions:
  - 读取 price.csv、volume.csv、fundamentals.csv、industry.csv、calendar.csv
  - 保留原始字段名与原始类型，不做任何清洗
  - 记录每张表的行数与列数，与 profile.json 对账
- risks_addressed:
  - 原始数据未加载
- expected_output: 5 个原始 DataFrame，字段与 profile.json 一致

### Step 2: standardize_column_names

- category: schema_standardization  |  priority: high  |  depends_on: [1]
- input_tables: `['raw_price', 'raw_volume', 'raw_fundamentals', 'raw_industry']`
- output_tables: `['std_price', 'std_volume', 'std_fundamentals', 'std_industry']`
- reason: profiler 发现 price 与 volume 的日期/代码字段命名不一致，必须先统一字段名才能按 (date, ticker) 合并。
- actions:
  - price.trade_date → date
  - volume.date → date（已是 date，保持）
  - volume.stock_code → ticker
  - fundamentals.ticker → ticker（已是 ticker，保持）
  - industry.ticker → ticker（已是 ticker，保持）
- risks_addressed:
  - profiler 检出 date_column_name_mismatch: ['trade_date', 'date'] — date fields have different names but likely same semantics
  - profiler 检出 security_id_column_name_mismatch: ['ticker', 'stock_code'] — security id fields have different names but likely same semantics
- expected_output: 所有表日期列统一为 date，证券代码列统一为 ticker

### Step 3: parse_and_validate_dates

- category: type_normalization  |  priority: high  |  depends_on: [2]
- input_tables: `['std_price', 'std_volume', 'std_fundamentals', 'std_calendar']`
- output_tables: `['typed_price', 'typed_volume', 'typed_fundamentals', 'typed_calendar']`
- reason: 日期是 panel 主键与对齐依据，必须先转成 datetime 才能做滚动与对齐。
- actions:
  - 将日期列解析为 datetime: ['announce_date', 'date', 'report_date', 'trade_date']
  - 检查空日期、无法解析日期，记录异常行数
  - 检查各表日期范围是否合理（与 profile.date_range 对账）
- risks_addressed:
  - 字符串日期无法参与时间运算
- expected_output: 日期列为 datetime 类型，异常日期被记录

### Step 4: validate_primary_keys

- category: data_quality  |  priority: high  |  depends_on: [3]
- input_tables: `['typed_price', 'typed_volume', 'typed_fundamentals']`
- output_tables: `['pk_checked_price', 'pk_checked_volume', 'pk_checked_fundamentals']`
- reason: profiler 发现 price.csv 存在重复 (trade_date, ticker) 主键，若不去重会导致 join 后行数膨胀与标签错位。
- actions:
  - 检查每张表的主键候选唯一性，重点 (date, ticker)
  - price.csv 检出重复主键 ['trade_date', 'ticker']: 2 条，需去重
  - 去重策略默认：保留最后一条（按加载顺序），但需在 plan 中标记为需人工确认
- risks_addressed:
  - price 主键重复 (2 条) — 去重策略需人工确认（保留最后一条 / 聚合）
- expected_output: 主键唯一的中间表；去重动作被记录待人工确认

### Step 5: align_with_trading_calendar

- category: time_alignment  |  priority: medium  |  depends_on: [4]
- input_tables: `['pk_checked_price', 'pk_checked_volume', 'typed_calendar']`
- output_tables: `['aligned_price', 'aligned_volume']`
- reason: 用交易日历对齐，剔除非交易日记录，保证 panel 时间轴一致。
- actions:
  - 使用 calendar.csv 的 is_trading_day 标志筛选交易日
  - 检查 price/volume 是否存在非交易日记录并记录
- risks_addressed:
  - profiler 提示 calendar 可作交易日对齐依据
- expected_output: 仅含交易日的 price/volume 中间表

### Step 6: merge_price_and_volume

- category: join  |  priority: high  |  depends_on: [5]
- input_tables: `['aligned_price', 'aligned_volume']`
- output_tables: `['price_volume_panel']`
- reason: 行情与成交是日频 panel 的主体，需合并成一张 (date, ticker) 宽表。profiler 已给出 join key 建议。
- actions:
  - 基于 date + ticker 合并 price 与 volume（字段已在 step2 统一）
  - price 与 volume 覆盖不一致，合并后检查缺失率
  - 默认 left join 以 price 为基准，缺失 volume 字段标记为待处理
- risks_addressed:
  - price/volume 覆盖不一致 → 合并后部分 key 缺失
- expected_output: price_volume_panel，主键 (date, ticker)

### Step 7: compute_price_volume_features

- category: feature_engineering  |  priority: high  |  depends_on: [6]
- input_tables: `['price_volume_panel']`
- output_tables: `['feature_panel']`
- reason: 生成收益与波动特征。rolling 窗口必须只使用历史数据，否则构成 look-ahead bias。
- actions:
  - return_1d = close.pct_change(1)，按 ticker 分组，仅用当前及过去价格
  - return_5d = close.pct_change(5)，按 ticker 分组，仅用历史窗口
  - volatility_20d = close.pct_change().rolling(20).std()，仅用历史 20 日
  - turnover_20d = turnover.rolling(20).mean()，仅用历史 20 日
  - 所有 rolling/pct_change 必须按 ticker 分组，禁止跨标的泄漏
  - 禁止使用未来价格作为特征
- risks_addressed:
  - rolling 窗口使用未来数据
  - 跨标的泄漏（未按 ticker 分组）
- expected_output: feature_panel 含 return_1d/return_5d/volatility_20d/turnover_20d

### Step 8: align_fundamentals_by_announce_date

- category: time_alignment  |  priority: high  |  depends_on: [7]
- input_tables: `['feature_panel', 'pk_checked_fundamentals']`
- output_tables: `['panel_with_fundamentals']`
- reason: 财务数据有公告滞后，必须基于 announce_date 对齐，确保某日只能用到该日及之前已公告的财务数据。
- actions:
  - 把 fundamentals 的 pe/pb/roe 基于 announce_date 对齐到日频 panel
  - 使用 as-of join / forward fill：每个交易日使用最近一次已公告的财务数据
  - 严禁直接用 report_date 作为可用日期（会引入未来函数）
- risks_addressed:
  - profiler 检出 fundamentals_lag: report_date 不是可用日期，必须用 announce_date 滞后对齐以避免 look-ahead bias
- expected_output: panel 含 pe/pb/roe，且均为已公告可得数据

### Step 9: merge_industry

- category: join  |  priority: medium  |  depends_on: [8]
- input_tables: `['panel_with_fundamentals', 'std_industry']`
- output_tables: `['panel_with_industry']`
- reason: 补充行业字段作为分类特征；profiler 发现行业存在缺失/拼写异常需标记。
- actions:
  - 按 ticker 合并 industry.csv 的 industry_name
  - industry.industry_name 缺失率 20.00%，合并后标记 warning
- risks_addressed:
  - industry.industry_name 存在缺失或拼写异常
- expected_output: panel 含 industry_name，异常值被标记

### Step 10: create_future_return_label

- category: label_engineering  |  priority: high  |  depends_on: [9]
- input_tables: `['panel_with_industry']`
- output_tables: `['labeled_panel']`
- reason: 生成预测标签。标签本质是未来信息，必须严格隔离于特征之外，否则构成 label leakage。
- actions:
  - label_next_5d = 未来 5 日收益率（close.shift(-5)/close - 1），按 ticker 分组
  - label_next_5d 只能作为标签，不得作为特征
  - 在后续训练时必须从 feature columns 中排除 label_next_5d
  - 生成 label 的行因含未来信息，训练特征矩阵中不得包含该列
- risks_addressed:
  - label leakage
  - 未来收益混入特征
- expected_output: labeled_panel 含 label_next_5d（仅标签用途）

### Step 11: final_missing_and_quality_checks

- category: data_quality  |  priority: medium  |  depends_on: [10]
- input_tables: `['labeled_panel']`
- output_tables: `['quality_checked_panel']`
- reason: join 与特征工程后需复查缺失/重复/异常，确保 panel 可用。
- actions:
  - 检查 join 后整体缺失率、重复 key、异常值、样本覆盖范围
- risks_addressed:
  - join 后质量未知
- expected_output: quality_checked_panel + 缺失/异常清单

### Step 12: leakage_and_validity_checks

- category: validation_planning  |  priority: high  |  depends_on: [11]
- input_tables: `['quality_checked_panel']`
- output_tables: `['validation_findings']`
- reason: 在交付建模前，必须由 Validity Critic 做泄漏与有效性审查。
- actions:
  - 规划 Validity Critic 需检查项（详见 validation_plan）
  - label leakage: label_next_5d 不得出现在特征列
  - look-ahead bias: rolling/pct_change 不得使用未来数据
  - fundamentals 是否使用 announce_date 滞后对齐
  - ticker-date 主键唯一性
  - 时间序列不得随机打乱，需按时间 train/test 切分
  - 幸存者偏差风险：检查是否存在仅含存续标的的样本
- risks_addressed:
  - label leakage
  - look-ahead bias
  - 时间序列打乱
  - 幸存者偏差
- expected_output: validation_findings，供 Critic 执行

### Step 13: export_analysis_ready_outputs

- category: export  |  priority: medium  |  depends_on: [12]
- input_tables: `['quality_checked_panel', 'validation_findings']`
- output_tables: `['prepared_panel.csv', 'data_dictionary.json', 'validation_report.json', 'data_quality_report.md']`
- reason: 产出最终 analysis-ready 产物与配套文档，供下游建模使用。
- actions:
  - 导出 prepared_panel.csv（analysis-ready 宽表）
  - 导出 data_dictionary.json（字段口径说明）
  - 导出 validation_report.json（校验结果）
  - 导出 data_quality_report.md（质量报告）
  - 注意：当前阶段只规划，不实际生成这些文件
- risks_addressed:
  - 产物未导出
- expected_output: 4 个产物文件路径（规划层面，本阶段不生成）

## 5. Feature and Label Plan

### features

| name | source | window | leakage_safe |
|---|---|---|---|
| return_1d | price.close | 1d lag | True |
| return_5d | price.close | 5d lag | True |
| volatility_20d | price.close | 20d rolling std | True |
| turnover_20d | volume.turnover | 20d rolling mean | True |
| pe | fundamentals.pe | as-of announce_date | True |
| pb | fundamentals.pb | as-of announce_date | True |
| roe | fundamentals.roe | as-of announce_date | True |
| industry_name | industry.industry_name | static | True |

### label

- name: `label_next_5d`
- definition: 未来 5 日收益率 = close.shift(-5)/close - 1，按 ticker 分组
- usage: label only; must be excluded from feature columns

### excluded_columns

- `label_next_5d`
- `any_future_return_columns`
- `raw_future_price_columns`
- `columns_available_only_after_prediction_date`

## 6. Validation Plan

| check_name | severity | description | suggested_rule |
|---|---|---|---|
| primary_key_uniqueness | error | (date, ticker) 主键必须唯一 | `assert panel.groupby(['date','ticker']).size().max() == 1` |
| missing_rate_after_join | warning | join 后各特征列缺失率应在可接受范围 | `for c in feature_cols: assert panel[c].isna().mean() < 0.2` |
| label_not_in_features | error | label_next_5d 不得出现在特征列 | `assert 'label_next_5d' not in feature_cols` |
| no_future_return_in_features | error | 特征中不得包含任何未来收益列 | `assert not any('future' in c or c.startswith('return_') and 'next' in c for c in feature_cols)` |
| rolling_window_uses_past_only | error | rolling/pct_change 只能使用历史窗口 | `verify rolling(20).std() and pct_change(1) use no shift(-k) with k>0` |
| fundamentals_aligned_by_announce_date | error | 财务字段必须基于 announce_date 滞后对齐 | `for each row, fundamentals effective date <= row date, based on announce_date` |
| trading_calendar_alignment | warning | panel 日期应与交易日历对齐，无非交易日记录 | `assert set(panel['date']).issubset(set(calendar[is_trading_day==1]['date']))` |
| time_based_train_test_split_required | error | 时间序列必须按时间切分，不得随机打乱 | `train.max(date) < test.min(date)` |
| duplicate_row_handling | warning | 重复行/重复主键必须已处理 | `assert panel.duplicated(['date','ticker']).sum() == 0` |
| suspicious_industry_values | warning | 行业字段缺失或拼写异常需标记 | `flag industry_name with missing or trailing/leading whitespace` |
| negative_or_zero_price_check | error | 价格不得 <= 0 | `assert (panel[['open','high','low','close']] > 0).all().all()` |
| negative_volume_or_turnover_check | error | 成交量/成交额不得 < 0 | `assert (panel[['volume','turnover']] >= 0).all().all()` |

## 7. Limitations

- 当前 Planner 为确定性规则版本，不调用 LLM，规划逻辑固定。
- 本阶段只输出 plan，不执行任何数据处理代码。
- 不保证 prepared_panel.csv 已生成；该文件由下一阶段 Code Executor 产出。
- 去重策略（保留最后一条 vs 聚合）需人工确认，Planner 仅给出默认建议。
- 未做收益预测、未做投资建议、未连接真实券商系统。

## 8. Next Stage

下一阶段由 Code Executor Agent 读取本 workflow_plan.json，生成并执行 pandas 数据处理代码，产出 prepared_panel.csv，再交由 Validity Critic 基于 validation_plan 做泄漏与有效性校验。

Code Executor Agent 的职责：

- 读取 `workflow_plan.json`
- 生成 pandas 数据处理代码
- 执行数据处理
- 输出 `prepared_panel.csv`
- 交给 Validity Critic 检查
