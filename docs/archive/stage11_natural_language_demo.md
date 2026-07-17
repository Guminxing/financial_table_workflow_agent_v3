# Stage 11 — Natural Language Agent Demo

> 本文档说明 Stage 11 引入的**自然语言 Agent Demo**：把 Stage 9–10 的 Agent Runtime
> 接入真实 OpenAI-compatible LLM，并提供自然语言 CLI（`src/chat_agent.py`），让用户
> 看到"自然语言 → 自主工具调用 → 报告"的完整闭环。
>
> **本阶段优先 Demo 可用性**，不实现跨进程 session persistence、MCP、多 Agent、
> 插件系统或生产级安全加固。当前 session 只存在进程内。

---

## 1. 目标

```
用户自然语言
→ OpenAICompatibleModelClient（真实 LLM，OpenAI-compatible tool calling）
→ AgentRuntime（有界 tool-calling 循环 + 重复检测）
→ PolicyEngine（执行前 allow/ask/deny；guarded→ASK）
→ ToolRegistry → PipelineRunner 领域工具
→ ToolResult 回填模型上下文
→ 模型继续或输出最终自然语言总结
```

---

## 2. 真实模型适配器：`src/agent_runtime/openai_compatible_client.py`

实现 `OpenAICompatibleModelClient`（结构化满足 `ModelClient` Protocol），使用已有
`requests`，支持标准 OpenAI-compatible Chat Completions tool calling。

**配置来源**（API Key 只从环境变量读取，不写入日志/事件/错误信息）：

| 环境变量 | 用途 |
|---|---|
| `FTA_LLM_API_KEY` | API Key，只放进 HTTP `Authorization: Bearer` 头 |
| `FTA_LLM_BASE_URL` | OpenAI-compatible base URL（如 `https://api.openai.com/v1`） |
| `FTA_LLM_MODEL` | 模型名 |

CLI 可用 `--model` / `--base_url` 覆盖后两项；API Key 只从环境变量读取。

**协议转换**（全部为模块级纯函数，便于单测）：

| 函数 | 转换 |
|---|---|
| `tool_spec_to_provider(spec)` | 通用 ToolSpec schema → provider `{"type":"function","function":{name,description,parameters}}`；`risk_level` 不发给 provider |
| `messages_to_provider(messages)` | Runtime messages → provider messages；assistant `tool_calls` 的 `arguments` 序列化为 JSON 字符串；tool 结果去掉 `name` |
| `response_to_turn(data)` | provider 响应 → `AssistantTurn`；有 `tool_calls` → `tool_calls`（丢弃伴随 content）；否则 `final_text=content` |

**`function.arguments` 解析**：dict 直接用；字符串 `json.loads`（必须是 object，否则
`ModelResponseError`）；None/空串 → `{}`；其他类型 → `ModelResponseError`。支持一次
返回多个 `tool_calls`。

**错误处理**：

- timeout / HTTP 非 200 / 非 JSON body → `ModelRequestError`。
- 空 choices / 非法响应结构 / 非法 tool_call / 非法 arguments → `ModelResponseError`。
- 错误信息**绝不**包含 API Key（`_scrub` 兜底替换为 `***`；payload 不含 key）。
- 使用可注入 `requests.Session`，测试全部 mock，不访问网络。
- 非必要不新增依赖（仅用已有的 `requests`）。

---

## 3. System Prompt：`prompts/financial_agent_system.md`

简洁的 system prompt，要求模型：

- 理解用户金融表格处理目标，根据状态自主选择工具。
- 遵守 `configure → profile → plan → prepare → validate → repair → revalidate → report` 依赖关系。
- 校验失败时先 `inspect_validation_failures` 读失败项，再决定下一步。
- 不编造工具结果或文件；不直接修改原始 CSV；不把 label 当 feature。
- 无法自动处理时明确转人工；不输出投资建议。
- 最终回答包含状态、关键指标、报告路径和未解决问题。

CLI 在构造 client 时读取该文件作为 system prompt；文件缺失时回退到最小内联 prompt。

---

## 4. 自然语言 CLI：`src/chat_agent.py`

```powershell
python -B src/chat_agent.py `
  --input_dir test_data/real_market_sample `
  --output_base outputs_real `
  --prompt "检查这些真实市场数据，生成建模宽表，必要时安全修复并输出报告"
