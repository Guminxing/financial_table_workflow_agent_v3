# Financial Table Workflow Agent (v3)

面向金融表格的数据准备、质量审计与真实 A 股数据接入工作流。
**v3 只使用真实市场数据作为正式输入**；合成样例数据及其自动生成逻辑已彻底移除。

> 目录职责一览见 [DIRECTORY_GUIDE.md](./DIRECTORY_GUIDE.md)；代码结构、模块职责与执行调用链见 [CODE_STRUCTURE.md](./CODE_STRUCTURE.md)；分阶段设计见 [docs/](./docs/)。

> **Stage 9（Agent Runtime MVP）**：新增模型驱动的 tool-calling Agent Runtime 骨架
> （`src/agent_runtime/` + `src/agent_tools/`），按 run_id 隔离每次运行产物。
> **本阶段不接入真实 LLM**，由测试中的 Fake Model 驱动验证。详见
> [docs/stage9_agent_runtime_mvp.md](./docs/stage9_agent_runtime_mvp.md)。
> **Stage 10（PolicyEngine + 审批恢复）**：在每次工具执行前加入确定性权限判断
> （allow/ask/deny），实现进程内 `resume(ApprovalResponse)` 暂停/恢复。详见
> [docs/stage10_policy_and_approval.md](./docs/stage10_policy_and_approval.md)。
> **Stage 11（Natural Language Demo）**：接入真实 OpenAI-compatible LLM，新增自然语言
> CLI `src/chat_agent.py`，让用户看到"自然语言 → 自主工具调用 → 报告"的完整闭环。
> 详见 [docs/stage11_natural_language_demo.md](./docs/stage11_natural_language_demo.md)。
> 原 Agent Shell 仍是固定命令模式，未被替换。

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
| 文档 | `docs/`、`prompts/`、`README.md`、`DIRECTORY_GUIDE.md`、`CODE_STRUCTURE.md` |

详见 [DIRECTORY_GUIDE.md](./DIRECTORY_GUIDE.md)。代码结构、模块职责与执行调用链见 [CODE_STRUCTURE.md](./CODE_STRUCTURE.md)。

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

**第四步：体验自然语言 Agent（Stage 11，需真实 LLM）**

先配置环境变量（API Key 只从环境变量读取，不写入日志/事件/错误信息）：

```powershell
$env:FTA_LLM_API_KEY = "sk-..."
$env:FTA_LLM_BASE_URL = "https://api.openai.com/v1"
$env:FTA_LLM_MODEL = "gpt-4o-mini"
```

然后运行：

```bash
python -B src/chat_agent.py `
  --input_dir test_data/real_market_sample `
  --output_base outputs_real `
  --prompt "检查这些真实市场数据，生成建模宽表，必要时安全修复并输出报告"
