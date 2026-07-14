# 第七阶段：One-Click Runner 与 Interactive Agent Shell

## 1. 为什么需要 Stage 7

前六阶段已经形成完整的"剖析 → 规划 → 执行 → 审查 → 修复 → 复审 → 报告"闭环，但运行方式存在明显短板：

- 用户必须**手动按顺序执行 7 条命令**（run_profile → run_planner → run_executor → run_critic → run_repair → run_critic → run_report_generator），每条命令都带一长串参数。
- 命令之间的依赖关系（profile 产物喂给 planner、prepared panel 喂给 critic、repaired panel 喂给复审 critic）全靠用户记在脑子里，漏一步或顺序错就报错。
- 没有统一的**状态视图**：用户无法一眼看出"现在跑到哪一步、哪一步失败、最终 validation status 是什么"。
- 没有**用户交互**：改 analysis_goal、看失败项、看 approved features、打开报告，都要用户自己去找文件路径。

Stage 7 的目标不是新增数据处理能力，而是**把运行方式和用户交互升级成 agent-like**：一键运行 + 交互式 shell。

---

## 2. 它解决了导师反馈中的什么问题

导师反馈的核心是：**目前运行方式需要手动执行一大堆脚本，不够像 agent，也缺少用户交互。**

Stage 7 针对性地回应：

| 导师反馈的问题 | Stage 7 的解决方式 |
|---|---|
| 手动执行一大堆脚本 | `python src/run_all.py` 一键运行完整 workflow |
| 不够像 agent | `python src/agent_shell.py` 交互式 shell，自然语言反馈 + 下一步建议 |
| 缺少用户交互 | shell 支持 `set goal` / `set input_dir` / `run <stage>` / `show failures` / `show features` / `open report` |
| 看不到整体状态 | `status` 命令 + run_all 的 summary dashboard 一眼看清 7 阶段状态与最终 validation status |
| 失败项难定位 | `show failures` 直接列出 failed/warning 检查项及证据 |
| approved features 难确认 | `show features` 明确显示 label_next_5d 是否进入特征 |

**重要边界**：Stage 7 只优化运行方式和用户交互，**不接入真实市场数据、不改动前六阶段核心逻辑、不训练模型、不输出投资建议、不做 Streamlit、不做多 Agent 投票**。

---

## 3. One-Click Runner 的设计

### 3.1 架构

```
run_all.py (CLI 入口)
     │
     ▼
PipelineRunner (src/pipeline_runner.py)
     │  复用前六阶段内部类：
     ├── FinancialTableProfiler   (Stage 1)
     ├── WorkflowPlanner           (Stage 2)
     ├── CodeExecutor             (Stage 3)
     ├── ValidityCritic           (Stage 4 / 6)
     ├── RepairLoop               (Stage 5)
     └── ReportGenerator          (Stage 7 报告)
     │
     ▼
outputs/sessions/latest_session.json + session_YYYYMMDD_HHMMSS.json
```

`PipelineRunner` 是统一调度器，**不复制粘贴业务逻辑**，而是直接 import 并调用前六阶段的内部类（与各 `run_*.py` CLI 调用的是同一批类），保证行为一致、原有 CLI 仍可独立使用。

### 3.2 阶段状态记录

每个阶段运行后记录：

- `stage_name`
- `status`：`pending` / `running` / `completed` / `failed` / `skipped`
- `start_time` / `end_time` / `duration_seconds`
- `output_files`
- `summary`
- `error_message`（失败时含 traceback）

### 3.3 auto_repair 逻辑

- 运行 initial critic 后读取 `overall_status`。
- 若 `failed` 且 `auto_repair=True`：自动运行 **有界多轮 Remediation Agent**
  （Observe → Decide → Act → Reflect，`max_repair_rounds` 默认 3）+ repaired critic。
- 若 `passed` / `passed_with_warnings`：跳过 repair 与 repaired critic，在 log 里标记
  `skipped` 并写明原因；仍写 `repair_history.json`（0 轮，`termination_reason=validation_passed`）。
- 若 `--no_repair`：即使 failed 也跳过 repair（标记 skipped，`termination_reason=repair_disabled`）。

### 3.4 Remediation Agent 状态字段（v2）

`get_status()` / `status` / `show summary` 新增展示：

- `repair_rounds`：实际运行的轮数
- `termination_reason`：`validation_passed` / `no_actionable_strategy` / `no_progress` /
  `max_rounds_reached` / `manual_review_required` / `stage_failed` / `repair_disabled`
