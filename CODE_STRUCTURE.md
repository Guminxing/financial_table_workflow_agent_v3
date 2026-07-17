# Code Structure

> 本文件面向希望继续阅读代码的人，提供目录结构、模块职责、核心数据结构、调用链与扩展位置。
> 项目总览与快速开始见 [README.md](README.md)；LLM Agent 见 [docs/LLM_AGENT.md](docs/LLM_AGENT.md)；
> 确定性 Pipeline 见 [docs/PIPELINE.md](docs/PIPELINE.md)。
>
> - `src/` 是**运行代码**。
> - `tests/` 是**测试代码**。
> - `test_data/real_market_sample/` 是**小型真实市场测试 fixture**（提交 Git，仅用于测试与最小演示）。
> - `data/real_market/` 与 `outputs_real/` 是**运行时目录**（不提交 Git，每次运行重新生成）。

---

## 1. 总体目录树

根据 `git ls-files` 实际跟踪的文件生成，展开到文件级别。忽略 `__pycache__/`、`*.pyc`、`.git/`、临时输出、虚拟环境、测试运行缓存。

```
financial_table_workflow_agent_v3/
├── src/                                # 运行代码（全部提交 Git）
│   ├── __init__.py                     # 包标记
│   ├── profiler.py                     # Stage 1: FinancialTableProfiler 数据剖析
│   ├── run_profile.py                  # Stage 1 CLI 入口
│   ├── planner.py                      # Stage 2: WorkflowPlanner 工作流规划
│   ├── run_planner.py                  # Stage 2 CLI 入口
│   ├── executor.py                     # Stage 3: CodeExecutor 生成 analysis-ready 宽表
│   ├── run_executor.py                 # Stage 3 CLI 入口
│   ├── critic.py                       # Stage 4/6: ValidityCritic 有效性审查
│   ├── run_critic.py                   # Stage 4/6 CLI 入口
│   ├── repair.py                       # Stage 5: RepairLoop + 策略注册表
│   ├── run_repair.py                   # Stage 5 CLI 入口（单轮，向后兼容）
│   ├── report_generator.py             # Stage 7: ReportGenerator 最终报告
│   ├── run_report_generator.py         # Stage 7 CLI 入口
│   ├── pipeline_runner.py              # PipelineRunner 统一调度器 + Remediation Agent 多轮闭环
│   ├── run_all.py                      # 一键运行入口（推荐主入口）
│   ├── agent_shell.py                  # 交互式 Agent Shell（固定命令模式，未替换）
│   ├── chat_agent.py                   # 自然语言 Agent CLI（接真实 OpenAI-compatible LLM）
│   ├── real_data_adapter.py            # 真实 A 股数据适配器
│   ├── run_fetch_real_data.py          # 真实数据抓取 CLI（可 --run_pipeline）
│   ├── data_sources/                    # 项目内置数据源（不依赖其他 Agent）
│   │   ├── __init__.py
│   │   └── astock.py                    # 东方财富 OHLCV + 新浪 fallback + 腾讯快照/行业
│   ├── agent_runtime/                  # Agent Runtime（模型驱动 tool-calling + 确定性权限审批 + 真实 LLM 适配器）
│   │   ├── __init__.py
│   │   ├── models.py                   # 核心数据结构（ToolCall/ToolResult/ToolSpec/AgentEvent/AgentRunResult/RiskLevel/StopReason/EventType）
│   │   ├── context.py                  # AgentContext + run_id 隔离（路径穿越防护；Stage 12 支持无 input_dir 启动 + set_input_dir）
│   │   ├── registry.py                 # ToolRegistry + 基础 JSON Schema 校验
│   │   ├── model_client.py             # ModelClient Protocol（不依赖具体 SDK）
│   │   ├── openai_compatible_client.py # OpenAICompatibleModelClient + 纯转换函数
│   │   ├── policy.py                   # PolicyEngine + PolicyAction(allow/ask/deny) + PendingApproval/ApprovalResponse
│   │   └── runtime.py                  # 有界 tool-calling 循环 + 进程内审批恢复（run/resume + event_callback）
│   └── agent_tools/                    # 领域工具（把 PipelineRunner 阶段包装成工具）
│       ├── __init__.py
│       └── pipeline_tools.py           # 11 个 pipeline 工具（含 fetch_real_market_data）+ build_default_registry()
├── tests/                              # 测试代码（全部提交 Git）
│   ├── __init__.py                     # 包标记
│   ├── test_remediation_agent.py       # Remediation Agent 单元/集成测试
│   ├── test_tool_registry.py           # ToolRegistry + schema 校验测试
│   ├── test_agent_runtime.py           # AgentRuntime + AgentContext/run_id 测试（含 ScriptedFakeModel）
│   ├── test_pipeline_tools.py          # Pipeline 领域工具测试
│   ├── test_policy_engine.py           # PolicyEngine + 策略优先级测试
│   ├── test_runtime_approval.py        # 审批暂停/恢复 + 防篡改防重放 + 多 ToolCall 恢复测试
│   ├── test_openai_compatible_client.py # OpenAICompatibleModelClient 适配器测试（全 mock 不访问网络）
│   ├── test_chat_agent.py              # 自然语言 CLI 测试（模式 A/B + 按工具名审批 + Fake Model 全链）
│   ├── test_fetch_tool.py              # Stage 12：fetch_real_market_data 工具测试（mock adapter，不访问网络）
│   ├── test_astock_data_source.py       # 内置 A 股数据源 HTTP 解析/缓存/独立性测试
│   └── test_chinese_report.py          # Stage 12：中文最终报告测试（标题/数值/summary 兼容/数据来源）
├── test_data/                          # 测试数据
│   └── real_market_sample/             # 小型真实 A 股 fixture（提交 Git）
│       ├── README.md                    # fixture 来源、抓取命令、行数、免责声明
│       ├── fetch_metadata.json         # 可审计的抓取元数据
│       ├── price.csv                    # 真实 OHLC（7 行）
│       ├── volume.csv                   # 真实成交量（7 行；turnover 留空）
│       ├── fundamentals.csv             # 仅表头（--no_snapshot_fundamentals）
│       ├── industry.csv                 # 真实行业（1 行，白酒Ⅱ）
│       └── calendar.csv                 # 交易日历（10 行）
├── data/
│   └── real_market/                    # 用户运行时下载的真实数据（不提交 Git，仅 .gitkeep）
│       └── .gitkeep
├── outputs_real/                       # 正式运行产物（不提交 Git，仅 .gitkeep）
│   ├── .gitkeep
│   └── runs/                           # Agent Runtime 按 run_id 隔离的运行目录
│       └── <run_id>/                   # profiles/plans/prepared/validation/repaired/...
├── docs/
│   ├── LLM_AGENT.md                    # LLM Agent 主文档（架构/工具/审批/运行/排查/验收）
│   ├── PIPELINE.md                     # 确定性七阶段 Pipeline
│   ├── test_records/                   # 真实环境端到端测试记录
│   │   ├── README.md                    # 测试记录索引
│   │   ├── 2026-07-17_mode_b_real_market_e2e.md
│   │   ├── 2026-07-17_mode_b_single_ticker_e2e.md
│   │   ├── 2026-07-17_mode_b_multi_ticker_cross_section_e2e.md
│   │   ├── 2026-07-17_mode_b_long_range_e2e.md
│   │   ├── 2026-07-17_mode_b_fundamentals_snapshot_boundary_e2e.md
│   │   ├── 2026-07-17_mode_b_partial_ticker_failure_tolerance_e2e.md
│   │   └── 2026-07-17_mode_b_no_usable_data_safe_stop_e2e.md
│   └── archive/                        # 分阶段开发过程记录（Stage 2–12，已被主文档取代）
├── prompts/
│   ├── workflow_planner_prompt.md      # LLM Planner Prompt 模板（供后续接入）
│   └── financial_agent_system.md       # 自然语言 Agent system prompt
├── README.md                           # 项目总览 + 快速开始
├── CODE_STRUCTURE.md                   # 本文件
├── requirements.txt                    # 依赖：pandas>=1.5.0, requests>=2.32.0
├── .env.example                        # 环境变量占位符（不提交真实 .env）
└── .gitignore                          # 忽略运行时数据/产物/缓存/凭据
```

