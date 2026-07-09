# Financial Table Analysis-Ready Workflow Agent

把原始金融/券商业务表格自动**剖析 → 清洗 → 校验**，最终生成可用于分析建模的 **analysis-ready table** 的 workflow agent 项目。

> 本项目与 NTU clinical table capstone 同构：金融数据准备的痛点（未来函数、字段口径不一致、交易日错位）与临床数据准备（时间泄漏、编码体系不一致、事件时间错位）一一对应。

---

## 1. 项目简介

目标：构建一个以"数据准备"为核心的 workflow agent，把杂乱的原始金融表格（行情、成交、财务、行业、交易日历等）自动加工成一张干净的宽表（analysis-ready panel），供下游建模使用。

整体 pipeline：

```
raw financial tables
  → Data Profiler          ✅ Stage 1 已完成
  → Workflow Planner Agent ✅ Stage 2 已完成
  → Code Executor          ✅ Stage 3 已完成
  → Validity Critic        ✅ Stage 4 已完成
  → Remediation / Repair   ✅ Stage 5 已完成（闭环）
  → Re-run Critic          ✅ Stage 6 闭环验证
  → Final Report Generator ✅ Stage 6 已完成（收口）
  → (Multi Planner Voting) ⏳ 计划
  → analysis-ready table
```

---

## 2. 阶段 scope

### Stage 1: Data Profiler（已完成）

```
raw financial tables → Data Profiler → profile.json → profile_report.md
```

- 纯确定性 Python/Pandas 实现，**不调用任何 LLM API**。
- 可完全离线运行。

### Stage 2: Workflow Planner（已完成）

```
profile.json + analysis_goal → Workflow Planner → workflow_plan.json → workflow_plan_report.md
```

- 确定性规则实现，**不调用任何 LLM API**，离线可运行。
- 读取 `profile.json`，结合下游分析目标，动态生成 13 个 workflow steps + 12 个 validation checks。
- **只规划，不执行代码，不生成 `prepared_panel.csv`**。
- 已附带 LLM Planner Prompt 模板（`prompts/workflow_planner_prompt.md`），供后续接入 LLM 使用。

### Stage 3: Code Executor（已完成）

```
raw CSV + workflow_plan.json → Code Executor → prepared_panel.csv + data_dictionary.json + execution_log.json + execution_report.md
```

- 确定性 baseline，**不调用任何 LLM API**，离线可运行。
- 按 plan 的步骤用 pandas 真正执行数据处理，生成 analysis-ready 日频 ticker-date panel。
- 严格防未来函数：rolling/pct_change 按 ticker 分组只用历史窗口；财务按 announce_date as-of 对齐；标签隔离。
- **不训练模型、不输出投资建议、不连接真实券商系统**。

### Stage 4: Validity Critic（已完成）

```
prepared_panel.csv + data_dictionary.json + execution_log.json + workflow_plan.json + executor.py → Validity Critic → validation_report.json + validation_report.md + approved_feature_columns.json
```

- 确定性 baseline，**不调用任何 LLM API**，离线可运行。
- 对 prepared panel 做有效性审查（非普通质量检查）：未来函数、label leakage、announce_date 对齐、rolling 源码静态检查、time-based split 要求等 15 项检查。
- 生成 `approved_feature_columns.json`，从结构上杜绝 label 进入特征矩阵。
- **不训练模型、不输出投资建议、不连接真实券商系统**。

### Stage 5: Remediation / Repair Loop（当前阶段，闭环）

```
prepared_panel.csv + validation_report.json → Repair Loop → repair_plan.json + repaired_panel.csv + repair_log.json + repair_report.md → (重新运行 Critic 复审)
```

- 确定性 baseline，**不调用任何 LLM API**，离线可运行。
- 读取 Critic 的 failed/warning 项，生成可解释的修复方案并执行，输出 repaired_panel.csv。
- 当前重点修复 close 缺失（保守删除行，不默认插值）；修复后支持重新运行 Critic 复审，形成"审查 → 修复 → 再审查"闭环。
- **不训练模型、不输出投资建议、不连接真实券商系统**。

> 后续阶段（Multi Planner Voting）尚未实现。

### Stage 6: Final Report Generator（已完成，收口）

