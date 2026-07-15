# Code Structure and Execution Flow

> 本文件是 **v3** 的代码结构说明，供导师或新开发者快速理解：项目有哪些代码文件、
> 每个文件负责什么、从哪个入口启动、Pipeline 各阶段如何调用、Agent 循环位于哪里、
> 测试覆盖哪些模块、输入数据与输出产物在哪里。
>
> - v3 **正式输入只使用真实市场数据**（合成样例数据及其自动生成逻辑已彻底移除）。
> - `src/` 是**运行代码**。
> - `tests/` 是**测试代码**。
> - `test_data/real_market_sample/` 是**小型真实市场测试 fixture**（提交 Git，仅用于测试与最小演示）。
> - `data/real_market/` 与 `outputs_real/` 是**运行时目录**（不提交 Git，每次运行重新生成）。
>
> 目录职责一览见 [DIRECTORY_GUIDE.md](DIRECTORY_GUIDE.md)；分阶段设计见 [docs/](docs/)。

---

## 1. 项目结构树

以下结构根据 `git ls-files` 实际跟踪的文件生成，展开到文件级别。**不包含**
`__pycache__/`、`*.pyc`、`.git/`、临时测试目录、下载缓存、`outputs_real/` 中的
实际运行产物、`data/real_market/` 中用户运行时下载的大规模数据。

```
financial_table_workflow_agent_v3/
├── src/                                # 运行代码（全部提交 Git）
│   ├── __init__.py                     # 包标记
│   ├── profiler.py                     # Stage 1: FinancialTableProfiler 数据剖析
│   ├── run_profile.py                  # Stage 1 CLI 入口
│   ├── planner.py                     # Stage 2: WorkflowPlanner 工作流规划
│   ├── run_planner.py                  # Stage 2 CLI 入口
│   ├── executor.py                    # Stage 3: CodeExecutor 生成 analysis-ready 宽表
│   ├── run_executor.py                 # Stage 3 CLI 入口
│   ├── critic.py                      # Stage 4/6: ValidityCritic 有效性审查
│   ├── run_critic.py                   # Stage 4/6 CLI 入口
│   ├── repair.py                      # Stage 5: RepairLoop + 策略注册表
│   ├── run_repair.py                   # Stage 5 CLI 入口（单轮，向后兼容）
│   ├── report_generator.py             # Stage 7: ReportGenerator 最终报告
│   ├── run_report_generator.py         # Stage 7 CLI 入口
│   ├── pipeline_runner.py              # Stage 7: PipelineRunner 统一调度器 + Remediation Agent 多轮循环（Stage 9 增加 run_remediation_agent()；Stage 10 增加 run_noop_repair()）
│   ├── run_all.py                      # Stage 7: 一键运行入口（推荐主入口）
│   ├── agent_shell.py                  # Stage 7: 交互式 Agent Shell（固定命令模式，未替换）
│   ├── real_data_adapter.py            # Stage 8: 真实 A 股数据适配器
│   ├── run_fetch_real_data.py          # Stage 8: 真实数据抓取 CLI（可 --run_pipeline）
│   ├── agent_runtime/                  # Stage 9–10: Agent Runtime（模型驱动 tool-calling 骨架 + 确定性权限审批，不接入真实 LLM）
│   │   ├── __init__.py
│   │   ├── models.py                   # ToolCall/ToolResult/ToolSpec/AgentEvent/AgentRunResult/RiskLevel/StopReason(+awaiting_approval)/EventType(+policy/approval/tool_denied)
│   │   ├── context.py                  # AgentContext + run_id 隔离（路径穿越防护）
│   │   ├── registry.py                 # ToolRegistry + 基础 JSON Schema 校验
│   │   ├── model_client.py             # ModelClient Protocol（不依赖具体 SDK）
│   │   ├── policy.py                   # Stage 10: PolicyEngine + PolicyAction(allow/ask/deny) + PolicyConfig/Rule/Decision + PendingApproval/ApprovalResponse
│   │   └── runtime.py                  # 有界 tool-calling 循环 + 进程内审批恢复（run/resume，max_tool_turns + 重复检测）
│   └── agent_tools/                    # Stage 9: 领域工具（把 PipelineRunner 阶段包装成工具）
│       ├── __init__.py
│       └── pipeline_tools.py           # 10 个 pipeline 工具 + build_default_registry()（not_needed 分支走 run_noop_repair()）
├── tests/                              # 测试代码（全部提交 Git）
│   ├── __init__.py                     # 包标记
│   ├── test_remediation_agent.py       # Remediation Agent 单元/集成测试（23 项）
│   ├── test_tool_registry.py           # Stage 9: ToolRegistry + schema 校验测试
│   ├── test_agent_runtime.py           # Stage 9: AgentRuntime + AgentContext/run_id 测试（含 ScriptedFakeModel）
│   ├── test_pipeline_tools.py          # Stage 9: Pipeline 领域工具测试
│   ├── test_policy_engine.py           # Stage 10: PolicyEngine + 策略优先级测试（16 项）
│   └── test_runtime_approval.py        # Stage 10: 审批暂停/恢复 + 防篡改防重放 + 多 ToolCall 恢复测试（18 项）
├── test_data/                          # 测试数据
│   └── real_market_sample/             # 小型真实 A 股 fixture（提交 Git）
│       ├── README.md                   # fixture 来源、抓取命令、行数、免责声明
│       ├── fetch_metadata.json         # 可审计的抓取元数据
│       ├── price.csv                   # 真实 OHLC（7 行）
│       ├── volume.csv                   # 真实成交量（7 行；turnover 留空）
│       ├── fundamentals.csv             # 仅表头（--no_snapshot_fundamentals）
│       ├── industry.csv                 # 真实行业（1 行，白酒Ⅱ）
│       └── calendar.csv                 # 交易日历（10 行）
├── data/
│   └── real_market/                    # 用户运行时下载的真实数据（不提交 Git，仅 .gitkeep）
│       └── .gitkeep
├── outputs_real/                       # 正式运行产物（不提交 Git，仅 .gitkeep）
│   ├── .gitkeep
│   └── runs/                           # Stage 9: Agent Runtime 按 run_id 隔离的运行目录
│       └── <run_id>/                   # profiles/plans/prepared/validation/repaired/...
├── docs/                               # 分阶段设计文档
│   ├── project_scope.md                # 项目范围与第一阶段说明
│   ├── project_overview_zh.md           # 面向导师的项目总说明（六阶段汇总）
│   ├── stage2_workflow_planner.md       # Stage 2 设计
│   ├── stage3_code_executor.md         # Stage 3 设计
│   ├── stage4_validity_critic.md        # Stage 4 设计
│   ├── stage5_remediation_loop.md       # Stage 5 设计（有界多轮）
│   ├── stage6_report_generator.md      # Stage 6/7 报告生成设计
│   ├── stage7_agent_shell.md           # Stage 7 一键运行 + Agent Shell 设计
│   ├── stage8_real_data_adapter.md      # Stage 8 真实数据适配器设计
│   ├── stage9_agent_runtime_mvp.md      # Stage 9 Agent Runtime MVP 设计（不接入真实 LLM）
│   └── stage10_policy_and_approval.md   # Stage 10 PolicyEngine + 审批恢复设计
├── prompts/
│   └── workflow_planner_prompt.md      # LLM Planner Prompt 模板（供后续接入 LLM）
├── README.md                           # 项目总览 + 快速开始（v3）
├── DIRECTORY_GUIDE.md                  # 目录职责、数据与 Git 跟踪规则
├── CODE_STRUCTURE.md                   # 本文件：代码结构、模块职责、执行调用链
├── readme_0713.md                      # 2026-07-13 真实数据接入说明（历史快照）
├── requirements.txt                    # 依赖：pandas>=1.5.0, requests>=2.32.0
└── .gitignore                          # 忽略运行时数据/产物/缓存/凭据
```