```

> 现场 Demo 可加 `--auto_approve_remediation` 自动批准 guarded 修复（仍走 ASK 门，
> 执行受内部安全门约束）。详见
> [docs/stage11_natural_language_demo.md](./docs/stage11_natural_language_demo.md)。

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

## 9. Agent Runtime MVP（Stage 9）

Stage 9 新增**模型驱动的 tool-calling Agent Runtime 骨架**，与原固定命令式
Agent Shell 并存（原 Shell 未被替换）：

- `src/agent_runtime/`：`models.py`（ToolCall/ToolResult/ToolSpec/AgentEvent/...）、
  `context.py`（AgentContext + run_id 隔离）、`registry.py`（ToolRegistry + schema 校验）、
  `model_client.py`（ModelClient Protocol）、`runtime.py`（有界 tool-calling 循环）。
- `src/agent_tools/pipeline_tools.py`：把 PipelineRunner 阶段包装成 10 个领域工具。
- 每次 Agent run 用独立 `outputs_real/runs/<run_id>/` 隔离产物。

**本阶段不接入真实 LLM**：`ModelClient` 只定义 Protocol，由测试中的
`ScriptedFakeModel` 驱动验证。真实模型适配器、`chat_agent.py` 自然语言 CLI、
权限审批（allow/ask/deny）、暂停恢复、session 持久化均属后续阶段。

调用链：用户消息 → ModelClient → Fake Model 返回 ToolCall → AgentRuntime →
ToolRegistry → PipelineRunner 领域工具 → ToolResult → 回填模型上下文 →
Fake Model 决定继续或输出最终回答。

详见 [docs/stage9_agent_runtime_mvp.md](./docs/stage9_agent_runtime_mvp.md)。

## 10. PolicyEngine + 审批恢复（Stage 10）

Stage 10 在 Stage 9 的 Runtime 上加入**确定性权限审批**与**进程内暂停/恢复**：

- `src/agent_runtime/policy.py`：`PolicyEngine` + `PolicyAction`(ALLOW/ASK/DENY) +
  `PolicyConfig`/`PolicyRule`/`PolicyDecision`/`PendingApproval`/`ApprovalResponse`。
  默认策略：`read`/`workspace_write`→ALLOW、`guarded`→ASK、未知→DENY。
  优先级：工具级 DENY > ASK > ALLOW > risk 默认 > 默认 DENY。完全确定性，不调用模型。
- `src/agent_runtime/runtime.py`：执行工具前必过 `PolicyEngine`；ASK 暂停返回
  `stop_reason=awaiting_approval` + `pending_approval`；`resume(ApprovalResponse)`
  校验 request_id/run_id/fingerprint（防篡改、防跨 run、防重放），批准执行一次、
  拒绝回填 `TOOL_REJECTED_BY_USER`，从断点继续；多 ToolCall 中途暂停后不丢失不重复。
- `src/pipeline_runner.py`：新增薄公开方法 `run_noop_repair()`，`pipeline_tools`
  的 not_needed 分支改走它，不再触碰私有方法。

**本阶段只做进程内暂停/恢复**，不实现 session 持久化、真实 LLM、chat CLI。
审批只决定"是否执行"，执行仍走 PipelineRunner → Remediation Agent，**不绕过**
删行阈值、轮数限制、标签泄漏保护等内部安全门。

详见 [docs/stage10_policy_and_approval.md](./docs/stage10_policy_and_approval.md)。

## 11. Natural Language Agent Demo（Stage 11）

Stage 11 把 Agent Runtime 接入**真实 OpenAI-compatible LLM**，并提供自然语言 CLI
`src/chat_agent.py`，完成"自然语言 → 自主工具调用 → 报告"的完整闭环：

- `src/agent_runtime/openai_compatible_client.py`：`OpenAICompatibleModelClient`
  实现 `ModelClient` Protocol，用已有 `requests` 调标准 Chat Completions tool calling。
  纯转换函数 `tool_spec_to_provider` / `messages_to_provider` / `response_to_turn`
  负责通用协议 ↔ provider 协议互转；支持一次返回多个 tool_calls；`function.arguments`
  必须解析为 JSON object；timeout/HTTP error/空 choices/非法 JSON/非法结构 → 结构化异常；
  错误信息**绝不**含 API Key（`_scrub` 兜底）；测试全部 mock，不访问网络。
- `prompts/financial_agent_system.md`：简洁 system prompt，约束模型遵守工具依赖、
  不编造结果、不把 label 当 feature、无法自动处理时转人工、不输出投资建议。
- `src/chat_agent.py`：自然语言 CLI。`run_chat(args, model_client=None,
  input_fn=input, output_fn=print)` 可注入 Fake Model 与 IO 函数测试。启动流程：
  AgentContext → ToolRegistry → PolicyEngine → ModelClient → AgentRuntime →
  执行 → 事件回调打印进度 → 处理 `awaiting_approval`（`Approve? [y/N]` 或
  `--auto_approve_remediation`）→ 输出最终回答 + `Run root` + `Final report` 路径。
- `src/agent_runtime/runtime.py`：`__init__` 增可选 `event_callback`，`_record_event`
  调用回调（异常吞掉），供 CLI 实时打印工具调用进度；默认 None，向后兼容。

**环境变量**（API Key 只从环境变量读取，不写入日志/事件/错误信息；`.env` 被
`.gitignore` 忽略，`.env.example` 只含占位符，项目不自动读取 `.env`）：

| 变量 | 用途 |
|---|---|
| `FTA_LLM_API_KEY` | API Key，只放进 HTTP `Authorization: Bearer` 头 |
| `FTA_LLM_BASE_URL` | OpenAI-compatible base URL |
| `FTA_LLM_MODEL` | 模型名（须支持 tool calling） |

**当前限制**：只支持 OpenAI-compatible 接口；session 只存在进程内（不实现跨进程
持久化）；不实现 MCP / 多 Agent / 插件系统；不是生产级安全加固；不记录隐藏推理；
不把 Demo 描述成生产级系统。

详见 [docs/stage11_natural_language_demo.md](./docs/stage11_natural_language_demo.md)。