```
前五阶段全部产物 → Final Report Generator → final_workflow_summary.json + final_workflow_report.md + final_workflow_one_page.md + pipeline_artifacts_index.json
```

- 确定性 baseline，**不调用任何 LLM API**，离线可运行。
- **只读**前五阶段产物，不重新跑任何阶段、不重算任何字段。
- 汇总六阶段 workflow 与闭环结果，明确说明这不是"普通表格检查"，而是 task-aware analysis-ready workflow prototype。
- 总报告含 Mermaid 架构图与 "Why This Is More Than Table Checking" 小节；一页摘要适合直接发导师。
- **不训练模型、不输出投资建议、不连接真实券商系统**。

---

## 3. 为什么只做数据准备，不做投资建议

- **职责边界**：本项目的对标是"临床 analysis-ready cohort table"——临床 capstone 的核心是把脏数据加工成可建模宽表，而不是诊断或开药。金融同构项目对应地只做数据准备，不做选股/择时/收益预测。
- **可复现性**：数据准备是确定性的、可审计的；投资建议涉及预测与决策，不确定性高、合规风险大，不在本阶段范围。
- **迁移价值**：把"金融未来函数 ≈ 临床时间泄漏"这类方法论沉淀在数据准备层，后续无论做金融建模还是临床建模都能复用。

---

## 4. 目录结构

```
financial_table_workflow_agent/
├── README.md
├── requirements.txt
├── data/
│   ├── raw/            # 原始数据（真实业务系统导出，本阶段留空）
│   └── sample/         # 模拟样例数据（generate_sample_data.py 生成）
├── src/
│   ├── __init__.py
│   ├── generate_sample_data.py   # 生成 5 张模拟 CSV
│   ├── profiler.py               # FinancialTableProfiler (Stage 1)
│   ├── run_profile.py            # profiler CLI (Stage 1)
│   ├── planner.py                # WorkflowPlanner (Stage 2)
│   ├── run_planner.py            # planner CLI (Stage 2)
│   ├── executor.py               # CodeExecutor (Stage 3)
│   ├── run_executor.py           # executor CLI (Stage 3)
│   ├── critic.py                 # ValidityCritic (Stage 4)
│   ├── run_critic.py             # critic CLI (Stage 4)
│   ├── repair.py                 # RepairLoop (Stage 5)
│   ├── run_repair.py             # repair CLI (Stage 5)
│   ├── report_generator.py       # ReportGenerator (Stage 6)
│   └── run_report_generator.py   # report generator CLI (Stage 6)
├── prompts/
│   └── workflow_planner_prompt.md  # LLM Planner Prompt 模板
├── outputs/
│   ├── profiles/       # profile.json / profile_report.md (Stage 1)
│   ├── plans/          # workflow_plan.json / workflow_plan_report.md (Stage 2)
│   ├── prepared/       # prepared_panel.csv / data_dictionary.json / execution_log.json / execution_report.md (Stage 3)
│   ├── validation/     # validation_report.json / validation_report.md / approved_feature_columns.json (Stage 4)
│   ├── repaired/       # repair_plan.json / repaired_panel.csv / repair_log.json / repair_report.md (Stage 5)
│   ├── validation_repaired/  # 复审 Critic 产物 (Stage 5 闭环)
│   └── final_report/   # final_workflow_summary.json / final_workflow_report.md / final_workflow_one_page.md / pipeline_artifacts_index.json (Stage 6)
└── docs/
    ├── project_scope.md
    ├── stage2_workflow_planner.md
    ├── stage3_code_executor.md
    ├── stage4_validity_critic.md
    ├── stage5_remediation_loop.md
    └── stage6_report_generator.md
```

---

## 5. 快速运行