> `src/` 共 27 个 Python 文件：1 个包标记（`__init__.py`）+ 9 个核心模块
> + 8 个 CLI 入口 + Stage 9–10 的 `agent_runtime/`（7 个文件，含 Stage 10 的 `policy.py`）
> 与 `agent_tools/`（2 个文件）。
> `tests/` 共 7 个 Python 文件（原 23 项 Remediation Agent 测试 + Stage 9 的
> `test_tool_registry.py` / `test_agent_runtime.py` / `test_pipeline_tools.py`
> + Stage 10 的 `test_policy_engine.py` / `test_runtime_approval.py`）。

> **Stage 9–10 说明**：`agent_runtime/` + `agent_tools/` 是模型驱动的 tool-calling
> Agent Runtime **骨架**，**不接入真实 LLM**（`ModelClient` 只定义 Protocol，
> 由测试中的 `ScriptedFakeModel` 驱动）。Stage 10 在 Runtime 上加入确定性
> `PolicyEngine`（allow/ask/deny）与进程内 `resume(ApprovalResponse)` 暂停/恢复。
> 原 `agent_shell.py` 仍是固定命令模式，未被替换。详见
> [docs/stage9_agent_runtime_mvp.md](docs/stage9_agent_runtime_mvp.md) 与
> [docs/stage10_policy_and_approval.md](docs/stage10_policy_and_approval.md)。

---

## 2. 正式运行入口

所有面向用户的入口都在 `src/` 下，统一用 `python -B src/<entry>.py` 从项目根目录运行。

| 入口文件 | 用途 | 主要输入 | 主要输出 | 定位 |
|---|---|---|---|---|
| `src/run_fetch_real_data.py` | 抓取真实 A 股数据（可选 `--run_pipeline` 直接跑流水线） | `--tickers` `--start_date` `--end_date` `--tradingagents_path` | `data/real_market/*.csv` + `fetch_metadata.json` | **数据获取入口**（需网络） |
| `src/run_all.py` | 一键运行完整 Pipeline（含 Remediation Agent） | `--input_dir data/real_market` `--output_root outputs_real` | `outputs_real/` 全部产物 + session log | **推荐主入口** |
| `src/agent_shell.py` | 交互式 Agent Shell（运行/查看状态/查看失败项/打开报告） | `--input_dir` `--output_root` | 同上（交互式） | **交互入口** |
| `src/run_profile.py` | 单独运行 Stage 1 Data Profiler | `--input_dir` `--output_dir` | `outputs_real/profiles/profile.json` + `.md` | 调试/单阶段 |
| `src/run_planner.py` | 单独运行 Stage 2 Workflow Planner | `--profile_path` `--output_dir` `--analysis_goal` | `outputs_real/plans/workflow_plan.json` + `.md` | 调试/单阶段 |
| `src/run_executor.py` | 单独运行 Stage 3 Code Executor | `--input_dir` `--plan_path` `--output_dir` | `outputs_real/prepared/prepared_panel.csv` 等 | 调试/单阶段 |
| `src/run_critic.py` | 单独运行 Validity Critic（初始或复审） | `--panel_path` `--data_dictionary_path` `--execution_log_path` `--plan_path` `--executor_source_path` `--calendar_path` `--output_dir` | `outputs_real/validation/validation_report.json` 等 | 调试/单阶段 |
| `src/run_repair.py` | 单独运行 Stage 5 Repair Loop（单轮，向后兼容） | `--panel_path` `--validation_report_path` `--data_dictionary_path` `--approved_features_path` `--output_dir` | `outputs_real/repaired/repaired_panel.csv` 等 | 调试/单阶段 |
| `src/run_report_generator.py` | 单独运行 Stage 7 Final Report Generator | 11 个前序产物路径 `--output_dir` | `outputs_real/final_report/*` | 调试/单阶段 |

