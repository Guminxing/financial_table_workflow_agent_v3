# 第五阶段：Remediation / Repair Loop（有界多轮自我修正）

## 1. 为什么需要 Repair Loop

前四阶段形成了"剖析 → 规划 → 执行 → 审查"的单向流水线，但 Critic 发现 `close` 缺失导致 `overall_status = failed`。
如果没有修复闭环，流水线就停在"发现问题"这一步，无法把 panel 推进到真正 analysis-ready。

Repair Loop 的作用是：**读取 Critic 的 failed 项，生成可解释的修复方案，执行确定性修复，
再交回 Critic 复审**，形成"审查 → 修复 → 再审查"的闭环，直到 panel 通过有效性审查。

这正是 Agent workflow 相比一次性脚本的核心价值：**能根据反馈自我修正，而非只报错就结束**。

---

## 2. v2 升级：从"单轮"到"有界多轮 Remediation Agent"

v2（2026-07-14）把原来只执行一次的 Critic → Repair → Re-critic，升级为
**有界、多轮、可审计、自主停止**的 Remediation Agent。

### 2.1 循环模型：Observe → Decide → Act → Reflect

每一轮：

```
Observe  读取最新 validation_report（首轮用 initial，后续用上一轮复审结果）
   ↓
Decide   用 strategy registry 选可执行策略，或给出 termination_reason
   ↓
Safety check   累计删除行数 / 原始 panel 行数 ≤ max_row_loss_ratio（默认 5%）
   ↓
Act      在 panel 副本上 apply_selected；实际行数复核
   ↓
Reflect  对修复后 panel 重新运行 Critic；记录 panel 指纹与 failed check 集合
   ↓
Decide whether to continue
```

**下一轮必须基于上一轮 repaired panel 和最新 Critic 结果，不得重新使用最初的输入。**

### 2.2 停止条件（termination_reason）

| termination_reason | 含义 |
|---|---|
| `validation_passed` | Critic 复审通过（无 failed check），正常收敛 |
| `no_actionable_strategy` | 没有任何策略能处理当前 failed check → 人工 |
| `no_progress` | failed check 集合 + panel 指纹连续两轮不变 → 停止，禁止无限循环 |
| `max_rounds_reached` | 达到 `max_repair_rounds`（默认 3）仍未收敛 |
| `manual_review_required` | 策略存在但安全门未通过（如累计删行 > 5%）→ 人工 |
| `stage_failed` | Remediation Agent 内部异常 |

**关键**：如果 failed check 集合和 panel 指纹连续不变，必须停止，禁止无限循环。

### 2.3 这不是什么

- **不是无限重试**：有 `max_repair_rounds` 上界（默认 3），有 `no_progress` 早停。
- **不是让模型直接修改金融数据**：所有修复动作是确定性 Python/Pandas 策略，
  不调用 LLM、不执行动态代码、不跑任意 shell 命令修改 DataFrame。
- **不是"修好就行"**：不得通过填充虚假值来"修好"数据；不得伪造或回填
  `announce_date`；不得修改 `label_next_5d` 的标签角色。

---

## 3. 安全修复策略注册表（strategy registry）

`src/repair.py` 把修复动作抽象成 `RepairStrategy` 协议，每个策略至少包含：

- `name`：策略名（snake_case，写入审计记录）
- `target_check`：针对的 Critic `check_name`
- `can_handle(failed_check, panel)`：是否能处理这个 failed check
- `estimated_affected_rows(failed_check, panel)`：预估影响行数（安全门预判）
- `risk`：风险描述
- `requires_confirmation`：是否需要人工确认（True 时即使能处理也走 manual review）
- `apply(panel, failed_check) -> (new_panel, action_detail)`：在副本上执行

### 3.1 已注册策略