> `src/` 共 31 个 Python 文件：1 个包标记 + 9 个核心模块 + 9 个 CLI 入口（含 `chat_agent.py`）+ `agent_runtime/`（8 个文件）、`agent_tools/`（2 个文件）与 `data_sources/`（2 个文件）。
> `tests/` 共 12 个 Python 文件，201 项 unittest。

---

## 2. 核心模块职责

### 2.1 `src/agent_runtime/` — Agent Runtime

模型驱动的 tool-calling Agent Runtime。LLM 负责理解意图与选工具，确定性 Pipeline 负责金融计算。

| 文件 | 核心类/函数 | 职责 |
|---|---|---|
| `models.py` | `RiskLevel` / `ToolCall` / `ToolError` / `ToolResult` / `ToolSpec` / `AssistantTurn` / `AgentEvent` / `EventType` / `AgentRunResult` / `StopReason` | 核心数据结构（最小、清晰、可序列化；不含 DataFrame/runner 对象） |
| `context.py` | `AgentContext`（`create` / `create_without_input_dir` / `configure_runner` / `get_runner` / `set_input_dir` / `has_input_dir` / `ensure_artifact_in_run_root` / `ensure_path_in_run_root` / `to_dict`）；`normalize_run_id` / `validate_input_dir`；`RunIdError` / `InputDirError` | 每次 run 的上下文 + run_id 隔离（路径穿越防护、run_root 严格位于 `output_base/runs/run_id`、input_dir 缺失即明确失败绝不生成合成数据）；Stage 12 支持无 input_dir 启动 + `set_input_dir`（fetch 后设置，校验五张 CSV + 路径边界） |
| `registry.py` | `ToolRegistry`（`register` / `get` / `list_specs` / `schemas_for_model` / `execute`）；`validate_arguments` / `SchemaValidationError`；`build_registry` | 工具注册表 + 基础 JSON Schema 校验；未知工具/参数错误/handler 异常均转结构化 ToolResult，不抛顶层、不泄漏 traceback |
| `model_client.py` | `ModelClient`（Protocol：`complete(messages, tools) -> AssistantTurn`） | 抽象模型接口，不依赖任何具体 SDK，不读 API Key |
| `openai_compatible_client.py` | `OpenAICompatibleModelClient`（实现 `ModelClient`）；`tool_spec_to_provider` / `messages_to_provider` / `response_to_turn`；`ModelError`/`ModelConfigError`/`ModelRequestError`/`ModelResponseError` | 用已有 `requests` 调标准 OpenAI-compatible Chat Completions tool calling；API Key 只从 `FTA_LLM_*` 环境变量读，只放进 HTTP `Authorization` 头，绝不写入日志/事件/错误信息（`_scrub` 兜底）；支持多 tool_calls；可注入 `requests.Session`，测试全 mock 不访问网络 |
| `policy.py` | `PolicyEngine`（`decide`）；`PolicyAction`(ALLOW/ASK/DENY) / `PolicyConfig`(`default` / `with_overrides`) / `PolicyRule` / `PolicyDecision` / `PendingApproval` / `ApprovalResponse`；`make_fingerprint` / `new_request_id` | 确定性权限引擎（不调用模型/IO/墙钟）；默认 read/workspace_write→ALLOW、guarded→ASK、未知→DENY；优先级 工具级 DENY>ASK>ALLOW > risk 默认 > 默认 DENY |
| `runtime.py` | `AgentRuntime`（`run` / `resume`）；`DEFAULT_MAX_TOOL_TURNS`；可选 `event_callback` | 有界 tool-calling 循环（顺序执行、重复检测、requires_user_action 停止、事件记录）；执行前过 PolicyEngine，ASK 暂停返回 awaiting_approval+pending_approval，resume 校验 request_id/run_id/fingerprint 后从断点继续；不直接调 PipelineRunner，只通过 ToolRegistry |

