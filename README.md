# Financial Table Workflow Agent (v3)

面向金融表格的数据准备、质量审计与真实 A 股数据接入工作流。
**v3 只使用真实市场数据作为正式输入**；合成样例数据及其自动生成逻辑已彻底移除。

> 目录职责一览见 [DIRECTORY_GUIDE.md](./DIRECTORY_GUIDE.md)；分阶段设计见 [docs/](./docs/)。

---

## 1. 项目目标

把原始金融表格（行情、成交、财务、行业、交易日历）自动
**剖析 → 规划 → 执行 → 审查 → 修复 → 复审 → 汇总**，生成可用于分析建模的
**analysis-ready 宽表**，并用一个**有界、可审计、自主停止**的 Remediation Agent
闭环保证数据有效性。确定性实现，不调用外部 LLM API，离线可运行。

## 2. Agent 工作流程

每一轮 Remediation Agent：

```
Observe（读最新 Critic 报告）
   → Decide（用策略注册表选可执行策略，或给出终止原因）
   → Act（在 panel 副本上执行修复；安全门在实际行数上复核）
   → Re-critic（对修复后 panel 重新运行 Critic）
   → Reflect（记录 panel 指纹与 failed check 集合）
   → Stop（满足终止条件即停，绝不无限重试）
```

终止条件：`validation_passed` / `no_actionable_strategy` / `no_progress` /
`max_rounds_reached` / `manual_review_required` / `stage_failed`。

## 3. 只使用真实市场数据

- 正式输入：用户用 `src/run_fetch_real_data.py` 下载的真实 A 股数据，放在 `data/real_market/`。
- 合成样例数据 `data/sample/` 与生成器 `src/generate_sample_data.py` 已在 v3 移除。
- 输入目录不存在、为空或缺少 CSV 时，程序**明确失败**并给出可操作错误信息，**绝不**静默回退到合成数据。
- 不伪造 `announce_date`、不把当前基本面快照回填到历史日期、不伪造行情/行业字段。

## 4. 运行代码 / 测试代码 / 测试数据在哪里

| 类型 | 位置 |
|---|---|
| 运行代码 | `src/` |
| 测试代码 | `tests/` |
| 真实测试数据（小型 fixture） | `test_data/real_market_sample/` |
| 用户运行时下载的真实数据 | `data/real_market/`（不提交 Git） |
| 运行结果 | `outputs_real/`（不提交 Git） |
| 文档 | `docs/`、`prompts/`、`README.md`、`DIRECTORY_GUIDE.md` |

详见 [DIRECTORY_GUIDE.md](./DIRECTORY_GUIDE.md)。

## 5. 最小真实数据运行命令

**第一步：下载真实市场数据**

```bash
python -B src/run_fetch_real_data.py --tickers 600519 ^
  --start_date 2024-01-01 --end_date 2024-01-10 ^
  --output_dir data/real_market ^
  --tradingagents_path D:\dwzq\TradingAgents-astock-main ^
  --no_snapshot_fundamentals
```

**第二步：运行完整 pipeline**

```bash
python -B src/run_all.py --input_dir data/real_market --output_root outputs_real
```

**第三步：体验 Agent Shell**

```bash
python -B src/agent_shell.py --input_dir data/real_market --output_root outputs_real
```

> 也可用一键命令 `python -B src/run_fetch_real_data.py ... --run_pipeline --output_root outputs_real`
> 一次完成抓取 + 流水线；但 README 同时给出上面的分步方式。

## 6. 如何运行测试

```bash
python -B -m unittest discover -s tests -v
```

集成测试使用 `test_data/real_market_sample/` 真实 fixture；故障场景（缺失/重复/
no_progress/max_rounds）注入到 fixture 的**临时副本**，不修改被提交的真实 fixture。

## 7. Agent 的安全边界

- **有界轮数**：`--max_repair_rounds`（默认 3），绝不无限重试。
- **5% 默认累计删行安全门**：`--max_row_loss_ratio`（默认 0.05），超过即转人工。
- **不伪造金融数据**：不伪造 `announce_date`、不回填基本面快照、不伪造行情/行业。
- **不修改标签角色**：`label_next_5d` 永不进入 `approved_feature_columns`。
- **无法安全修复时转人工**：未知 failed check 走 `manual_review_required`，绝不猜测。
- **确定性、可审计**：当前是确定性 Agent，不是 LLM 直接改金融数据；每轮决策与指纹写入
  `outputs_real/repaired/repair_history.json`，即使 blocked/failed 也保存。

## 8. GitHub 不提交的内容

`data/real_market/`、`outputs_real/`、缓存、session 日志、凭据均被 `.gitignore` 忽略。
唯一提交的真实数据是 `test_data/real_market_sample/` 下的小型测试 fixture。