- `unresolved_checks`：未能自动修复的 failed check 名
- `manual_review_required`：是否需要人工介入

### 3.5 session log

- 每次 `run_full_pipeline` 后生成 `outputs/sessions/latest_session.json`（覆盖，便于 shell 读取最新状态）。
- 同时生成带时间戳的 `outputs/sessions/session_YYYYMMDD_HHMMSS.json`（保留历史）。
- 失败不静默吞掉：`error_message` + traceback 写入 session log，终端也明确报错。

### 3.6 run_all.py 终端输出（summary dashboard）

```
[run_all] Financial Table Workflow Agent

Input dir: data/sample
Output root: outputs
Analysis goal: ...

Stage 1 Data Profiler .......... completed
Stage 2 Workflow Planner ....... completed
Stage 3 Code Executor .......... completed
Stage 4 Validity Critic ........ failed
Stage 5 Repair Loop ............ completed
Stage 6 Re-run Critic .......... passed_with_warnings
Stage 7 Final Report ........... completed

Final status: passed_with_warnings
Rows: 300 -> 298
Rows removed by repair: 2
Label leakage: passed
Approved features: 8
Final report: outputs/final_report/final_workflow_report.md
One-page summary: outputs/final_report/final_workflow_one_page.md
Session log: outputs/sessions/latest_session.json
```

### 3.7 可选参数

```
--input_dir data/sample
--output_root outputs
--analysis_goal "..."
--no_repair              # 即使 critic failed 也不自动修复
--max_repair_rounds 3    # Remediation Agent 最大轮数（默认 3）
--max_row_loss_ratio 0.05  # 累计删行 / 原始行数 上限（默认 5%），超过转人工
--skip_report           # 跳过 Final Report Generator
--clean_outputs         # 运行前清空 output_root
--verbose               # 打印详细进度与 traceback
```

---

## 4. Agent Shell 的设计

### 4.1 启动

```
python src/agent_shell.py
```

启动后显示：

```
Financial Table Workflow Agent Shell
Type 'help' to see available commands.
Current input_dir: data/sample
Current output_root: outputs

agent>
```

### 4.2 agent-like 交互

shell 不调用 LLM，但体现 agent-like 交互：

- 用户输入命令后，用自然语言反馈当前动作（`[agent] Running full pipeline ...`）。
- 每一步完成后给出下一步建议：
  - `Profiler completed. Next suggested step: run planner.`
  - `Critic found failed checks. Next suggested step: run repair.`
  - `Repair completed. Next suggested step: run recritic.`
  - `Final report generated. You can use 'open report'.`
- 模糊命令 intent mapping：
  - `run pipeline` / `full run` → `run all`
  - `summary` → `show summary`
  - `failures` → `show failures`
  - `features` → `show features`
  - `open final report` → `open report`
- 未知命令不崩溃：`I did not understand that command. Type 'help' for available commands.`

### 4.3 非交互测试模式

真正交互不便自动测试，因此提供：

```
python src/agent_shell.py --demo_commands
```

自动执行 `status` / `show summary` / `show failures` / `show features` 后退出，用于验证 shell 的只读命令链路。

---

## 5. 支持的命令列表

| 命令 | 作用 |
|---|---|
| `help` | 显示所有可用命令 |
| `status` | 显示当前 pipeline 状态（各阶段状态、输出文件存在性、最终 validation status） |
| `set goal <text>` | 设置 analysis_goal |
| `set input_dir <path>` | 设置输入数据目录 |
| `set output_root <path>` | 设置输出根目录 |
| `run all` | 运行完整 pipeline |
| `run profile` | 只运行 Data Profiler |
| `run planner` | 只运行 Workflow Planner |
| `run executor` | 只运行 Code Executor |
| `run critic` | 运行 initial critic |
| `run repair` | 运行 Repair Loop |
| `run recritic` | 对 repaired panel 重新运行 Critic |
| `run report` | 运行 Final Report Generator |
| `show summary` | 读取 final_workflow_summary.json，打印关键摘要 |
| `show failures` | 读取 validation_report，列出 failed/warning 检查项 |
| `show features` | 显示 approved features、label column、excluded columns，明确 label_next_5d 是否在 features 中 |
| `open report` | Windows 上用 os.startfile 打开 final_workflow_report.md |
| `open outputs` | 打开 outputs 文件夹 |
| `reset session` | 清空当前 shell session 状态（不删除 outputs 文件） |
| `exit` / `quit` | 退出 shell |

---

## 6. 为什么这更接近 Agent workflow