**通常不需要用户直接运行的文件**：

- `src/pipeline_runner.py` — 统一调度器，被 `run_all.py` / `agent_shell.py` / `run_fetch_real_data.py --run_pipeline` 复用，不直接面向终端用户。
- `src/profiler.py` / `planner.py` / `executor.py` / `critic.py` / `repair.py` / `report_generator.py` / `real_data_adapter.py` — 核心类库，被上述 CLI 与 `pipeline_runner.py` import，不直接 `python src/xxx.py` 运行（`real_data_adapter.py` 直接运行只打印用法提示）。

---

## 3. Pipeline 执行流程

真实调用链（阶段名与顺序与 `pipeline_runner.py` 的 `STAGE_ORDER` / `STAGE_DISPLAY` 一致）：

```
真实数据源 (TradingAgents-astock-main, 只读依赖)
    ↓
src/run_fetch_real_data.py  →  src/real_data_adapter.py (fetch_real_data)
    ↓
data/real_market/*.csv  (price / volume / fundamentals / industry / calendar + fetch_metadata.json)
    ↓
src/run_all.py  (或 run_fetch_real_data.py --run_pipeline)
    ↓
PipelineRunner.run_full_pipeline()        [src/pipeline_runner.py]
    ├── Stage 1: Profiler        FinancialTableProfiler.run()           → profiles/
    ├── Stage 2: Planner         WorkflowPlanner.build_plan()           → plans/
    ├── Stage 3: Executor         CodeExecutor.execute()                 → prepared/
    ├── Stage 4: Critic (初始)    ValidityCritic.run_all_checks()       → validation/
    ├── Stage 5: Remediation Agent (有界多轮, 仅当 Stage 4 failed 且 auto_repair)
    │       Observe → Decide → Safety Gate → Act → Re-critic → Reflect
    │       → repaired/ + repair_history.json
    ├── Stage 6: Re-run Critic   对 repaired_panel 重新运行 Critic      → validation_repaired/
    └── Stage 7: Final Report    ReportGenerator.save_all()             → final_report/
    ↓
outputs_real/  (profiles / plans / prepared / validation / repaired /
                validation_repaired / final_report / sessions)
```

**阶段编号说明**（以代码为准）：

- `pipeline_runner.py` 的 `STAGE_DISPLAY` 把流水线记为 **Stage 1–7**：
  `profile` / `planner` / `executor` / `initial_critic` / `repair` / `repaired_critic` / `final_report`。
- 真实数据适配器记为 **Stage 8**（`real_data_adapter.py` / `run_fetch_real_data.py`），
  它在流水线**之前**提供输入，不属于 `PipelineRunner` 的 7 个阶段。
- `run_full_pipeline()` 的 `auto_repair` 逻辑：Stage 4 `overall_status == "failed"` 且
  `auto_repair=True` 时才进入 Stage 5 多轮 Remediation Agent；否则跳过 repair 与
  repaired_critic，写 no-op 产物（`no_repair_needed` 或 `repair_disabled`）。

---

## 4. Agent 内部闭环

Remediation Agent 的多轮自我修正闭环位于 `src/pipeline_runner.py`，策略与安全门逻辑
位于 `src/repair.py`。

```
Observe   读取最新 validation_report（首轮用 initial，后续用上一轮复审）
   ↓
Decide    RepairLoop.decide_round() 用策略注册表选可执行策略，或给出 termination_reason
   ↓
Safety Gate   累计删除行数 / 原始 panel 行数 ≤ max_row_loss_ratio（默认 5%）
              （先预估，apply 后再用实际行数复核；超限则回退到本轮输入）
   ↓
Act      RepairLoop.apply_selected() 在 panel 副本上依次执行 selected 策略
   ↓
Re-critic   PipelineRunner._run_critic() 对修复后 panel 重新运行 ValidityCritic
   ↓
Reflect   记录 panel 指纹 (RepairLoop.panel_fingerprint) 与 failed check 集合
   ↓
Decide whether to continue
   ├── validation_passed        → Stop（Critic 复审通过，无 failed check）
   ├── no_actionable_strategy  → Manual Review（无策略能处理当前 failed check）
   ├── no_progress             → Stop（failed 集合 + 指纹连续两轮不变，禁止无限循环）
   ├── max_rounds_reached      → Stop（达到 max_repair_rounds，默认 3）
   ├── manual_review_required  → Manual Review（策略存在但安全门未通过 / 空面板）
   └── stage_failed            → Manual Review（Remediation Agent 内部异常）
```

**逻辑所在文件、类与方法**：

| 环节 | 文件 | 类 / 方法 |
|---|---|---|
| 多轮调度（外层，含异常捕获） | `src/pipeline_runner.py` | `PipelineRunner._run_remediation_agent()` |
| 多轮主循环（Observe/Decide/Act/Reflect/Stop） | `src/pipeline_runner.py` | `PipelineRunner._remediation_agent_loop()` |
| 每轮记录构造 | `src/pipeline_runner.py` | `PipelineRunner._make_round_record()` |
| 修复后 panel 落盘 | `src/pipeline_runner.py` | `PipelineRunner._save_repaired_panel()` |
| 当前 report 复制为复审报告 | `src/pipeline_runner.py` | `PipelineRunner._copy_current_validation_as_repaired()` |
| 兼容旧产物（repair_plan/log/report） | `src/pipeline_runner.py` | `PipelineRunner._write_remediation_legacy_artifacts()` |
| **repair_history.json 写入位置** | `src/pipeline_runner.py` | `PipelineRunner._write_repair_history()` → `outputs_real/repaired/repair_history.json` |
| Critic 复用（初始 + 每轮复审） | `src/pipeline_runner.py` | `PipelineRunner._run_critic()`（支持 `_critic_factory` 注入，测试用） |
| Observe：提取 failed check | `src/repair.py` | `RepairLoop.failed_checks_of()`（静态） |
| Observe：panel 指纹 | `src/repair.py` | `RepairLoop.panel_fingerprint()`（静态，sha256） |
| Decide：选策略 / 给终止原因 | `src/repair.py` | `RepairLoop.decide_round()` |
| Act：在副本上执行策略 | `src/repair.py` | `RepairLoop.apply_selected()` |
| 策略协议 | `src/repair.py` | `RepairStrategy`（Protocol：`name` / `target_check` / `can_handle` / `estimated_affected_rows` / `risk` / `requires_confirmation` / `apply`） |
| 已注册策略 | `src/repair.py` | `DropRowsWithMissingCorePrice` / `DropExactDuplicateRows` / `TrimIndustryNameWhitespace`；`DEFAULT_STRATEGIES`；`list_strategies()` |
| 单轮入口（向后兼容 `run_repair.py`） | `src/repair.py` | `RepairLoop.build_repair_plan()` / `apply_repairs()` |
| Critic 检查项实现 | `src/critic.py` | `ValidityCritic.run_all_checks()` + 15 个 `_check_*` 方法 |