```bash
# 安装依赖（仅需 pandas）
pip install -r requirements.txt

# 生成模拟数据
python src/generate_sample_data.py

# Stage 1: 运行 profiler
python src/run_profile.py --input_dir data/sample --output_dir outputs/profiles

# Stage 2: 运行 planner
python src/run_planner.py --profile_path outputs/profiles/profile.json --output_dir outputs/plans

# Stage 3: 运行 executor
python src/run_executor.py --input_dir data/sample --plan_path outputs/plans/workflow_plan.json --output_dir outputs/prepared

# Stage 4: 运行 critic
python src/run_critic.py --panel_path outputs/prepared/prepared_panel.csv --data_dictionary_path outputs/prepared/data_dictionary.json --execution_log_path outputs/prepared/execution_log.json --plan_path outputs/plans/workflow_plan.json --executor_source_path src/executor.py --calendar_path data/sample/calendar.csv --output_dir outputs/validation

# Stage 5: 运行 repair（修复 Critic 发现的 failed 项）
python src/run_repair.py --panel_path outputs/prepared/prepared_panel.csv --validation_report_path outputs/validation/validation_report.json --data_dictionary_path outputs/prepared/data_dictionary.json --approved_features_path outputs/validation/approved_feature_columns.json --output_dir outputs/repaired

# Stage 5 闭环：对 repaired panel 重新运行 critic 复审
python src/run_critic.py --panel_path outputs/repaired/repaired_panel.csv --data_dictionary_path outputs/prepared/data_dictionary.json --execution_log_path outputs/prepared/execution_log.json --plan_path outputs/plans/workflow_plan.json --executor_source_path src/executor.py --calendar_path data/sample/calendar.csv --output_dir outputs/validation_repaired

# Stage 6: 运行 report generator（汇总前五阶段产物，生成最终总报告）
python src/run_report_generator.py --profile_json outputs/profiles/profile.json --workflow_plan_json outputs/plans/workflow_plan.json --prepared_panel outputs/prepared/prepared_panel.csv --execution_log outputs/prepared/execution_log.json --initial_validation_report outputs/validation/validation_report.json --repair_plan outputs/repaired/repair_plan.json --repair_log outputs/repaired/repair_log.json --repaired_panel outputs/repaired/repaired_panel.csv --final_validation_report outputs/validation_repaired/validation_report.json --approved_features outputs/validation_repaired/approved_feature_columns.json --data_dictionary outputs/prepared/data_dictionary.json --output_dir outputs/final_report
```

> 若 `data/sample/` 下没有 CSV，`run_profile.py` 会自动调用 `generate_sample_data` 生成样例数据。
> `run_planner.py` 若未传 `--analysis_goal`，使用默认的 5 日收益率预测 / 因子分析目标。

---

## 6. 输出文件说明

### `outputs/profiles/profile.json`

机器可读的完整数据画像，结构：

- `project` / `profile_version`
- `tables[]`：每张表的 schema、dtype、缺失值、日期列、证券代码列、数值列、日期范围、重复行、主键候选重复、数值统计、`potential_issues`
- `cross_table_findings`：
  - `possible_date_columns`
  - `possible_security_id_columns`
  - `schema_inconsistencies`（如 `trade_date` vs `date`、`ticker` vs `stock_code`）
  - `join_key_suggestions`
  - `global_potential_issues`（如 fundamentals 公告滞后 / look-ahead bias 提示）

### `outputs/profiles/profile_report.md`

人类可读的 Markdown 报告，包含每张表的统计表与问题清单，以及跨表发现。

### `outputs/plans/workflow_plan.json`（Stage 2）

机器可读的数据准备计划，结构：

- `analysis_goal` / `input_profile_path`
- `detected_context`：发现的表、主表、日期字段、证券代码字段
- `planning_assumptions`：基于 profile 动态生成的假设（含 look-ahead bias 提示）
- `workflow_steps[]`：13 个有序步骤（加载→统一字段→解析日期→校验主键→交易日对齐→合并→特征→财务对齐→行业→标签→质量检查→泄漏校验→导出）
- `feature_plan`：8 个特征 + 1 个标签 + 4 类排除列
- `validation_plan.checks`：12 个校验项（主键唯一/标签隔离/无未来函数/财务用 announce_date 等）
- `execution_notes_for_code_executor` / `limitations` / `next_stage_recommendation`

### `outputs/plans/workflow_plan_report.md`（Stage 2）

人类可读的计划报告，含分析目标、数据上下文、profiler 关键问题、步骤表、特征与标签计划、校验计划、局限性与下一阶段。

### `outputs/prepared/prepared_panel.csv`（Stage 3）