### 2.2 `src/agent_tools/` — 领域工具

| 文件 | 核心类/函数 | 职责 |
|---|---|---|
| `pipeline_tools.py` | `build_default_registry()` / `build_default_registry_specs()`；11 个工具 handler（`_tool_fetch_real_market_data` / `_tool_configure_workflow` 等）；`_validate_fetch_tickers` / `_validate_fetch_date` / `MAX_FETCH_TICKERS` / `_require_runner` | 把 PipelineRunner 阶段包装成 11 个领域工具（含 `fetch_real_market_data`，guarded）；只返回摘要+指标+产物路径+下一步；label 泄漏即安全错误；not_needed 分支走公开 `run_noop_repair()`，不触碰私有方法；Stage 12：无 input_dir 时 profile/plan/prepare 返回 `PRECONDITION_NOT_MET` |

### 2.3 `src/chat_agent.py` — 自然语言 CLI

| 函数 | 职责 |
|---|---|
| `parse_args(argv)` | 解析 CLI 参数（见 README §6.4）；Stage 12：`--input_dir` 可选（模式 B）、`--auto_approve_data_fetch` |
| `run_chat(args, *, model_client=None, input_fn=input, output_fn=print) -> int` | 主逻辑，可注入 Fake Model 与 IO 函数测试；启动流程 AgentContext（模式 A 校验 input_dir / 模式 B 无 input_dir）→ToolRegistry→PolicyEngine→ModelClient→AgentRuntime→执行→事件回调打印进度→处理 awaiting_approval→输出最终回答+run_root+报告路径；退出码 0 完成/1 配置或运行错误/2 需人工介入 |
| `_build_model_client(args, workspace_root)` | 构造真实 `OpenAICompatibleModelClient`；失败抛 `ModelConfigError` |
| `_resolve_policy()` | 始终返回默认 `PolicyEngine()`（guarded→ASK）；`--auto_approve_*` 在 CLI 层按工具名自动回复 approved，不改策略 |
| `_should_auto_approve(tool_name, *, auto_approve_data_fetch, auto_approve_remediation)` | Stage 12：按 `pending.tool_name` 决定是否自动批准（fetch→`--auto_approve_data_fetch`，remediation→`--auto_approve_remediation`，互不越权） |
| `_handle_approval(runtime, result, *, auto_approve_data_fetch, auto_approve_remediation, input_fn, output_fn)` | 交互式 `Approve? [y/N]` 或按工具名 auto-approve，调 `runtime.resume(ApprovalResponse)` |
| `_build_context(args, workspace_root, run_id)` | Stage 12：按是否传 `--input_dir` 构造 AgentContext（模式 A `create` / 模式 B `create_without_input_dir`） |
| `_make_event_printer(output_fn)` | 构造 `AgentEvent` 回调，打印 `[tool]` / `[approval]` / `[stop]` 进度行；不打印完整 messages/隐藏推理/API Key |
| `_find_report_path(ctx)` | 从当前 run 的 runner 读取 `full_report_md` 路径 |
| `main()` | `return run_chat(parse_args())` |