> `repair_history.json` 即使 blocked / failed / 异常也保存（`_run_remediation_agent`
> 的 except 分支与 no-op 分支都会调用 `_write_repair_history`），保证审计文件始终存在。

---

## 5. 核心模块职责

`src/` 中每个核心模块的职责、核心类/函数、被谁调用（类名/函数名均来自当前代码）：

| 文件 | 核心类/函数 | 职责 | 被谁调用 |
|---|---|---|---|
| `real_data_adapter.py` | `RealDataFetchConfig` / `fetch_real_data()` / `resolve_tradingagents_path()` | 复用参考项目 `TradingAgents-astock-main` 的真实 A 股行情获取能力，输出五张 CSV + `fetch_metadata.json`；严格防未来函数（不回填基本面快照到历史日期） | `run_fetch_real_data.py` |
| `profiler.py` | `FinancialTableProfiler`（`run()` / `save_json()` / `save_markdown()`） | 剖析目录下所有 CSV：schema/dtype/缺失/重复/日期列/证券代码列/数值统计/异常/跨表不一致，输出 `profile.json` + `profile_report.md` | `run_profile.py`、`PipelineRunner._profile_impl()` |
| `planner.py` | `WorkflowPlanner`（`load_profile()` / `build_plan()` / `save_plan()` / `save_markdown_report()`）；`DEFAULT_ANALYSIS_GOAL` | 读 `profile.json` + analysis_goal，动态生成 13 个 workflow steps + 12 个 validation checks + 8 特征 + 1 标签的计划，输出 `workflow_plan.json` + `.md` | `run_planner.py`、`PipelineRunner._planner_impl()` |
| `executor.py` | `CodeExecutor`（`load_workflow_plan()` / `load_raw_tables()` / `execute()` / `save_outputs()` / `save_execution_report()`） | 按 plan 用 pandas 真正执行 11 步数据处理，生成 analysis-ready `prepared_panel.csv` + `data_dictionary.json` + `execution_log.json` + `execution_report.md`；防未来函数（rolling 按 ticker 分组、财务按 `announce_date` as-of 对齐、label 隔离） | `run_executor.py`、`PipelineRunner._executor_impl()` |
| `critic.py` | `ValidityCritic`（`load_inputs()` / `run_all_checks()` / `save_json_report()` / `save_markdown_report()` / `save_approved_feature_columns()`） | 对 prepared/repaired panel 做 15 项有效性审查（未来函数 / label leakage / announce_date 对齐 / 源码静态检查 / 时间切分），输出 `validation_report.json` + `.md` + `approved_feature_columns.json` | `run_critic.py`、`PipelineRunner._run_critic()`（初始 + 每轮复审） |
| `repair.py` | `RepairLoop`（`load_inputs()` / `decide_round()` / `apply_selected()` / `failed_checks_of()` / `panel_fingerprint()` / `build_repair_plan()` / `apply_repairs()` / `save_outputs()` / `save_report()`）；`RepairStrategy` 协议；`DropRowsWithMissingCorePrice` / `DropExactDuplicateRows` / `TrimIndustryNameWhitespace`；`DEFAULT_STRATEGIES` / `list_strategies()` | 策略注册表 + 有界多轮修复决策；单轮接口向后兼容 `run_repair.py`；多轮调度由 `PipelineRunner` 驱动 | `run_repair.py`（单轮）、`PipelineRunner._remediation_agent_loop()`（多轮） |
| `report_generator.py` | `ReportGenerator`（`load_inputs()` / `build_summary()` / `build_artifacts_index()` / `render_full_report()` / `render_one_page()` / `save_all()`） | 只读前五阶段全部产物，汇总成 `final_workflow_summary.json` + `final_workflow_report.md` + `final_workflow_one_page.md` + `pipeline_artifacts_index.json`；动态读取实际结果，不硬编码行数 | `run_report_generator.py`、`PipelineRunner._final_report_impl()` |
| `pipeline_runner.py` | `PipelineRunner`（`run_profile()` / `run_planner()` / `run_executor()` / `run_initial_critic()` / `run_repair()` / `run_remediation_agent()` / `run_noop_repair()` / `run_repaired_critic()` / `run_final_report()` / `run_full_pipeline()` / `get_status()` / `save_session_log()` / `print_dashboard()`；`_run_remediation_agent()` / `_remediation_agent_loop()` / `_write_repair_history()`） | 统一调度器：复用前六阶段内部类按序运行并记录状态；内含 Remediation Agent 多轮闭环；no-op 产物生成；session log；Stage 9 增加薄公开方法 `run_remediation_agent()`；Stage 10 增加薄公开方法 `run_noop_repair()`（no-op 产物单一事实源，供 Agent 工具与 run_full_pipeline 复用） | `run_all.py`、`agent_shell.py`、`run_fetch_real_data.py --run_pipeline`、`agent_tools/pipeline_tools.py` |
| `agent_shell.py` | `AgentShell`（`loop()` / `dispatch()` / `_cmd_*`；`INTENT_ALIASES`） | 交互式 Agent Shell（**固定命令模式，Stage 9 未替换**）：`run all` / `run <stage>` / `status` / `show summary` / `show failures` / `show features` / `open report` / `set goal|input_dir|output_root`；模糊命令 intent mapping；从 `repair_history.json` 恢复历史状态 | `python src/agent_shell.py`（终端用户） |
| `agent_runtime/models.py` | `RiskLevel` / `ToolCall` / `ToolError` / `ToolResult` / `ToolSpec` / `AssistantTurn` / `AgentEvent` / `EventType` / `AgentRunResult` / `StopReason` | Stage 9–10 Agent Runtime 核心数据结构（清晰、最小、可序列化；不含 DataFrame/runner 对象）；Stage 10 增 `StopReason.AWAITING_APPROVAL`、policy/approval/tool_denied 事件、`AgentRunResult.pending_approval` | `agent_runtime/registry.py`、`agent_runtime/runtime.py`、`agent_tools/pipeline_tools.py`、测试 |
| `agent_runtime/context.py` | `AgentContext`（`create()` / `configure_runner()` / `get_runner()` / `ensure_artifact_in_run_root()` / `to_dict()`）；`normalize_run_id()` / `validate_input_dir()`；`RunIdError` / `InputDirError` | Stage 9：每次 Agent run 的上下文 + run_id 隔离（路径穿越防护、run_root 严格位于 output_base/runs/run_id、input_dir 缺失即明确失败绝不生成合成数据） | `agent_tools/pipeline_tools.py`、`agent_runtime/runtime.py`、测试 |
| `agent_runtime/registry.py` | `ToolRegistry`（`register()` / `get()` / `list_specs()` / `schemas_for_model()` / `execute()`）；`validate_arguments()` / `SchemaValidationError`；`build_registry()` | Stage 9：工具注册表 + 基础 JSON Schema 校验；未知工具/参数错误/handler 异常均转结构化 ToolResult，不抛到顶层，不泄漏 traceback | `agent_tools/pipeline_tools.py`、`agent_runtime/runtime.py`、测试 |
| `agent_runtime/model_client.py` | `ModelClient`（Protocol：`complete(messages, tools) -> AssistantTurn`） | Stage 9：抽象模型接口，不依赖任何具体 SDK，不读 API Key；本轮由测试中的 `ScriptedFakeModel` 实现 | `agent_runtime/runtime.py`、测试 |
| `agent_runtime/policy.py` | `PolicyEngine`（`decide(tool_name, risk_level, *, run_id) -> PolicyDecision`）；`PolicyAction`(ALLOW/ASK/DENY) / `PolicyConfig`(`default()` / `with_overrides()`) / `PolicyRule` / `PolicyDecision` / `PendingApproval` / `ApprovalResponse`；`make_fingerprint()` / `new_request_id()` | Stage 10：确定性权限引擎（不调用模型/IO/墙钟）；默认 read/write→ALLOW、guarded→ASK、未知→DENY；优先级 工具级 DENY>ASK>ALLOW > risk 默认 > 默认 DENY | `agent_runtime/runtime.py`、测试 |
| `agent_runtime/runtime.py` | `AgentRuntime`（`run(user_message) -> AgentRunResult` / `resume(ApprovalResponse) -> AgentRunResult`）；`DEFAULT_MAX_TOOL_TURNS` | Stage 9–10：有界 tool-calling 循环（顺序执行、重复检测、requires_user_action 停止、事件记录）；Stage 10 执行前过 PolicyEngine（ALLOW/ASK/DENY），ASK 暂停返回 awaiting_approval+pending_approval，resume 校验 request_id/run_id/fingerprint（防篡改/防跨 run/防重放）后从断点继续；不直接调 PipelineRunner，只通过 ToolRegistry | 测试（由 `ScriptedFakeModel` 驱动） |
| `agent_tools/pipeline_tools.py` | `build_default_registry()` / `build_default_registry_specs()`；10 个工具 handler（`_tool_configure_workflow` 等） | Stage 9–10：把 PipelineRunner 阶段包装成 10 个领域工具（configure/inspect/profile/plan/prepare/validate/remediation/revalidate/report/inspect_failures）；只返回摘要+指标+产物路径+下一步；label 泄漏即安全错误；not_needed 分支走公开 `run_noop_repair()`，不触碰私有方法 | `agent_runtime/runtime.py`（经 ToolRegistry）、测试 |
| `run_fetch_real_data.py` | `parse_args()` / `main()` | 抓取真实数据 CLI；解析参考项目路径；可选 `--run_pipeline` 直接调 `PipelineRunner` | 终端用户 |
| `run_all.py` | `parse_args()` / `main()` / `_compute_exit_code()` / `_validate_remediation_args()` | 一键运行 CLI；三态退出码（0 passed / 1 stage failed / 2 ran but manual_review） | 终端用户（推荐主入口） |
| `run_profile.py` / `run_planner.py` / `run_executor.py` / `run_critic.py` / `run_repair.py` / `run_report_generator.py` | 各自 `parse_args()` / `main()` | 单阶段 CLI，调试用；默认路径已指向 `data/real_market` 与 `outputs_real/...` | 终端用户（调试/单阶段） |