| 策略 | target_check | 可达性 | 说明 |
|---|---|---|---|
| `drop_rows_with_missing_core_price` | `missing_rate_after_join` | Agent 自动调用 | 删除 `close` 缺失行（保守，不插值） |
| `drop_exact_duplicate_rows` | `primary_key_uniqueness` | Agent 自动调用 | 删除**内容完全一致**的重复行（keep first） |
| `trim_industry_name_whitespace` | `source_flags_consistency` | **dormant / 手动** | 清理 `industry_name` 首尾空格（不伪造行业） |

> `trim_industry_name_whitespace` 的 target_check 在当前 Critic 中是 **warning** 而非
> failed，而 Remediation Agent 默认只处理 failed checks，因此该策略在真实流程中
> **不会被 Agent 自动调用**。它作为 registry 中的 dormant / manual utility 保留，
> 供未来 warning allowlist 或手动调用使用，不宣称会被 Agent 自动触发。**安全约束**：
> 只对原本非空的字符串 strip；None / NaN / pd.NA 必须继续保持缺失，绝不转成
> "None"/"nan"/"<NA>"；空字符串清理后转为 pd.NA。

### 3.2 安全边界

- **必须保留** `drop_rows_with_missing_core_price`（向后兼容）。
- **未知的 failed check 不得猜测修复**，必须进入 manual review。
- 新策略只增加能够安全验证的策略；**不得通过填充虚假值来"修好"数据**。
- 每个策略必须有真实 Critic check 映射；不会被当前 Critic 触发的策略不作为主要验收能力。

---

## 4. 安全门

- 默认累计删除行数超过原始 panel 的 **5%** 时，不得自动执行；标记
  `manual_review_required`。阈值由 `--max_row_loss_ratio` 覆盖。
- 行损安全门**必须在 DataFrame 副本上按实际执行结果复核**：预估通过后 apply，
  再用实际 `rows_removed` 复算累计比例；超过阈值则**不保存修复后 panel**，
  回退到本轮输入，转人工。
- **不得伪造或回填 `announce_date`**。
- **不得修改 `label_next_5d` 的标签角色**；`label_next_5d` 永远不得进入
  `approved_feature_columns`。
- **不允许 LLM、动态代码或任意 shell 命令直接修改 DataFrame**。
- **原始 CSV 不得被覆盖**，只能生成派生产物（`repaired_panel.csv`）。

---

## 5. 审计记录：repair_history.json

新增 `outputs/repaired/repair_history.json`，至少记录每轮：

- `round`
- `validation_status_before`
- `failed_checks_before`
- `candidate_strategies`
- `selected_strategies`
- `decision_reason`
- `rows_before` / `rows_after`
- `cumulative_row_loss_ratio`
- `validation_status_after`
- `failed_checks_after`
- `panel_fingerprint`
- `termination_reason`

顶层还记录 `max_repair_rounds` / `max_row_loss_ratio` / `repair_rounds` /
`termination_reason` / `manual_review_required` / `unresolved_checks`。

**即使 blocked 或 failed，`repair_history.json` 仍然保存**（保证审计文件始终存在）。

同时保持原来的以下文件继续可用：

- `repair_plan.json`
- `repaired_panel.csv`
- `repair_log.json`
- `repair_report.md`
- `validation_repaired/validation_report.json`
- final report

---

## 6. Repair Loop 在整体 pipeline 中的位置

```
prepared_panel.csv ──► [Validity Critic] ──► validation_report.json
                                                      │
                                            (failed?) │
                                                      ▼
                                   [Remediation Agent]  (有界多轮)
                                   ├── repair_plan.json
                                   ├── repaired_panel.csv
                                   ├── repair_log.json
                                   ├── repair_report.md
                                   └── repair_history.json   (v2 新增)
                                                      │
                                                      ▼
                                          [Validity Critic] (复审)
                                                      │
                                            (passed?) │
                                                      ▼
                                              analysis-ready
```