```

参数：

| 参数 | 说明 |
|---|---|
| `--input_dir` | 真实市场数据目录（默认 `test_data/real_market_sample`） |
| `--output_base` | 产物根（默认 `outputs_real`），每次 run 隔离在 `<base>/runs/<run_id>/` |
| `--run_id` | 可选；未传时自动生成 `run_<8hex>` |
| `--prompt` | 可选；未传时从终端读取 |
| `--model` | 覆盖 `FTA_LLM_MODEL` |
| `--base_url` | 覆盖 `FTA_LLM_BASE_URL` |
| `--max_tool_turns` | 模型工具调用轮上限（默认 12） |
| `--auto_approve_remediation` | 现场 Demo 自动批准 guarded remediation（仍走 ASK 门，执行仍受安全门约束） |
| `--max_repair_rounds` / `--max_row_loss_ratio` / `--analysis_goal` | 透传给 PipelineRunner |

**启动流程**（`run_chat`）：

1. 创建 `AgentContext`（校验 input_dir，绝不回退合成数据）。
2. 创建默认 `ToolRegistry`（10 个领域工具）。
3. 创建 `PolicyEngine`（默认策略：guarded→ASK）。
4. 创建 `OpenAICompatibleModelClient`（读环境变量；缺失时明确报错退出码 1）。
5. 创建 `AgentRuntime`（注入事件回调打印进度）。
6. 执行用户自然语言请求。
7. 输出简洁的工具调用进度（基于 `AgentEvent` 回调，不打印完整 messages / 隐藏推理 / API Key）。
8. 处理 `awaiting_approval`（交互式 `Approve? [y/N]` 或 auto-approve）。
9. 输出模型最终回答 + `Run root` + `Final report` 路径。

**为便于测试**，主逻辑拆成：

```python
run_chat(args, model_client=None, input_fn=input, output_fn=print)
```

测试注入 Fake Model 与输入输出函数，不访问网络。

**审批交互**：

```text
Agent requests:
  run_safe_remediation
  arguments: {}
Approve? [y/N]
```

- `y` → `runtime.resume(ApprovalResponse(approved=True))`
- 其他 → `runtime.resume(ApprovalResponse(approved=False))`
- `--auto_approve_remediation` 时跳过输入，自动回复 approved（提示行经 `output_fn` 输出）。

**退出码**：0 完成；1 配置/运行错误；2 需要人工介入（`manual_review` /
`requires_user_action` / awaiting_approval 被拒绝后模型未完成）。

---

## 5. Runtime 事件回调（Stage 11 增量）

`AgentRuntime.__init__` 增可选 `event_callback: Callable[[AgentEvent], None]`。
`_record_event` 在追加事件后调用回调（异常吞掉，不影响主循环）。CLI 用它打印
`[tool] <name> ... <status>` / `[approval] requested: ...` / `[stop] <reason>` 进度行。
向后兼容：不传回调时行为与 Stage 10 完全一致。

---

## 6. Demo 体验

终端输出示例（Fake Model 驱动；真实 LLM 时进度行内容由模型实际选择决定）：

```text
Financial Table Workflow Agent

Run: run_...
Input: test_data/real_market_sample

[user]
检查数据并生成最终报告

[tool] configure_workflow ........ ... configured
[tool] profile_financial_data .... ... completed
[tool] create_workflow_plan ...... ... completed
[tool] prepare_financial_panel ... ... completed
[tool] validate_financial_panel .. ... completed
[tool] run_safe_remediation ...... ... not_needed
[tool] validate_repaired_panel ... ... completed
[tool] generate_workflow_report .. ... completed
[stop] completed