---

## 6. 数据流和产物

### 输入文件（`data/real_market/`，由 `run_fetch_real_data.py` 抓取）

| 文件 | 列 | 来源 |
|---|---|---|
| `price.csv` | `trade_date, ticker, open, high, low, close` | 真实 OHLCV 的 OHLC 部分 |
| `volume.csv` | `date, stock_code, volume, turnover` | volume 来自真实 OHLCV；turnover 无可靠来源时留空，不伪造 |
| `fundamentals.csv` | `report_date, announce_date, ticker, pe, pb, roe` | 当前快照（`announce_date = 抓取日期`）；`--no_snapshot_fundamentals` 时仅表头 |
| `industry.csv` | `ticker, industry_name` | 优先东财 f127 真实行业；失败时 `unknown` |
| `calendar.csv` | `date, is_trading_day` | 覆盖请求区间；有真实行情的日期标记 1，其余 0 |
| `fetch_metadata.json` | 审计字段 | 抓取时间、来源标签、行数、错误/警告、`fundamentals_limitation` |

### 输出产物（`outputs_real/` 下，目录与文件名以 `pipeline_runner.py` 路径常量与 `report_generator.build_artifacts_index()` 为准）

| 目录 | 关键产物 | 产生阶段 | 职责 |
|---|---|---|---|
| `outputs_real/profiles/` | `profile.json` / `profile_report.md` | Stage 1 | 数据画像 |
| `outputs_real/plans/` | `workflow_plan.json` / `workflow_plan_report.md` | Stage 2 | 数据准备计划 |
| `outputs_real/prepared/` | `prepared_panel.csv` / `data_dictionary.json` / `execution_log.json` / `execution_report.md` | Stage 3 | analysis-ready 宽表 + 字段口径 + 执行日志 |
| `outputs_real/validation/` | `validation_report.json` / `validation_report.md` / `approved_feature_columns.json` | Stage 4 | 初始 Critic 报告 + approved features |
| `outputs_real/repaired/` | `repair_plan.json` / `repaired_panel.csv` / `repair_log.json` / `repair_report.md` / **`repair_history.json`** | Stage 5 | 修复方案 + 修复后 panel + 多轮审计记录 |
| `outputs_real/validation_repaired/` | `validation_report.json` / `validation_report.md` / `approved_feature_columns.json` | Stage 6 | 复审 Critic 报告 + 复审 approved features |
| `outputs_real/final_report/` | `final_workflow_summary.json` / `final_workflow_report.md` / `final_workflow_one_page.md` / `pipeline_artifacts_index.json` | Stage 7 | 六阶段汇总 + 总报告 + 一页摘要 + 产物索引 |
| `outputs_real/sessions/` | `latest_session.json` / `session_YYYYMMDD_HHMMSS.json` | Stage 7 | 每次运行的完整状态快照 |
| `outputs_real/runs/<run_id>/` | 同上 7 个子目录（profiles/plans/prepared/validation/repaired/validation_repaired/final_report/sessions） | Stage 9 | Agent Runtime 按 run_id 隔离的运行目录；每个 run 一个独立 PipelineRunner，互不读取/恢复 |

