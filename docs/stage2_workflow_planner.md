# 第二阶段：Workflow Planner Agent

## 1. 为什么需要 Planner

第一阶段的 Data Profiler 只回答了"数据长什么样、有什么问题"，
但没有回答"为了得到 analysis-ready 宽表，下一步该按什么顺序做什么"。

Workflow Planner 的作用就是：**把 profiler 的发现 + 下游分析目标，
翻译成一份有序、可执行、可校验的数据准备计划**。

它解决的核心问题：

- 字段口径不一致（`trade_date` vs `date`、`ticker` vs `stock_code`）该在哪一步统一？
- 重复主键该去重还是聚合？由谁确认？
- 财务数据有公告滞后，该用 `report_date` 还是 `announce_date` 对齐？
- 哪些特征会引入未来函数？标签如何隔离？
- 最终要产出哪些文件？

这些问题如果直接交给 Code Executor 写代码，很容易遗漏顺序与泄漏风险；
Planner 先把"做什么、为什么、风险是什么"定下来，再让 Executor 执行。

---

## 2. Planner 和 Profiler 的关系

```
raw tables → [Data Profiler] → profile.json
                                      │
                                      ▼
                          [Workflow Planner] ← analysis_goal
                                      │
                                      ▼
                            workflow_plan.json
                                      │
                                      ▼
                          [Code Executor]（下一阶段）
```

- **Profiler**：被动剖析，输出"数据画像 + 问题清单"（事实层）。
- **Planner**：主动规划，读取画像与目标，输出"步骤 + 风险 + 校验项"（决策层）。
- Planner 不重新剖析数据，而是**消费** profiler 的结论；
  因此 profiler 检测得越准，planner 的计划越有针对性。

---

## 3. 输入输出

### 输入

- `outputs/profiles/profile.json`（第一阶段产物）
- `analysis_goal`（下游分析目标，默认为 5 日收益率预测 / 因子分析）

### 输出

- `outputs/plans/workflow_plan.json`：机器可读的结构化计划
- `outputs/plans/workflow_plan_report.md`：人类可读的报告

### 运行命令

```bash
python src/run_planner.py --profile_path outputs/profiles/profile.json --output_dir outputs/plans
# 可选自定义目标：
python src/run_planner.py --profile_path outputs/profiles/profile.json --output_dir outputs/plans --analysis_goal "..."
```

---

## 4. 当前确定性规则版本的作用

`src/planner.py` 中的 `WorkflowPlanner` 是**确定性规则版本**：

- 不调用任何 LLM API，离线可运行。
- 根据 `profile.json` 的实际内容**动态生成**步骤与风险项，而非写死静态 JSON。
- 例如：
  - 若 `price.csv` 检出重复主键 → `validate_primary_keys` 步骤引用该 issue 并标记去重策略需人工确认。
  - 若 `cross_table_findings.schema_inconsistencies` 存在 → `standardize_column_names` 步骤引用具体不一致项。
  - 若 `fundamentals.csv` 有 `report_date` + `announce_date` → 生成 look-ahead bias 假设，并在 `align_fundamentals_by_announce_date` 强调用 `announce_date`。
  - 若 `calendar.csv` 存在 → 生成 `align_with_trading_calendar` 步骤。
  - 若 `industry.csv` 有缺失 → `merge_industry` 加入 warning。
  - 若某表缺失率高 → `final_missing_and_quality_checks` 加入对应检查。
  - 若 price/volume 覆盖不一致 → `merge_price_and_volume` 加入 warning。

它生成 13 个 workflow steps、12 个 validation checks、8 个特征 + 1 个标签 + 4 类排除列。

**本阶段只规划，不执行代码，不生成 `prepared_panel.csv`。**

---

## 5. 后续如何替换或增强为 LLM Planner

确定性版本的局限：规则固定，遇到更复杂的 schema、更多表、更细的清洗策略时扩展性有限。

替换路径：

1. **Prompt 模板已就绪**：`prompts/workflow_planner_prompt.md` 是一份可复用的 LLM Planner Prompt，
   包含 system role、输入输出格式、硬性原则（防未来函数、标签隔离等）、动态化要求、必须包含的 steps/checks。
2. **接口不变**：LLM Planner 只需输出与 `WorkflowPlanner.build_plan` 相同结构的 JSON，
   下游 `run_planner.py` / Code Executor 无需改动。
3. **混合策略**：可先用确定性版本生成 baseline plan，再用 LLM 做增强/补全/多方案投票（Multi Planner Voting）。
4. **校验兜底**：无论谁生成的 plan，都应过一遍结构校验 + Validity Critic，防止 LLM 偷偷把未来函数写进特征。

---

## 6. 和临床 capstone 的迁移关系

金融数据准备与临床数据准备的规划痛点同构，Planner 的方法论可双向迁移：

| 金融场景 | 临床场景 | 共性问题 |
|---|---|---|
| 金融 workflow planning（去重→统一字段→对齐→特征→标签→校验） | 临床 preprocessing planning（去重→统一编码→对齐事件时间→特征→标签→校验） | 多源脏表 → 有序清洗计划 |
| 金融 look-ahead bias（财务用 announce_date 滞后） | 临床时间泄漏（检验结果用报告时间而非采集时间） | 时间因果性 / as-of 对齐 |
| 金融 ticker-date panel（每行一个标的-交易日） | 临床 patient-time cohort table（每行一个患者-时间点） | 实体-时间宽表 |
| 金融 validation plan（主键唯一/标签隔离/无未来函数） | 临床数据有效性审查（唯一 ID/标签隔离/无时间泄漏） | analysis-ready 校验清单 |

迁移要点：把 Planner 的"步骤模板 + 风险项 + 校验项"抽象成与领域无关的接口，
后续可同时服务金融 panel 与临床 cohort 两类 analysis-ready 表。