[assistant]
数据处理已经完成……
Run root: .../outputs_real/runs/run_...
Final report: .../final_report/final_workflow_report.md
```

进度显示基于 `AgentEvent` 回调实现；CLI 不直接调用 `PipelineRunner`。

---

## 7. 测试

新增两个测试文件，共 43 项（31 + 12），全部不访问网络：

- `tests/test_openai_compatible_client.py`（31 项）：ToolSpec 转 provider schema、
  messages 转 provider、final text 解析、单个/多个 tool_calls、非法 arguments JSON、
  空 choices/错误响应结构、timeout/HTTP error、错误信息不含 API Key、缺配置明确错误、
  `complete()` 端到端（mock）。
- `tests/test_chat_agent.py`（12 项）：CLI 参数与环境变量读取、缺配置明确错误、
  Fake Model 完成自然语言工具链、approval approve/reject、`--auto_approve_remediation`、
  输出含 run_root 与报告路径、不访问真实网络。

原有 102 项测试继续通过（全量 `python -B -m unittest discover -s tests -v`）。

---

## 8. 文档与配置

- 新增 `docs/stage11_natural_language_demo.md`（本文件）。
- 新增 `.env.example`（**只写占位符**；项目不自动读取或提交真实 `.env`）。
- 更新 `README.md` 与 `CODE_STRUCTURE.md`：环境变量配置、自然语言 Demo 命令、
  Agent 调用链、审批说明、当前限制。

---

## 9. 当前限制（明确声明）

- **只支持 OpenAI-compatible 接口**（标准 Chat Completions tool calling）。
- **session 只存在进程内**：不实现跨进程 session persistence；进程退出即丢失
  Runtime 状态（pending approval / 事件流不落盘）。
- **不实现 MCP / 多 Agent / 插件系统**。
- **不是生产级安全加固**：审批是进程内交互；API Key 由调用方负责保管。
- **不提交 API Key**：`.env` 被 `.gitignore` 忽略；`.env.example` 只含占位符。
- **不记录隐藏推理**：只记录用户输入、工具调用、工具结果、最终文本。
- **不把 Demo 描述成生产级系统**。

---

## 10. 下一阶段（不在本阶段实现）

- session 持久化（`AgentRunResult` + 事件流 + pending approval 落盘，跨进程恢复）。
- 真实模型适配器的流式输出、重试、限流。
- 更细粒度 PolicyEngine（按工具/参数动态决策）。
- MCP / 多 Agent / 插件系统。

---

## 11. 新增 / 修改的文件

新增：

```
src/agent_runtime/openai_compatible_client.py   # OpenAICompatibleModelClient + 纯转换函数
src/chat_agent.py                                # 自然语言 CLI（run_chat 可注入测试）
prompts/financial_agent_system.md                # system prompt
tests/test_openai_compatible_client.py           # 31 项适配器测试
tests/test_chat_agent.py                          # CLI 测试（Stage 12 扩展至 21 项）
docs/stage11_natural_language_demo.md            # 本文件
.env.example                                      # 占位符配置（不提交真实 .env）
```

修改：

```
src/agent_runtime/runtime.py                     # __init__ 增 event_callback；_record_event 调用回调
README.md                                         # Stage 11 小节
CODE_STRUCTURE.md                                 # Stage 11 结构说明
```

未修改：`test_data/real_market_sample/` 真实 fixture、所有现有 CLI、
Stage 9–10 的 102 项测试（行为不变；`event_callback` 默认 None，向后兼容）。

---

## 12. Stage 12 增量（自然语言抓取 + 中文报告）

Stage 12 在 Stage 11 基础上扩展（详见
`docs/stage12_natural_language_data_fetch_and_chinese_report.md`）：

- **新增工具 `fetch_real_market_data`**（guarded）：模型从自然语言提取 tickers /
  start_date / end_date，抓取真实 A 股数据到当前 run 的 `raw_data`，再走完整流程。
- **`--input_dir` 改为可选**：模式 A（已有 CSV）/ 模式 B（自然语言抓取）。
- **`--auto_approve_data_fetch`** 新增 CLI 参数；真实数据由项目内置数据源获取，审批按
  `pending.tool_name` 分别授权（fetch / remediation 互不越权）。
- **固定 Markdown 最终报告中文化**：`final_workflow_report.md` /
  `final_workflow_one_page.md` 中文正文 + "数据来源与时间边界"章节。
- **AgentContext 支持无 input_dir 启动**：`create_without_input_dir` + `set_input_dir`。
- Stage 12 当时测试扩展至 191 项（新增 `test_fetch_tool.py` 28 项 +
  `test_chinese_report.py` 9 项 + `test_chat_agent.py` 9 项）；当前独立数据源改造后为
  199 项。