### 关键安全约束（以代码为准）

- **`label_next_5d` 不会进入 approved feature columns**：`critic._derive_approved_feature_columns()`
  取 `FEATURE_WHITELIST ∩ (data_dictionary role=feature)`，而 `label_next_5d` 的 role 是 `label`，
  结构性排除；`_check_label_not_in_approved_features()` 与 `_check_no_future_named_columns_in_features()`
  强制校验。
- **Agent 不直接伪造或回填金融数据**：`repair.py` 策略只做保守删除 / strip 空格，
  `DropRowsWithMissingCorePrice` 删行不插值；`TrimIndustryNameWhitespace` 不把缺失值伪造为
  `"None"/"nan"/"<NA>"`；`real_data_adapter._build_fundamentals()` 不把当前快照回填到历史日期。
- **无法安全修复时转 manual review**：未知 failed check 走 `no_actionable_strategy`；
  累计删行超 `max_row_loss_ratio` 走 `manual_review_required`；超限修复结果不保存，回退到本轮输入。
- **Stage 9 Agent Runtime 安全边界**：`AgentContext` 校验 input_dir（缺失即明确失败，绝不生成合成数据）；
  run_id 路径穿越防护；run_root 严格位于 `output_base/runs/run_id`；工具只写当前 run_root；
  `label_in_approved_features=True` 时工具返回安全错误并 `requires_user_action=True`；
  `manual_review_required` 时 `requires_user_action=True`，Runtime 停止；
  Runtime 有界（`max_tool_turns` 默认 12 + 重复检测）；不接入真实 LLM，不实现任意 shell/Python/MCP。
- **Stage 10 权限审批安全边界**：`PolicyEngine` 是 Runtime 执行前的**唯一**授权入口，
  完全确定性（不调用模型/IO/墙钟）；默认 guarded→ASK；模型与用户文本不能自行修改策略或声明
  "已授权"；`resume` 校验 request_id/run_id/fingerprint（防篡改、防跨 run、防重放），审批只能
  消费一次；审批只决定"是否执行"，执行仍走 PipelineRunner → Remediation Agent，**不绕过**
  删行阈值、轮数限制、标签泄漏保护等内部安全门；`awaiting_approval`（执行前/未执行/有 pending）
  与 `requires_user_action`（执行后/已执行/无 pending）严格区分。

---

## 7. 测试结构

测试代码位于 `tests/`，共 **102 项** unittest，分 13 个 `TestCase`：

### 7.1 Remediation Agent 测试（`test_remediation_agent.py`，23 项）

