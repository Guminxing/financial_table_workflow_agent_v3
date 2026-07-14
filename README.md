# Financial Table Workflow Agent

面向金融表格的数据准备、质量审计与真实 A 股数据接入工作流。

> 详细文档见：[2026-07-13 项目说明](./readme_0713.md)

## 当前分支：v2

`v2` 分支基于 master 基线提交 `9da0980`（Stage 7 + Stage 8）创建独立 worktree 开发，原版 master 目录保持不变，后续改动仅在 v2 worktree 中进行。

## 文档导航

真实数据接入链路、运行命令、五张 CSV 数据契约、2026-07-13 实测验证结果、以及时间对齐与防泄漏设计均在详细文档中：

➡️ **[readme_0713.md](./readme_0713.md)**

## v2 升级（2026-07-14）：有界多轮 Remediation Agent

`v2` 把原来只执行一次的 Critic → Repair → Re-critic，升级为**有界、多轮、可审计、自主停止**的 Remediation Agent：

- 循环模型：**Observe → Decide → Act → Reflect**（不是无限重试，也不是让模型直接改金融数据）。
- 停止条件：`validation_passed` / `no_actionable_strategy` / `no_progress` / `max_rounds_reached` / `manual_review_required` / `stage_failed`。
- 安全门：累计删行 > 原始 panel 的 5%（`--max_row_loss_ratio`）转人工；不伪造 `announce_date`；`label_next_5d` 永不进 `approved_feature_columns`；原始 CSV 不被覆盖。
- 策略注册表：`drop_rows_with_missing_core_price` / `drop_exact_duplicate_rows` / `trim_industry_name_whitespace`；未知 failed check 走 manual review，绝不猜测。
- 审计：`outputs/repaired/repair_history.json` 记录每轮决策与指纹；即使 blocked/failed 也保存。
- CLI：`--max_repair_rounds` / `--max_row_loss_ratio`；`python src/run_all.py` 默认行为保持兼容。

详见 [docs/stage5_remediation_loop.md](./docs/stage5_remediation_loop.md) 与 [tests/test_remediation_agent.py](./tests/test_remediation_agent.py)。
