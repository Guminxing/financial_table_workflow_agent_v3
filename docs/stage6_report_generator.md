# 第六阶段：Final Report Generator

## 1. 为什么需要 Report Generator

前五阶段形成了完整的"剖析 → 规划 → 执行 → 审查 → 修复 → 复审"闭环，
但产物分散在 `outputs/profiles`、`outputs/plans`、`outputs/prepared`、
`outputs/validation`、`outputs/repaired`、`outputs/validation_repaired` 六个目录，
没有一个汇总视图。

Report Generator 的作用是：**只读**前五阶段产物，生成一份面向导师/审计的
最终总报告，把六阶段 workflow 与闭环结果讲清楚，并明确说明这不是"普通表格检查"，
而是 **task-aware analysis-ready workflow prototype**。

它不重新跑任何阶段、不训练模型、不输出投资建议，只做"汇总 + 讲清楚"。

---

## 2. 在整体 pipeline 中的位置

```
Stage 1 Profiler → Stage 2 Planner → Stage 3 Executor → Stage 4 Critic
   → Stage 5 Repair Loop → Stage 6 Re-run Critic
                                                              │
                                                              ▼
                                                  [Final Report Generator]
                                                  ├── final_workflow_summary.json
                                                  ├── final_workflow_report.md
                                                  ├── final_workflow_one_page.md
                                                  └── pipeline_artifacts_index.json
```

Report Generator 是流水线的**收口**：把前面所有阶段的产物与闭环结果，
浓缩成机器可读的 summary、人类可读的总报告、一页摘要、产物索引。

---

## 3. 输入输出

### 输入（全部只读）

- `outputs/profiles/profile.json`（Stage 1）
- `outputs/plans/workflow_plan.json`（Stage 2）
- `outputs/prepared/prepared_panel.csv`（Stage 3，初始 panel）
- `outputs/prepared/execution_log.json`（Stage 3）
- `outputs/prepared/data_dictionary.json`（Stage 3）
- `outputs/validation/validation_report.json`（Stage 4，初始 Critic）
- `outputs/repaired/repair_plan.json`（Stage 5）
- `outputs/repaired/repair_log.json`（Stage 5）
- `outputs/repaired/repaired_panel.csv`（Stage 5，修复后 panel）
- `outputs/validation_repaired/validation_report.json`（Stage 6 复审 Critic）
- `outputs/validation_repaired/approved_feature_columns.json`（Stage 6 复审）

### 输出

- `outputs/final_report/final_workflow_summary.json`：机器可读六阶段汇总
- `outputs/final_report/final_workflow_report.md`：人类可读总报告（含 Mermaid 架构图 + "Why This Is More Than Table Checking"）
- `outputs/final_report/final_workflow_one_page.md`：一页摘要（适合直接发导师）
- `outputs/final_report/pipeline_artifacts_index.json`：全部产物文件索引

### 运行命令

```bash
python src/run_report_generator.py \
  --profile_json outputs/profiles/profile.json \
  --workflow_plan_json outputs/plans/workflow_plan.json \
  --prepared_panel outputs/prepared/prepared_panel.csv \
  --execution_log outputs/prepared/execution_log.json \
  --initial_validation_report outputs/validation/validation_report.json \
  --repair_plan outputs/repaired/repair_plan.json \
  --repair_log outputs/repaired/repair_log.json \
  --repaired_panel outputs/repaired/repaired_panel.csv \
  --final_validation_report outputs/validation_repaired/validation_report.json \
  --approved_features outputs/validation_repaired/approved_feature_columns.json \
  --data_dictionary outputs/prepared/data_dictionary.json \
  --output_dir outputs/final_report
```

---

## 4. 四份产物说明

### `final_workflow_summary.json`

机器可读汇总，顶层含三个便于脚本断言的关键字段：
`initial_validation_status`、`final_validation_status`、`rows_removed_by_repair`；
嵌套 `closed_loop_result`（initial_rows / initial_status / failed_check /
failed_reason / rows_removed / repaired_rows / final_status /
label_not_in_approved_features / one_line）；以及 `pipeline_stages`、
`profile_summary`、`plan_summary`、`execution_summary`、`panel_summary`、
`approved_feature_columns`、`label_column`、`excluded_columns`、`limitations`。