| 测试类 | 测试数 | 主要覆盖内容 | 数据来源 |
|---|---|---|---|
| `TestStrategyRegistry` | 6 | 策略注册表：`DEFAULT_STRATEGIES` 存在性、`DropRowsWithMissingCorePrice` / `DropExactDuplicateRows` / `TrimIndustryNameWhitespace` 的 `can_handle` / `apply` 行为；`TrimIndustryNameWhitespace` 不把缺失值伪造为字符串 | 内存 DataFrame（单元测试，非真实行情） |
| `TestRemediationAgent` | 8 | 0 轮收敛 / 一轮收敛 / `no_actionable_strategy` / 严格 `no_progress` / 严格 `max_rounds_reached` / 5% 安全门转人工 / label 不进 features / blocked 时仍写 `repair_history.json` | 真实 fixture 临时副本（注入故障） |
| `TestExitCodes` | 6 | `run_all.py` 三态退出码：0（passed / 一轮收敛）/ 1（参数越界）/ 2（manual_review / `--no_repair` + initial failed） | 真实 fixture 临时副本（subprocess 调 `run_all.py`） |
| `TestShellStateRestore` | 2 | `agent_shell` 从 `repair_history.json` 恢复历史状态；`--demo_commands` 显示恢复后的状态 | 真实 fixture 临时副本（subprocess） |
| `TestRepairHistorySchema` | 1 | `repair_history.json` 顶层与每轮字段完整性 | 真实 fixture 临时副本 |

### 7.2 Stage 9 Agent Runtime 测试（3 个文件，45 项）

| 测试文件 | 测试类 | 测试数 | 主要覆盖内容 | 数据来源 |
|---|---|---|---|---|
| `test_tool_registry.py` | `TestRegistryCore` / `TestSchemaValidation` | 11 | A1 注册查找 / A2 重复拒绝 / A3 未知工具结构化错误 / A4 缺 required / A5 类型错误 / A6 handler 异常转 ToolResult / A7 schema 导出；boolean 严格非 int、enum、array items | 内存（无 fixture） |
| `test_agent_runtime.py` | `TestRunId` / `TestAgentContext` / `TestAgentRuntime` | 22 | B1–B6 run_id 隔离 / D1–D10 Runtime 行为（执行/回填/completed/max_tool_turns/重复/未知工具/失败反馈/requires_user_action/事件/不直接依赖 runner）+ model_protocol_error | 真实 fixture 临时副本 + `ScriptedFakeModel` |
| `test_pipeline_tools.py` | `TestPipelineTools` / `TestPipelineToolsInputValidation` | 12 | C1–C10 输入校验/不生成合成数据/configure run_root/profile/stage 失败/status 只读当前 run/validation 结构化/remediation 安全状态传递/label 不进 features/not_needed | 真实 fixture 临时副本 |

### 7.3 Stage 10 权限审批测试（2 个文件，34 项）

| 测试文件 | 测试类 | 测试数 | 主要覆盖内容 | 数据来源 |
|---|---|---|---|---|
| `test_policy_engine.py` | `TestPolicyDefaults` / `TestPolicyRulesAndPriority` / `TestDeterminism` / `TestSerialization` | 16 | read/write/guarded/unknown 默认决策；工具级 allow/ask/deny 与优先级（DENY>ASK>ALLOW）；确定性（相同输入多次一致、decide 不改 config）；所有结构可 JSON 序列化；fingerprint 稳定且 run 作用域；request_id 唯一 | 内存（无 fixture） |
| `test_runtime_approval.py` | `TestHandlerNotExecutedOnAskDeny` / `TestApproveExecutesOnce` / `TestRejectFeedback` / `TestResumeRejection` / `TestResumeDoesNotResetCounters` / `TestMultiToolCallResume` / `TestGuardedRemediationDefaultAsk` / `TestApprovalDoesNotBypassSafetyGate` / `TestAwaitingVsRequiresUserAction` / `TestNoopRepairPublicAPIOnly` / `TestEndToEndRepairPath` | 18 | ASK/DENY 时 handler 未执行；批准后只执行一次；拒绝后不执行并反馈；错误 request_id/参数篡改/跨 run/重复审批被拒绝；resume 后轮数与重复检测不重置；多 ToolCall 暂停后正确继续（含 DENY 不中断后续）；guarded remediation 默认 ASK；批准后仍受安全门约束；awaiting_approval vs requires_user_action 不混淆；no-op repair 只用公开 API（源码断言）；实际修复路径端到端通过 | 真实 fixture 临时副本 + `ScriptedFakeModel` |

**Stage 9 测试关键设计**：

- `ScriptedFakeModel`：按顺序返回预设 `AssistantTurn`，记录收到的 messages/tools，预设耗尽即明确失败；不访问网络，不依赖 LLM SDK。
- 真实 fixture 复制到临时目录后使用（`_copy_fixture`），不修改 `test_data/real_market_sample`。
- 每个 run_id 用独立 input 子目录，避免同一测试中多次 `copytree` 冲突。
- 不写入被 Git 跟踪的数据目录；不访问网络；不要求真实 API Key；不依赖 `outputs_real` 历史产物。

**测试关键设计**：

- **单元测试**：`TestStrategyRegistry` 用 `_make_panel()` 构造的内存 DataFrame 验证策略算法边界
  （`can_handle` / `apply`），**不**代表真实行情数据，也不作为正式运行示例。
- **集成测试用真实 fixture**：`TestRemediationAgent` / `TestExitCodes` / `TestShellStateRestore` /
  `TestRepairHistorySchema` 的 `setUp` 调 `_copy_fixture(tmp)` 把
  `test_data/real_market_sample/` 复制到临时目录，所有故障注入只改**临时副本**。
- **故障注入到临时副本**：缺失行（`df.loc[idx, "close"] = None`）、重复行、未知 failed check、
  `no_progress` / `max_rounds` 场景都在临时副本上人为制造，**不修改被提交的真实 fixture**。
- **FakeCritic**：`FakeCritic` / `FakeCriticReport` 可注入 `PipelineRunner._critic_factory`，
  控制每轮复审结果（固定 failed check 集合 / 递增 `injected_check_n`），用于严格验证
  `no_progress` 与 `max_rounds_reached` 路径；`injected_check_n` 是人为注入的测试故障名，
  不是真实数据抓取结果。