### 2.4 `src/pipeline_runner.py` — 统一调度器

`PipelineRunner` 复用前六阶段内部类按序运行并记录状态，内含 Remediation Agent 多轮闭环。公开方法（来自源码）：

`run_profile` / `run_planner` / `run_executor` / `run_initial_critic` / `run_repair` / `run_remediation_agent` / `run_noop_repair` / `run_repaired_critic` / `run_final_report` / `run_full_pipeline` / `get_status` / `save_session_log` / `print_dashboard`。

`STAGE_ORDER = [profile, planner, executor, initial_critic, repair, repaired_critic, final_report]`。被 `run_all.py` / `agent_shell.py` / `run_fetch_real_data.py --run_pipeline` / `agent_tools/pipeline_tools.py` 复用。

### 2.5 原 Pipeline 阶段模块

| 文件 | 核心类 | 职责 |
|---|---|---|
| `profiler.py` | `FinancialTableProfiler` | 剖析所有 CSV：schema/dtype/缺失/重复/日期列/证券代码列/数值统计/跨表不一致 |
| `planner.py` | `WorkflowPlanner`；`DEFAULT_ANALYSIS_GOAL` | 读 profile + analysis_goal，动态生成 workflow steps + validation checks + 特征 + 标签 |
| `executor.py` | `CodeExecutor` | 按 plan 用 pandas 执行数据处理，生成 analysis-ready 宽表；防未来函数（rolling 按 ticker 分组、财务按 `announce_date` as-of 对齐、label 隔离） |
| `critic.py` | `ValidityCritic` | 对 prepared/repaired panel 做有效性审查（未来函数 / label leakage / announce_date 对齐 / 源码静态检查 / 时间切分），生成 `approved_feature_columns.json` |
| `repair.py` | `RepairLoop`；`RepairStrategy` 协议；`DropRowsWithMissingCorePrice` / `DropExactDuplicateRows` / `TrimIndustryNameWhitespace`；`DEFAULT_STRATEGIES` / `list_strategies` | 策略注册表 + 有界多轮修复决策；单轮接口向后兼容 `run_repair.py`；多轮调度由 `PipelineRunner` 驱动 |
| `report_generator.py` | `ReportGenerator` | 只读前五阶段全部产物，汇总成最终报告；Stage 12：中文正文 + "数据来源与时间边界"章节（读 `fetch_metadata.json`，无则显示"用户提供的已有 CSV"）；动态读取实际结果，不硬编码行数；`passed_with_warnings` 显示为"passed_with_warnings（通过但有警告）"不覆盖原值 |
| `data_sources/astock.py` | `AStockDataSource` / `normalize_ticker` | 项目内置 A 股数据源：东方财富日线为主、Sina HTTP fallback、腾讯当前快照、东方财富行业；HTTP session 可注入，缓存由调用方指定，不依赖其他 Agent 项目 |
| `real_data_adapter.py` | `RealDataFetchConfig` / `fetch_real_data` | 把内置数据源转换为五张 CSV + `fetch_metadata.json`；缓存默认位于 `output_dir/cache`；严格防未来函数（不回填当前基本面快照） |