- **Critic**：判定 panel 是否合格，给出 failed/warning 证据。
- **Remediation Agent**：消费 Critic 结论，每轮选策略、安全门复核、执行、复审、反思。
- 修复后**必须重新运行 Critic** 复审，确认失败已解决。

---

## 7. 输入输出

### 输入

- `outputs/prepared/prepared_panel.csv`
- `outputs/prepared/data_dictionary.json`
- `outputs/validation/validation_report.json`
- `outputs/validation/approved_feature_columns.json`

### 输出

- `outputs/repaired/repair_plan.json`：修复方案（哪些 failed、用什么策略、影响多少行）
- `outputs/repaired/repaired_panel.csv`：修复后的 panel（派生产物，不覆盖原始）
- `outputs/repaired/repair_log.json`：修复执行日志（行数变化、修复后自检）
- `outputs/repaired/repair_report.md`：人类可读修复报告
- `outputs/repaired/repair_history.json`：**v2 新增**，多轮审计记录

### 运行命令

一键运行（含多轮 Remediation Agent）：

```powershell
python src/run_all.py --max_repair_rounds 3 --max_row_loss_ratio 0.05
```

单轮 CLI（向后兼容，不走多轮）：

```bash
python src/run_repair.py \
  --panel_path outputs/prepared/prepared_panel.csv \
  --validation_report_path outputs/validation/validation_report.json \
  --data_dictionary_path outputs/prepared/data_dictionary.json \
  --approved_features_path outputs/validation/approved_feature_columns.json \
  --output_dir outputs/repaired
```

---

## 8. 修复后自检

Repair Loop 在删除行后立即做自检（写入 `repair_log.checks_after_repair`）：

- `close_missing_count` 应为 0
- `primary_key_unique` 应为 True
- `label_column_preserved` 应为 True（label_next_5d 仍在）
- `label_not_in_approved_features` 应为 True（approved_feature_columns 不变，仍不含 label）

自检通过后，仍需重新运行 Critic 做完整 15 项复审，确认 `overall_status` 从 failed 变为 passed/passed_with_warnings。

---

## 9. 闭环价值：Agent workflow 的自我修正

传统一次性脚本的流程是：跑 → 报错 → 人工介入。
Remediation Agent 体现的 Agent workflow 价值是：

1. **反馈驱动**：Critic 的 failed 项直接驱动修复方案生成，而非人工读日志猜该改什么。
2. **可解释**：每个修复动作有 `target_check`/`strategy`/`reason`/`risk`，审计可追溯。
3. **闭环可验证**：修复后必须重新跑 Critic，"修没修好"由独立审查器判定，而非修复器自说自话。
4. **有界自主停止**：`max_repair_rounds` + `no_progress` + 安全门，保证不会无限循环或越界删数据。
5. **可扩展**：后续可接入 LLM 生成更复杂修复策略（仍受安全门约束），或做多轮修复直到收敛。

---

## 10. 和临床 capstone 的迁移关系

金融修复闭环与临床数据修复闭环同构：

| 金融场景 | 临床场景 | 共性问题 |
|---|---|---|
| close 缺失 → 删除行/重拉行情 | 关键检验值缺失 → 删除记录/补录 | 核心字段缺失的修复策略 |
| Critic failed → Repair → 再 Critic | 有效性审查未过 → 修复 → 再审查 | 审查-修复-复审闭环 |
| 保守删除 vs 插值（避免污染建模） | 保守删除 vs 插补（避免污染队列分析） | 修复策略的保守性权衡 |
| 修复后重算依赖字段 | 修复后重算衍生变量 | 修复的下游传播 |
| 有界多轮 + 安全门 + 审计 | 有界多轮 + 删除上限 + 审计 | 自主停止与可审计 |

迁移要点：把"读审查报告 → 生成修复方案 → 执行 → 复审"的闭环抽象成与领域无关的接口，
后续可同时服务金融 panel 与临床 cohort 的修复。