- **动态断言**：删除对 300/298 等合成数据规模的硬编码，按 fixture 实际行数 `n = len(df)` 动态断言。
- **安全门与收敛路径隔离**：真实 fixture 仅 7 行，注入 2 行缺失即 28% > 5% 安全门，
  故收敛路径测试用 `max_row_loss_ratio=0.5` 隔离；5% 安全门本身由
  `test_row_loss_over_5_percent_manual_review`（注入 1 行，1/7≈14% > 5%）独立验证。

---

## 8. 常用命令

以下 PowerShell 命令可直接复制，参数与当前 CLI 一致（从项目根目录运行）。

**1. 下载真实市场数据**（需网络；参考项目路径按实际填写）

```powershell
python -B src/run_fetch_real_data.py --tickers 600519 `
  --start_date 2024-01-01 --end_date 2024-01-10 `
  --output_dir data/real_market `
  --tradingagents_path D:\dwzq\TradingAgents-astock-main `
  --no_snapshot_fundamentals
```

**2. 运行完整 Pipeline**（推荐主入口）

```powershell
python -B src/run_all.py --input_dir data/real_market --output_root outputs_real
```

可选参数：`--no_repair` / `--max_repair_rounds 3` / `--max_row_loss_ratio 0.05` /
`--skip_report` / `--clean_outputs` / `--verbose` / `--analysis_goal "..."`。

**3. 启动 Agent Shell**

```powershell
python -B src/agent_shell.py --input_dir data/real_market --output_root outputs_real
```

非交互测试模式：`python -B src/agent_shell.py --demo_commands`。

**4. 运行全部测试**

```powershell
python -B -m unittest discover -s tests -v
```

> Stage 9 新增 `test_tool_registry.py` / `test_agent_runtime.py` / `test_pipeline_tools.py`，
> Stage 10 新增 `test_policy_engine.py` / `test_runtime_approval.py`，
> 共 102 项测试（原 23 + Stage 9 的 45 + Stage 10 的 34）。测试用 `ScriptedFakeModel`
> 驱动 Agent Runtime，不接入真实 LLM。

**5. 使用真实 fixture 做最小验证**（无需网络，用提交的小型真实 fixture 跑流水线）

```powershell
python -B src/run_all.py --input_dir test_data/real_market_sample --output_root outputs_real
```

> 也可用一键命令 `python -B src/run_fetch_real_data.py ... --run_pipeline --output_root outputs_real`
> 一次完成抓取 + 流水线。

> **Stage 9 Agent Runtime** 目前没有面向终端用户的 CLI 入口（`chat_agent.py` 属后续阶段）；
> Runtime 通过 Python API（`AgentRuntime(model, registry, context).run(msg)`）使用，
> 由测试中的 `ScriptedFakeModel` 驱动。

---

## 9. 阅读代码的推荐顺序

给导师或新开发者的推荐阅读顺序（由浅入深，先文档后代码）：

1. [README.md](README.md) — 项目总览、真实数据策略、最小运行命令、安全边界。
2. [DIRECTORY_GUIDE.md](DIRECTORY_GUIDE.md) — 目录职责、数据与 Git 跟踪规则。
3. 本文件 [CODE_STRUCTURE.md](CODE_STRUCTURE.md) — 代码结构、模块职责、执行调用链。
4. `src/run_all.py` — 推荐主入口，看 `main()` 与 `_compute_exit_code()` 理解一键运行与三态退出码。
5. `src/pipeline_runner.py` — 统一调度器，看 `run_full_pipeline()` 理解 7 阶段顺序与 `auto_repair` 逻辑；看 `_remediation_agent_loop()` 理解 Remediation Agent 多轮闭环。
6. `src/critic.py` — 看 `run_all_checks()` 与 15 个 `_check_*` 方法理解有效性审查（未来函数 / label leakage / announce_date 对齐 / 源码静态检查）。
7. `src/repair.py` — 看 `RepairStrategy` 协议、`DEFAULT_STRATEGIES`、`decide_round()` / `apply_selected()` 理解策略注册表与安全门。
8. `src/executor.py` — 看 `execute()` 的 11 步与 `_align_fundamentals()` / `_compute_price_volume_features()` / `_create_future_return_label()` 理解防未来函数实现。
9. `src/real_data_adapter.py` — 看 `fetch_real_data()` 与 `_build_fundamentals()` 理解真实数据接入与基本面时间点约束。
10. `src/profiler.py` / `src/planner.py` / `src/report_generator.py` — 前三阶段与收口报告的实现细节。
11. `tests/test_remediation_agent.py` — 看 `FakeCritic` / `_copy_fixture` 与 5 个 `TestCase` 理解测试如何用真实 fixture 验证 Agent 行为。
12. [docs/](docs/) — 分阶段设计文档（`stage2`–`stage10` + `project_scope.md` + `project_overview_zh.md`）。
13. [docs/stage9_agent_runtime_mvp.md](docs/stage9_agent_runtime_mvp.md) — Stage 9 Agent Runtime MVP 设计（ModelClient / ToolSpec / ToolRegistry / AgentContext / Runtime 循环 / 停止条件 / Fake Model 测试 / 安全边界 / 下一阶段）。
14. [docs/stage10_policy_and_approval.md](docs/stage10_policy_and_approval.md) — Stage 10 PolicyEngine + 审批恢复设计（默认策略与优先级 / pause/resume 防篡改防重放 / 多 ToolCall 恢复 / awaiting vs requires_user_action / 安全门不被绕过）。
15. `src/agent_runtime/models.py` → `context.py` → `registry.py` → `model_client.py` → `policy.py` → `runtime.py` — Agent Runtime 骨架的阅读顺序。
16. `src/agent_tools/pipeline_tools.py` — 10 个领域工具如何把 PipelineRunner 阶段包装成 ToolSpec。
17. `tests/test_agent_runtime.py` — 看 `ScriptedFakeModel` 如何驱动 Runtime 验证 tool-calling 循环与停止条件。
18. `tests/test_policy_engine.py` / `tests/test_runtime_approval.py` — Stage 10 权限决策与审批暂停/恢复的测试。