### `final_workflow_report.md`

人类可读总报告，含：
- Executive Summary（闭环结果）
- **Mermaid 架构图**（六阶段 + 300→298 + failed→passed_with_warnings）
- **Why This Is More Than Table Checking**（必含小节）
- Stage-by-stage（1-6 每段输入/输出/关键结论）
- Closed-loop deep dive（300→298 明细 + 自检表）
- Approved features & label isolation
- Limitations
- Next steps

### `final_workflow_one_page.md`

一页摘要，适合直接发导师：项目目标、五个模块、闭环结果（明确写出
Critic 发现 2 行 close 缺失 → Repair 删除 2 行 → 复审 passed_with_warnings；
label_next_5d 不在 approved features）、为什么重要、下一步。

### `pipeline_artifacts_index.json`

按 stage 列出每个产物文件 `{stage, path, description, exists}`，
`exists` 用 `Path.exists()` 实算，便于一眼看出哪些产物已就绪。

---

## 5. 为什么"不只是表格检查"

普通表格检查问"数据干不干净"（缺失/重复/dtype/异常），必要但远不足以建模。
本 workflow 问的是更难、task-aware 的问题：**这份数据能不能安全地喂给一个
时间序列模型而不泄漏未来？**

具体体现在：
1. **task-aware 规划**：Planner 读 profile + 下游分析目标，输出有序、
   依赖明确、每步标注泄漏风险的计划，而非通用清洗配方。
2. **未来函数构造性预防**：rolling/pct_change 按 ticker 分组只用历史窗口；
   财务按 `announce_date` as-of 对齐，绝不用 `report_date`。
3. **label leakage 预防**：`label_next_5d` 用 `shift(-5)` 生成，标注 `role=label`，
   结构性排除出 `approved_feature_columns`。
4. **时间有效性**：plan 要求 time-based train/test 切分，Critic 强制检查。
5. **源码级静态分析**：Critic 读 `executor.py` 源码验证 `merge_asof` + `announce_date`
   且无非 label 的 `shift(-k)`。
6. **闭环自我修正**：Critic failed → Repair → 再 Critic 独立复审。

---

## 6. 当前确定性 baseline 的限制

- Report Generator 为确定性规则，不调用 LLM。
- 只读前五阶段产物，不重新跑任何阶段、不重算任何字段。
- 不训练模型、不输出投资建议、不连接真实券商系统、不做 Streamlit、不做多 Agent 投票。
- 使用真实市场数据（经适配器抓取），非合成样例数据。

---

## 7. 闭环价值回顾

整个六阶段闭环的价值在于：**能根据反馈自我修正，而非只报错就结束**。

- 初始 300 行 → Critic failed（close 缺 2 行）→ Repair 删 2 行 → 298 行 →
  复审 passed_with_warnings。
- label 始终隔离于特征之外。
- 每一步可追溯、可解释、可独立复审。

Report Generator 把这个闭环浓缩成一份可交付的总报告，让导师/审计一眼看清
"做了什么、为什么、结果如何、下一步"。

---

## 8. 和临床 capstone 的迁移关系

金融最终报告与临床最终报告同构：

| 金融场景 | 临床场景 | 共性问题 |
|---|---|---|
| 六阶段 workflow 总报告 | 临床数据准备 pipeline 总报告 | 多阶段产物的汇总与闭环呈现 |
| 300→298 行 + failed→passed_with_warnings | cohort 行数变化 + 审查状态变化 | 闭环结果的量化呈现 |
| task-aware analysis-ready panel | task-ready cohort table | 围绕下游建模目标的准备 |
| Mermaid 架构图 + 一页摘要 | pipeline 架构图 + 一页摘要 | 面向导师的可交付呈现 |

迁移要点：把"读各阶段产物 → 汇总闭环结果 → 生成总报告/一页摘要/产物索引"
抽象成与领域无关的接口，后续可同时服务金融 panel 与临床 cohort 两类
analysis-ready 表的最终交付。