analysis-ready 日频 ticker-date panel，字段含：主键（date, ticker）、行情（open/high/low/close）、成交量（volume/turnover）、特征（return_1d/return_5d/volatility_20d/turnover_20d/pe/pb/roe/industry_name）、标签（label_next_5d）、来源标志（source_*_available）。

### `outputs/prepared/data_dictionary.json`（Stage 3）

字段口径说明，每列标注 role（primary_key / raw_input / feature / label / source_flag / auxiliary）。`label_next_5d` 标注 `role=label`，pe/pb/roe 标注基于 announce_date as-of 对齐。

### `outputs/prepared/execution_log.json`（Stage 3）

机器可读执行日志：执行步骤、警告、错误、输出文件、最终表摘要、列缺失率、质量检查（含重复处理与 announce_date 对齐记录）。

### `outputs/prepared/execution_report.md`（Stage 3）

人类可读执行报告：输入文件、所用 plan、执行步骤、输出表摘要、生成特征、标签定义、财务对齐说明、警告与限制。

### `outputs/validation/validation_report.json`（Stage 4）

机器可读有效性审查报告：overall_status（passed / passed_with_warnings / failed）、检查汇总、15 项检查明细（每项含 category/severity/status/evidence/recommendation）、approved_feature_columns、excluded_columns、limitations。

### `outputs/validation/validation_report.md`（Stage 4）

人类可读审查报告：总体状态、检查范围说明、输入文件、检查结果表、泄漏与时间有效性、数据质量发现、approved features、限制、下一阶段。

### `outputs/validation/approved_feature_columns.json`（Stage 4）

下游建模可直接使用的特征白名单：approved_feature_columns（仅 role=feature 列）、excluded_columns、label_column、使用说明。从结构上杜绝 label 进入特征矩阵。

### `outputs/repaired/repair_plan.json`（Stage 5）

机器可读修复方案：input_validation_status、failed_checks、repair_actions（每项含 target_check/strategy/reason/affected_rows_before/risk）、not_repaired_items、next_validation_required。

### `outputs/repaired/repaired_panel.csv`（Stage 5）

修复后的 analysis-ready panel（删除 close 缺失行），字段结构与 prepared_panel.csv 一致，可直接交回 Critic 复审。

### `outputs/repaired/repair_log.json`（Stage 5）

修复执行日志：rows_before/rows_after/rows_removed、actions_applied、checks_after_repair（close 缺失数、主键唯一性、label 保留）、warnings、next_step。

### `outputs/repaired/repair_report.md`（Stage 5）

人类可读修复报告：为何需要修复、Critic failed 项、修复策略、修复结果、限制、下一步（重新运行 Critic）。

### `outputs/final_report/final_workflow_summary.json`（Stage 6）

机器可读六阶段汇总：顶层含 `initial_validation_status` / `final_validation_status` / `rows_removed_by_repair` 三个关键字段；嵌套 `closed_loop_result`（300→298、failed→passed_with_warnings、label 隔离）、`pipeline_stages`、各阶段摘要、`approved_feature_columns`、`limitations`。

### `outputs/final_report/final_workflow_report.md`（Stage 6）

人类可读总报告：Executive Summary、**Mermaid 架构图**、**Why This Is More Than Table Checking**、Stage-by-stage、Closed-loop deep dive、Approved features & label isolation、Limitations、Next steps。

### `outputs/final_report/final_workflow_one_page.md`（Stage 6）

一页摘要，适合直接发导师：项目目标、五个模块、闭环结果（Critic 发现 2 行 close 缺失 → Repair 删除 2 行 → 复审 passed_with_warnings；label 不在 approved features）、为什么重要、下一步。

### `outputs/final_report/pipeline_artifacts_index.json`（Stage 6）

全部产物文件索引：按 stage 列出每个文件 `{stage, path, description, exists}`，`exists` 实算。

---

## 7. 下一步计划

- **Multi Planner Voting**：多个 Planner 各自出方案，投票/择优，提升鲁棒性。
- **LLM Planner / LLM Critic / LLM Repair 接入**：用 LLM 替换/增强规则组件。
- **baseline comparison**：rule-based vs single-agent vs multi-agent + critic。

> 以上均为后续阶段，**当前第六步不实现**。
