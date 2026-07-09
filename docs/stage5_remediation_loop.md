# 第五阶段：Remediation / Repair Loop

## 1. 为什么需要 Repair Loop

前四阶段形成了"剖析 → 规划 → 执行 → 审查"的单向流水线，但 Critic 发现 `close` 缺失导致 `overall_status = failed`。
如果没有修复闭环，流水线就停在"发现问题"这一步，无法把 panel 推进到真正 analysis-ready。

Repair Loop 的作用是：**读取 Critic 的 failed/warning 项，生成可解释的修复方案，执行确定性修复，
再交回 Critic 复审**，形成"审查 → 修复 → 再审查"的闭环，直到 panel 通过有效性审查。

这正是 Agent workflow 相比一次性脚本的核心价值：**能根据反馈自我修正，而非只报错就结束**。

---

## 2. Repair Loop 在整体 pipeline 中的位置

```
prepared_panel.csv ──► [Validity Critic] ──► validation_report.json
                                                      │
                                            (failed?) │
                                                      ▼
                                          [Repair Loop]
                                          ├── repair_plan.json
                                          ├── repaired_panel.csv
                                          ├── repair_log.json
                                          └── repair_report.md
                                                      │
                                                      ▼
                                          [Validity Critic] (复审)
                                                      │
                                            (passed?) │
                                                      ▼
                                              analysis-ready
```

- **Critic**：判定 panel 是否合格，给出 failed/warning 证据。
- **Repair Loop**：消费 Critic 的结论，针对 failed 项生成修复方案并执行。
- 修复后**必须重新运行 Critic** 复审，确认失败已解决。

---

## 3. 输入输出

### 输入

- `outputs/prepared/prepared_panel.csv`
- `outputs/prepared/data_dictionary.json`
- `outputs/validation/validation_report.json`
- `outputs/validation/approved_feature_columns.json`

### 输出

- `outputs/repaired/repair_plan.json`：修复方案（哪些 failed、用什么策略、影响多少行）
- `outputs/repaired/repaired_panel.csv`：修复后的 panel
- `outputs/repaired/repair_log.json`：修复执行日志（行数变化、修复后自检）
- `outputs/repaired/repair_report.md`：人类可读修复报告

### 运行命令

```bash
python src/run_repair.py \
  --panel_path outputs/prepared/prepared_panel.csv \
  --validation_report_path outputs/validation/validation_report.json \
  --data_dictionary_path outputs/prepared/data_dictionary.json \
  --approved_features_path outputs/validation/approved_feature_columns.json \
  --output_dir outputs/repaired
```

修复后复审：

```bash
python src/run_critic.py --panel_path outputs/repaired/repaired_panel.csv \
  --data_dictionary_path outputs/prepared/data_dictionary.json \
  --execution_log_path outputs/prepared/execution_log.json \
  --plan_path outputs/plans/workflow_plan.json \
  --executor_source_path src/executor.py \
  --calendar_path data/sample/calendar.csv \
  --output_dir outputs/validation_repaired
```

---

## 4. 当前修复策略

当前重点修复 `missing_rate_after_join` 中 `close` 缺失导致的 failed。

**策略：删除 `close` 缺失行（保守策略，不默认插值）。**

理由：
- `close` 是核心行情字段，`return_1d`/`return_5d`/`volatility_20d`/`label_next_5d` 都依赖它；
- 对模拟数据和建模宽表，删除 2/300 行比插值更保守，避免凭空制造价格点；
- 插值可能引入人为的收益/波动模式，污染下游建模。

对真实业务数据的说明（写入 report）：
- 更理想的方式是重新拉取原始行情或按 ticker 时间序列插值/复权重拉；
- 当前 baseline 选择保守删除。

不修复的项（记入 `not_repaired_items`）：
- pe/pb/roe 高缺失：财务公告频率低是合理现象，仅 warning，不修复；
- industry_name 缺失：模拟数据设计（一个 ticker 缺行业），保留原样，下游可编码为 'unknown'。

---

## 5. 修复后自检

Repair Loop 在删除行后立即做自检（写入 `repair_log.checks_after_repair`）：
- `close_missing_count` 应为 0
- `primary_key_unique` 应为 True
- `label_column_preserved` 应为 True（label_next_5d 仍在）
- `label_not_in_approved_features` 应为 True（approved_feature_columns 不变，仍不含 label）

自检通过后，仍需重新运行 Critic 做完整 15 项复审，确认 `overall_status` 从 failed 变为 passed/passed_with_warnings。

---

## 6. 当前确定性 baseline 的限制

- 修复为确定性规则，不调用 LLM。
- 当前只实现 `close` 缺失的"删除行"策略；其他 failed 项（如有）记入 `not_repaired_items`，需人工处理。
- 最小修复**不重算**剩余行的依赖字段（return/volatility/label）；对当前样本，因 rolling 用 `min_periods=1`，删除行不会破坏既有窗口。完整修复应回到 executor 在修复后的输入上重跑。
- 未训练模型、未输出投资建议、未连接真实券商系统。

---

## 7. 闭环价值：Agent workflow 的自我修正

传统一次性脚本的流程是：跑 → 报错 → 人工介入。
Repair Loop 体现的 Agent workflow 价值是：

1. **反馈驱动**：Critic 的 failed 项直接驱动修复方案生成，而非人工读日志猜该改什么。
2. **可解释**：每个修复动作有 `target_check`/`strategy`/`reason`/`risk`，审计可追溯。
3. **闭环可验证**：修复后必须重新跑 Critic，"修没修好"由独立审查器判定，而非修复器自说自话。
4. **可扩展**：后续可接入 LLM 生成更复杂修复策略，或做多轮修复直到收敛。

---

## 8. 和临床 capstone 的迁移关系

金融修复闭环与临床数据修复闭环同构：

| 金融场景 | 临床场景 | 共性问题 |
|---|---|---|
| close 缺失 → 删除行/重拉行情 | 关键检验值缺失 → 删除记录/补录 | 核心字段缺失的修复策略 |
| Critic failed → Repair → 再 Critic | 有效性审查未过 → 修复 → 再审查 | 审查-修复-复审闭环 |
| 保守删除 vs 插值（避免污染建模） | 保守删除 vs 插补（避免污染队列分析） | 修复策略的保守性权衡 |
| 修复后重算依赖字段 | 修复后重算衍生变量 | 修复的下游传播 |

迁移要点：把"读审查报告 → 生成修复方案 → 执行 → 复审"的闭环抽象成与领域无关的接口，
后续可同时服务金融 panel 与临床 cohort 的修复。