普通脚本流水线的特征是"用户记顺序、手动跑、跑完看文件"。Stage 7 让它更接近 agent workflow：

1. **统一调度**：PipelineRunner 把 7 个阶段封装成一个可编程对象，阶段依赖、auto_repair、skip 逻辑都在调度器里，用户不用记顺序。
2. **状态自省**：`get_status()` / `status` 命令随时给出"跑到哪一步、哪一步失败、最终 status"的快照，而不是让用户去翻文件。
3. **反馈驱动**：initial critic failed → 自动 Remediation Agent（有界多轮
   Observe → Decide → Act → Reflect）→ 自动 recritic，这是 agent 的"根据
   反馈自我修正"，不是"报错就停"。`max_repair_rounds` + `no_progress` +
   安全门保证不会无限循环。
4. **交互式意图理解**：shell 接受模糊命令（`full run`、`summary`、`failures`），给出自然语言反馈和下一步建议，像在和用户对话。
5. **可审计**：每次运行生成 session log（latest + timestamped），记录每阶段 status/duration/error，整个运行过程可追溯。

注意：这里的 "agent-like" 是**交互与调度层面**的，不是"调用 LLM"。所有逻辑仍是确定性规则，离线可运行。

---

## 7. 和前六阶段的关系

- **不删除/重写前六阶段代码**：`pipeline_runner.py` 只 import 并调用前六阶段的内部类（`FinancialTableProfiler` / `WorkflowPlanner` / `CodeExecutor` / `ValidityCritic` / `RepairLoop` / `ReportGenerator`），与各 `run_*.py` CLI 调用的是同一批类。
- **保持原有 CLI 兼容**：`run_profile.py` / `run_planner.py` / ... / `run_report_generator.py` 全部保留、可独立运行，开发者模式仍可单步调试某一阶段。
- **产物路径不变**：Stage 7 仍写入前六阶段约定的目录（`outputs/profiles`、`outputs/plans`、`outputs/prepared`、`outputs/validation`、`outputs/repaired`、`outputs/validation_repaired`、`outputs/final_report`），新增的只有 `outputs/sessions/`。
- **闭环结果不变**：仍是 300 → 298、failed → passed_with_warnings、label 隔离。

---

## 8. 为什么暂时不接入真实市场数据

- **职责边界**：Stage 7 只优化运行方式和用户交互，不扩大数据来源。
- **风险隔离**：真实数据源（券商行情、付费 API）涉及合规、稳定性、字段口径差异，需要单独验证，不应混进运行方式优化阶段。
- **可复现性**：当前用 `generate_sample_data.py` 生成的模拟数据，保证 workflow 逻辑可离线、可复现地验证。
- **接口已预留**：数据入口通过 `--input_dir` 和 shell 的 `set input_dir` 暴露。将来只要把真实数据转换成 `price.csv` / `volume.csv` / `fundamentals.csv` / `industry.csv` / `calendar.csv` 这 5 张表的格式，就可以直接复用现有 workflow，无需改 Stage 1-6 代码。

---

## 9. 后续如何接入真实数据源

真实市场数据源接入**暂不在本项目实现**，后续会单独建立一个小项目验证免费历史数据源，例如：

- 公开历史行情 CSV（交易所/券商官网导出）
- Kaggle 金融数据集
- Yahoo Finance CSV 导出
- Stooq 历史行情
- AKShare / Tushare 等开源金融数据接口

验证流程（在独立小项目里完成）：

1. 选定数据源，确认免费、可离线导出、字段覆盖 price/volume/fundamentals/industry/calendar。
2. 写一个适配器，把数据源导出/下载成 `price.csv` / `volume.csv` / `fundamentals.csv` / `industry.csv` / `calendar.csv` 五张表（字段口径对齐本项目 schema）。
3. 在本项目里 `set input_dir <真实数据目录>` 或 `python src/run_all.py --input_dir <真实数据目录>`，复用现有六阶段 workflow。
4. 真实数据接入后**仍不做投资建议、不训练模型**，只验证 workflow 在真实数据上的有效性审查能力。

---

## 10. 下一阶段计划

- **真实数据源验证项目**：单独小项目，验证免费历史市场数据源，再接入主 workflow。
- **LLM-based Planner / Critic / Repair**：用 LLM 替换/增强规则组件（接口已就绪，输出结构不变即可无缝替换）。
- **Multi Planner Voting**：多个 Planner 各出方案，投票/择优，提升鲁棒性。

> 以上均为后续阶段。当前 Stage 7 只做 One-Click Runner + Interactive Agent Shell，已完成并自洽。