### 2.6 其他目录

- `prompts/`：`financial_agent_system.md`（自然语言 Agent system prompt，Stage 12 双模式）、`workflow_planner_prompt.md`（LLM Planner 模板，供后续接入）。
- `tests/`：见 [§6](#6-测试结构)。
- `docs/`：`LLM_AGENT.md`（LLM Agent 主文档）、`PIPELINE.md`（确定性七阶段）、
  `test_records/`（真实环境端到端验收记录）、`archive/`（Stage 2–12 开发过程记录）。
- `test_data/real_market_sample/`：小型真实 A 股 fixture（ticker 600519，2024-01-01..2024-01-10），仅用于测试与最小演示，不是代表性市场样本，不是投资数据。

---

## 3. Agent Runtime

核心数据结构与类之间的关系（类名、字段名、文件路径均来自源码）：

```
ModelClient (Protocol, model_client.py)
  └─ OpenAICompatibleModelClient (openai_compatible_client.py)
       complete(messages, tools) -> AssistantTurn

AgentContext (context.py)
  - workspace_root / input_dir (可 None) / output_base / run_id / run_root
  - analysis_goal / auto_repair / max_repair_rounds / max_row_loss_ratio
  - runner: PipelineRunner | None
  - create() / create_without_input_dir() / configure_runner() / get_runner()
  - set_input_dir() / has_input_dir() / ensure_artifact_in_run_root() / ensure_path_in_run_root()

ToolRegistry (registry.py)
  - register(ToolSpec) / get(name) / list_specs() / schemas_for_model() / execute(ToolCall, ctx)

ToolSpec (models.py)
  - name / description / input_schema / risk_level / handler

ToolCall (models.py)        call_id / name / arguments
ToolResult (models.py)      ok / status / summary / metrics / artifacts / next_actions / error / requires_user_action
AssistantTurn (models.py)   final_text | tool_calls（XOR，is_valid() 校验）

AgentEvent (models.py)      event_type / timestamp / payload
EventType (models.py)       user_message / assistant_turn / tool_call / tool_result /
                            runtime_stop / policy_decision / approval_requested /
                            approval_resolved / tool_denied
AgentRunResult (models.py)  final_text / stop_reason / events / tool_turns / pending_approval
StopReason (models.py)      completed / max_tool_turns / repeated_tool_call /
                            requires_user_action / model_protocol_error /
                            runtime_error / awaiting_approval
RiskLevel (models.py)       read / workspace_write / guarded

PolicyEngine (policy.py)
  - decide(tool_name, risk_level, *, run_id) -> PolicyDecision
PolicyAction (policy.py)    ALLOW / ASK / DENY
PolicyConfig (policy.py)   risk_defaults / rules / default_action；default() / with_overrides()
PolicyRule (policy.py)      rule_id / action / tool_names / risk_levels / priority
PolicyDecision (policy.py)  action / reason / rule_id / tool_name / risk_level
PendingApproval (policy.py) request_id / call_id / tool_name / arguments / fingerprint / run_id
ApprovalResponse (policy.py) request_id / approved / note

AgentRuntime (runtime.py)
  - run(user_message) -> AgentRunResult
  - resume(ApprovalResponse) -> AgentRunResult
  - max_tool_turns（默认 12）+ 重复检测
  - event_callback（可选，CLI 实时打印进度）
```

**resume 流程**：`run()` 在 ASK 处暂停，返回 `AgentRunResult(stop_reason=awaiting_approval, pending_approval=...)`。`resume(ApprovalResponse)` 校验 `request_id` / `run_id` / `fingerprint`（防篡改、防跨 run、防重放），通过后一次性消费 pending：approved 执行原 ToolCall 一次，rejected 回填 `TOOL_REJECTED_BY_USER`，然后从暂停位置 +1 继续处理同一轮剩余 ToolCall，再继续 Agent 循环。resume 不重置 `max_tool_turns` 与重复检测状态。

---

## 4. 核心调用链

### 4.1 自然语言 Agent

```
用户自然语言
  → chat_agent.py（模式 A：--input_dir；模式 B：无 input_dir，模型抓取）
  → OpenAICompatibleModelClient（OpenAI-compatible tool calling）
  → AgentRuntime（有界 tool-calling 循环 + 重复检测）
  → PolicyEngine（执行前 allow/ask/deny；guarded→ASK；fetch/remediation 按工具名审批）
  → ToolRegistry → 11 个金融领域工具（含 fetch_real_market_data）
  → PipelineRunner（确定性金融数据处理）
  → ToolResult（摘要/指标/产物路径/下一步）回填模型上下文
  → 模型继续调用工具或生成最终回答（中文）
```

模式 B 抓取链：`fetch_real_market_data`（写 `run_root/raw_data`，设 `input_dir`）
→ `configure_workflow` → `profile` → ... → `report`。

### 4.2 确定性 Pipeline

```
profile（FinancialTableProfiler）
  → plan（WorkflowPlanner）
  → executor（CodeExecutor 生成 analysis-ready 宽表）
  → critic（ValidityCritic 初始审查）
  → remediation（RepairLoop 有界多轮，仅当 initial failed 且 auto_repair）
       Observe → Decide → Safety Gate → Act → Re-critic → Reflect
  → repaired critic（对 repaired_panel 重新运行 Critic）
  → report（ReportGenerator 只读前序产物汇总）
```

`STAGE_ORDER` 与 `STAGE_DISPLAY` 见 `pipeline_runner.py`。`run_full_pipeline()` 的 `auto_repair` 逻辑：Stage 4 `overall_status == "failed"` 且 `auto_repair=True` 时才进入 Stage 5；否则跳过 repair 与 repaired_critic，写 no-op 产物（`no_repair_needed` 或 `repair_disabled`）。

---

## 5. 状态与产物边界

- **run_id → run_root**：`AgentContext` 用 `normalize_run_id` 校验 run_id（禁止 `..` / `/` / `\`，匹配 `^[A-Za-z0-9][A-Za-z0-9_-]*$`），`run_root = output_base/runs/run_id`，resolve 后校验 `run_root` 严格位于 `output_base` 之下。
- **PipelineRunner 绑定当前运行**：`AgentContext.configure_runner()` 创建只属于当前 run_id/run_root 的 `PipelineRunner`，`output_root == run_root`；不同 run_id 的 runner 互不读取/恢复。
- **工具限制产物位于 run_root**：`AgentContext.ensure_artifact_in_run_root(path)` / `ensure_path_in_run_root(path)` 校验路径属于当前 run_root，越权抛 `ValueError`；工具只写当前 run_root，不覆盖原始输入 CSV。
- **Stage 12 抓取隔离**：`fetch_real_market_data` 只写当前 run 的 `run_root/raw_data/`，绝不覆盖 `data/real_market`；`set_input_dir` 校验 raw_data 位于 run_root 之下 + 五张 CSV 齐全。
- **ToolResult 返回内容**：只返回 `summary` / `status` / `metrics` / `artifacts`（产物路径）/ `next_actions`（下一步建议）；不把完整 CSV / 完整报告 / DataFrame 放入 ToolResult。
- **approval 为进程内状态**：`PendingApproval` 只存在 Runtime 内存；进程退出即丢失。`resume` 校验 `request_id` / `run_id` / `fingerprint` 保证审批绑定当前 run、参数未被篡改、不可重放。Stage 12：CLI 按 `pending.tool_name` 分别授权（fetch / remediation 互不越权）。

---

## 6. 测试结构

测试代码位于 `tests/`，共 201 项 unittest。各文件验证内容（不逐条罗列方法）：

| 测试文件 | 主要覆盖内容 | 数据来源 |
|---|---|---|
| `test_remediation_agent.py` | 策略注册表、Remediation Agent 多轮收敛/终止条件、5% 安全门、label 不进 features、`repair_history.json` 审计、`run_all.py` 三态退出码、`agent_shell` 状态恢复 | 真实 fixture 临时副本 + 内存 DataFrame |
| `test_tool_registry.py` | ToolRegistry 注册/查找/重复拒绝、JSON Schema 校验（required/type/enum/array）、handler 异常转 ToolResult | 内存（无 fixture） |
| `test_agent_runtime.py` | run_id 隔离、AgentContext、AgentRuntime 执行/回填/completed/max_tool_turns/重复检测/未知工具/requires_user_action/事件 | 真实 fixture 临时副本 + `ScriptedFakeModel` |
| `test_pipeline_tools.py` | 11 个领域工具的输入校验、不生成合成数据、configure run_root、stage 失败传递、status 只读当前 run、remediation 安全状态、label 不进 features、not_needed、无 input_dir 时 PRECONDITION_NOT_MET | 真实 fixture 临时副本 |
| `test_policy_engine.py` | 默认决策、工具级规则与优先级（DENY>ASK>ALLOW）、确定性、可序列化、fingerprint 稳定且 run 作用域、request_id 唯一 | 内存（无 fixture） |
| `test_runtime_approval.py` | ASK/DENY 时 handler 未执行、批准只执行一次、拒绝反馈、错误 request_id/参数篡改/跨 run/重复审批被拒、resume 不重置计数器、多 ToolCall 暂停后继续、guarded 默认 ASK、批准不绕过安全门、awaiting vs requires_user_action、no-op repair 只用公开 API、端到端修复路径 | 真实 fixture 临时副本 + `ScriptedFakeModel` |
| `test_openai_compatible_client.py` | ToolSpec 转 provider schema、messages 转 provider、final text 解析、单个/多个 tool_calls、非法 arguments JSON、空 choices/错误响应结构、timeout/HTTP error、错误信息不含 API Key、缺配置明确错误、`complete()` 端到端 | 内存（全 mock `requests.Session`，不访问网络） |
| `test_chat_agent.py` | CLI 参数（`--input_dir` 可选 / `--auto_approve_data_fetch`）、缺配置明确错误（退出码 1）、Fake Model 完成自然语言工具链（模式 A/B）、approval approve/reject、按工具名分别授权、输出路径与无网络运行 | 真实 fixture 临时副本 + `ScriptedFakeModel` + mock `fetch_real_data` |
| `test_fetch_tool.py` | Stage 12：`fetch_real_market_data` 注册、11 个工具、ticker/日期/数量校验、默认禁用 snapshot、raw_data 隔离、产物不逃出 run_root、fetch 后更新 input_dir、全失败结构化错误、部分失败保留成功 + warning、adapter 异常、risk=guarded | 真实 fixture 临时副本 + mock `real_data_adapter.fetch_real_data` |
| `test_astock_data_source.py` | 内置数据源 ticker 规范化、东方财富日线解析、Sina fallback、腾讯快照、行业解析、run-local 缓存和外部 Agent 依赖边界 | 内存响应 + mock HTTP，不访问网络 |
| `test_chinese_report.py` | Stage 12：中文报告标题、一页摘要标题、数值来自真实产物、summary.json 结构兼容、label 不进 approved、`passed_with_warnings` 中文显示、数据来源章节（有/无 fetch_metadata） | 真实 fixture 临时副本 |

**测试关键设计**：

- `ScriptedFakeModel`：注入 `run_chat(model_client=...)` 或直接驱动 Runtime，按顺序返回预设 `AssistantTurn`，不依赖真实 LLM。
- `FakeSession`：记录 `requests.Session.post()` 调用并按队列返回 `FakeResponse` / 抛异常，全程不访问网络；验证 payload 不含 API Key、`Authorization` 头含 Key。
- 真实 fixture 复制到临时目录后使用（`_copy_fixture`），故障注入只改临时副本，不修改 `test_data/real_market_sample`。
- `FakeCritic` 可注入 `PipelineRunner._critic_factory`，控制每轮复审结果，用于严格验证 `no_progress` 与 `max_rounds_reached` 路径。
- Stage 12 抓取测试 mock `real_data_adapter.fetch_real_data`（`unittest.mock.patch.object`），把 fixture 五张 CSV 复制到 `config.output_dir` 并返回同构 metadata，全程不访问网络。

---

## 7. 扩展指南

### 7.1 新增领域工具

1. 在 `src/agent_tools/pipeline_tools.py` 写 `_tool_xxx(arguments, context) -> ToolResult`，只返回摘要+指标+产物路径+下一步，产物经 `ctx.ensure_artifact_in_run_root()` 校验。
2. 在 `build_default_registry_specs()` 追加 `ToolSpec(name=..., description=..., input_schema=..., risk_level=..., handler=...)`。
3. 运行 `tests/test_pipeline_tools.py` 与 `tests/test_agent_runtime.py`；若涉及网络/抓取，参考 `tests/test_fetch_tool.py` 用 `mock.patch.object` mock adapter。

### 7.2 新增 ModelClient provider

1. 在 `src/agent_runtime/` 新增实现 `ModelClient` Protocol 的类（`complete(messages, tools) -> AssistantTurn`）。
2. 复用 `openai_compatible_client.py` 的纯转换函数，或自写 provider 协议互转；API Key 只从环境变量读，绝不写入日志/事件/错误信息。
3. 在 `src/chat_agent.py` 的 `_build_model_client` 增加分支或通过 `run_chat(model_client=...)` 注入。
4. 运行 `tests/test_openai_compatible_client.py`（若复用转换函数）与 `tests/test_chat_agent.py`。

### 7.3 增加 PolicyRule

1. 用 `PolicyConfig.with_overrides({"tool_name": PolicyAction.ALLOW})` 在默认策略上加 tool 级覆盖，或构造 `PolicyConfig(rules=[PolicyRule(...)])`。
2. 优先级按 action 自动赋值（DENY=3 / ASK=2 / ALLOW=1）。
3. 运行 `tests/test_policy_engine.py` 与 `tests/test_runtime_approval.py`。

### 7.4 增加 Pipeline 阶段

1. 在 `src/` 新增阶段模块与 `run_xxx.py` CLI。
2. 在 `src/pipeline_runner.py` 的 `STAGE_ORDER` / `STAGE_DISPLAY` 增加阶段，加 `run_xxx()` 公开方法。
3. 若需暴露给 Agent，在 `agent_tools/pipeline_tools.py` 包装成工具。
4. 运行 `tests/test_remediation_agent.py` 与相关阶段测试。

### 7.5 修改后应运行的测试

```powershell
python -B -m unittest discover -s tests -v
```

全量 201 项；改动局部时至少运行对应文件（见 [§6](#6-测试结构)）。
