# Stage 9 — Agent Runtime MVP

> 本文档说明 Stage 9 引入的 **Agent Runtime MVP 骨架**：一个可由 Fake Model 驱动的
> tool-calling Agent Runtime，把现有 PipelineRunner 阶段包装成领域工具，并按 run_id
> 隔离每次运行的产物。
>
> **本阶段不接入真实 LLM**。Runtime 由测试中的 `ScriptedFakeModel` 驱动验证。
> 真实模型适配器、自然语言 CLI（`chat_agent.py`）、权限审批、暂停恢复、session 持久化
> 均属后续阶段。

---

## 1. 目标

把"固定命令式 Agent Shell"升级为"模型驱动的 tool-calling Agent Runtime"骨架：

```
用户消息
→ ModelClient Protocol
→ Fake Model 返回结构化 ToolCall
→ AgentRuntime
→ ToolRegistry
→ PipelineRunner 领域工具
→ ToolResult
→ 回填模型上下文
→ Fake Model 决定继续调用或输出最终回答
```

本轮只完成骨架（开发顺序第 1–5 步）：

1. Agent run_id 和运行目录隔离。
2. 验证并保持"绝不自动生成模拟数据"。
3. 定义 `ToolSpec` / `ToolCall` / `ToolResult` / `AgentEvent` 等协议。
4. 把现有 PipelineRunner 阶段包装成领域工具。
5. 实现可由 Fake Model 驱动的 tool-calling Agent Runtime。

---

## 2. 与原 Agent Shell 的区别

| 维度 | 原 Agent Shell（`src/agent_shell.py`） | Agent Runtime MVP（`src/agent_runtime/`） |
|---|---|---|
| 驱动方式 | 固定命令 + 别名映射（`run all` / `status` / ...） | 模型返回结构化 `ToolCall`，Runtime 执行 |
| 模型 | 无 LLM，纯命令分发 | `ModelClient` Protocol（本轮由 Fake Model 实现） |
| 工具协议 | 无（命令直接调 PipelineRunner 方法） | `ToolSpec` / `ToolCall` / `ToolResult` + `ToolRegistry` |
| 事件模型 | 无 | `AgentEvent`（user_message / assistant_turn / tool_call / tool_result / runtime_stop） |
| 目录隔离 | 共用 `outputs_real/` | 每次 run 独立 `outputs_real/runs/<run_id>/` |
| 停止条件 | 用户输入 `exit` | `completed` / `max_tool_turns` / `repeated_tool_call` / `requires_user_action` / `model_protocol_error` / `runtime_error` |

**原 Agent Shell 仍是固定命令模式，未被替换或删除**；Agent Runtime 是并行的、更结构化的新入口骨架。

---

## 3. ModelClient Protocol

`src/agent_runtime/model_client.py` 定义抽象模型接口，**不**依赖任何具体 SDK：

```python
@runtime_checkable
class ModelClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn: ...
```

- 生产代码只定义 Protocol。
- `FakeModel` / `ScriptedFakeModel` 放在测试代码中（`tests/test_agent_runtime.py`）。
- 不读取环境变量中的 API Key。
- Runtime 不应知道具体模型供应商。

---

## 4. ToolSpec / ToolResult

`src/agent_runtime/models.py` 定义清晰、最小、可序列化的数据结构：

- **`ToolCall`**：`call_id` / `name` / `arguments`。
- **`ToolError`**：`code` / `message` / `retryable`（至少支持这三项）。
- **`ToolResult`**：`ok` / `status` / `summary` / `metrics` / `artifacts` / `next_actions` / `error` / `requires_user_action`。
- **`ToolSpec`**：`name` / `description` / `input_schema` / `risk_level` / `handler`。
- **`RiskLevel`**：`read` / `workspace_write` / `guarded`（本轮只记录，不实现完整审批）。
- **`AssistantTurn`**：`final_text` 或 `tool_calls`（恰一非空，否则 `model_protocol_error`）。
- **`AgentEvent`**：`event_type` / `timestamp` / `payload`。
- **`AgentRunResult`**：`final_text` / `stop_reason` / `events` / `tool_turns`。

所有结构都容易转换为 JSON；**不**把 DataFrame 或 PipelineRunner 对象直接放入事件。

---

## 5. ToolRegistry

`src/agent_runtime/registry.py` 实现 `ToolRegistry`：

- `register(spec)` / `get(name)` / `list_specs()` / `schemas_for_model()` / `execute(call, context)`。
- 工具名唯一；重复注册报错。
- 未知工具返回结构化 `ToolResult`（`code=UNKNOWN_TOOL`），不抛到 Runtime 顶层。
- 参数按 `input_schema` 做基础校验；失败返回 `code=INVALID_TOOL_ARGUMENTS`、`retryable=True`。
- handler 异常转换为 `code=TOOL_EXECUTION_ERROR`；**不**把完整 traceback 返回模型。
- `schemas_for_model()` 返回通用 JSON Schema 风格结构，不绑定某家模型 API。

**基础 schema 校验支持的子集**（本轮只实现项目工具所需）：
`object` / `required` / `properties` / `string` / `integer` / `number` / `boolean` / `array` / `enum`。

**限制**（在此说明）：不支持 `additionalProperties` / `pattern` / `minimum`·`maximum` /
`minItems`·`maxItems` / `oneOf`·`allOf`·`anyOf`。`integer` 接受 `int`；`number` 接受 `int`/`float`；
`boolean` 严格接受 `bool`（Python 中 `bool` 是 `int` 子类，已显式排除）。

---

## 6. AgentContext 和 run_id 隔离

`src/agent_runtime/context.py` 提供 `AgentContext` + run_id 隔离：

- `run_id` 必须匹配 `^[A-Za-z0-9][A-Za-z0-9_-]*$`；禁止 `..` / `/` / `\`（路径穿越防护）。
- `run_root` 严格位于 `output_base / "runs" / run_id`（resolve 后用 `relative_to` 校验）。
- `input_dir` 不存在、为空或缺少必要 CSV（price/volume/fundamentals/industry/calendar）时返回明确错误，**绝不**生成合成数据。
- 每个 run 一个独立 `PipelineRunner`（`configure_runner()` 创建，`output_root == run_root`）。
- `ensure_artifact_in_run_root()` 校验产物路径属于当前 run_root，越权即抛错。
- 旧 `PipelineRunner` API 继续接受普通 `output_root`（不强制 `runs/` 结构）。

建议 run_id 格式：`run_YYYYMMDD_HHMMSS_<short-id>`。测试中允许传入固定 run_id 以保证可重复。

```
outputs_real/
  runs/
    <run_id>/
      profiles/  plans/  prepared/  validation/  repaired/
      validation_repaired/  final_report/  sessions/
```

---

## 7. Pipeline 工具列表

`src/agent_tools/pipeline_tools.py` 注册 10 个领域工具（按 pipeline 顺序）：

| # | 工具名 | 调用 | risk_level |
|---|---|---|---|
| 1 | `configure_workflow` | 校验输入目录 + 更新 AgentContext + 创建当前 run 的 runner（不执行 pipeline） | workspace_write |
| 2 | `inspect_pipeline_status` | `PipelineRunner.get_status()`（只读当前 run） | read |
| 3 | `profile_financial_data` | `PipelineRunner.run_profile()` | workspace_write |
| 4 | `create_workflow_plan` | `PipelineRunner.run_planner()` | workspace_write |
| 5 | `prepare_financial_panel` | `PipelineRunner.run_executor()` | workspace_write |
| 6 | `validate_financial_panel` | `PipelineRunner.run_initial_critic()` | workspace_write |
| 7 | `run_safe_remediation` | `PipelineRunner.run_remediation_agent()`（薄公开方法） | guarded |
| 8 | `validate_repaired_panel` | `PipelineRunner.run_repaired_critic()` | workspace_write |
| 9 | `generate_workflow_report` | `PipelineRunner.run_final_report()` | workspace_write |
| 10 | `inspect_validation_failures` | 只读当前 run 的 validation JSON | read |

**工具包装要求**（已实现）：

- 优先调用 PipelineRunner 公开方法；不复制业务代码。
- 不把完整 CSV / 完整报告 / 完整 DataFrame 放入 `ToolResult`；只返回摘要、指标、产物路径、下一步建议。
- artifact path 必须属于当前 run_root。
- stage `status=failed` 时 `ToolResult.ok=False`。
- `manual_review_required` 时 `requires_user_action=True`。
- `label_in_approved_features=True` 时返回安全错误（`LABEL_LEAKAGE_DETECTED`）。
- 每个写工具只允许写当前 run_root。
- 不暴露 `fetch_real_market_data`（本轮不实现网络工具）。

`run_safe_remediation` 的安全语义：

- 只在 initial critic 为 `failed` 时执行多轮 Remediation Agent；已 `passed`/`passed_with_warnings` 返回 `not_needed`。
- 继续使用现有 `max_repair_rounds` / `max_row_loss_ratio` / `no_progress` / `manual_review_required` / `unresolved_checks` / label 泄漏保护。
- 不重写修复策略；不绕过现有安全门。
- 通过 `PipelineRunner.run_remediation_agent()`（薄公开方法）委托现有私有 `_run_remediation_agent()` / `_remediation_agent_loop()`，**不**复制多轮逻辑。

---

## 8. Runtime 循环

`src/agent_runtime/runtime.py` 实现最小 tool-calling 循环：

1. 追加用户消息。
2. 调用 `ModelClient.complete()`。
3. 若返回 `final_text`，正常结束（`completed`）。
4. 若返回 `tool_calls`：
   - 顺序执行，不并行；
   - 记录 `tool_call` 事件；
   - 执行 `ToolRegistry`；
   - 记录 `tool_result` 事件；
   - 将结构化结果追加到下一轮模型 messages。
5. 继续调用模型，直到停止条件。

**硬约束**：

- Runtime 不直接调用 PipelineRunner；只能通过 ToolRegistry 调用工具。
- Runtime 不自行判断金融校验是否通过；金融状态以 `ToolResult` / PipelineRunner / Critic 为唯一事实来源。
- 工具失败后必须把失败结果反馈给模型，不能假装成功。
- `requires_user_action=True` 时本轮停止，不能继续自动修复。
- 不允许无限循环（`max_tool_turns` 默认 12 + 重复检测双保险）。
- 不记录或输出隐藏推理过程；只记录输入、工具调用、工具结果和最终文本。

---

## 9. 停止条件

`stop_reason` 取值（`src/agent_runtime/models.py` 的 `StopReason`）：

| stop_reason | 触发 |
|---|---|
| `completed` | 模型返回 `final_text` |
| `max_tool_turns` | 达到 `max_tool_turns`（默认 12） |
| `repeated_tool_call` | 连续两轮相同工具名 + 规范化参数 |
| `requires_user_action` | 工具返回 `requires_user_action=True`（如 manual_review_required） |
| `model_protocol_error` | `AssistantTurn` 的 `final_text` 与 `tool_calls` 同时为空或同时非空 |
| `runtime_error` | Runtime 内部不可恢复错误（兜底） |

---

## 10. Fake Model 测试方式

`tests/test_agent_runtime.py` 实现 `ScriptedFakeModel`：

- 按顺序返回预设 `AssistantTurn`。
- 记录收到的 `messages` 和 `tools`。
- 预设响应耗尽时明确失败（抛 `RuntimeError`）。
- 不访问网络，不依赖任何 LLM SDK。

测试覆盖：

- **B. AgentContext/run_id**：合法 run_id、拒绝 `../x`、拒绝 `/`·`\`、run_root 位置、两 run 隔离、不跨 run 恢复 repair_history。
- **D. AgentRuntime**：执行 tool_call、ToolResult 回填、completed、max_tool_turns、重复检测、未知工具不崩溃、工具失败反馈、requires_user_action 停止、事件正确、Runtime 不直接依赖 PipelineRunner、model_protocol_error。

`tests/test_tool_registry.py` 覆盖 A1–A7；`tests/test_pipeline_tools.py` 覆盖 C1–C10。

---

## 11. 当前安全边界

- **有界轮数**：`max_tool_turns`（默认 12）+ Remediation Agent 的 `max_repair_rounds`（默认 3）。
- **绝不生成合成数据**：`AgentContext.create()` 校验 input_dir，缺失即明确失败。
- **run_id 隔离**：路径穿越防护 + `run_root` 严格位于 `output_base/runs/run_id`。
- **不覆盖原始输入 CSV**：工具只写 run_root 下派生产物。
- **label 泄漏保护**：`label_in_approved_features=True` 时工具返回安全错误并 `requires_user_action=True`。
- **manual_review_required 传递**：安全门违反 / no_progress / no_actionable_strategy → `requires_user_action=True`，Runtime 停止。
- **不伪造金融数据**：修复策略只做保守删除 / strip；不回填 announce_date；不修改 label 角色。
- **不接入真实 LLM**：本轮无 API Key、无网络模型调用。
- **不实现任意 shell / 任意 Python 执行 / MCP / 多 Agent / 插件 / 后台任务**。

---

## 12. 本阶段未接入真实 LLM

明确声明：

- 本阶段**没有**接入 OpenAI / Anthropic / Gemini / Ollama 或其他真实模型。
- 本阶段**没有**添加任何 API Key 或读取环境变量中的凭据。
- 本阶段**没有**实现网络模型调用。
- 当前使用 **Fake Model**（`ScriptedFakeModel`）验证 Agent Runtime 的 tool-calling 循环、停止条件、事件模型与 run_id 隔离。
- 旧 Agent Shell 仍是固定命令模式，未被替换。

---

## 13. 下一阶段计划

本阶段完成后停止，等待用户确认，再进入：

- **PolicyEngine**：基于 `RiskLevel` 的 `allow` / `ask` / `deny` 权限审批。
- **allow / ask / deny**：写工具与 guarded 工具的运行时审批门。
- **暂停与恢复**：Runtime 可在 `requires_user_action` 后暂停，用户决策后恢复。
- **session 持久化**：把 `AgentRunResult` + 事件流持久化到 run_root，跨进程恢复。
- **真实模型适配器**：实现 `ModelClient` 的真实 LLM 适配器（仍受 PolicyEngine 约束）。
- **`chat_agent.py`**：自然语言 CLI 入口（本轮不创建）。

---

## 14. 新增 / 修改的文件

新增：

```
src/agent_runtime/__init__.py
src/agent_runtime/models.py
src/agent_runtime/context.py
src/agent_runtime/registry.py
src/agent_runtime/model_client.py
src/agent_runtime/runtime.py
src/agent_tools/__init__.py
src/agent_tools/pipeline_tools.py
tests/test_tool_registry.py
tests/test_agent_runtime.py
tests/test_pipeline_tools.py
docs/stage9_agent_runtime_mvp.md   # 本文件
```

修改：

```
src/pipeline_runner.py   # 新增薄公开方法 run_remediation_agent()
README.md                # 增加 Agent Runtime MVP 结构说明
CODE_STRUCTURE.md        # 增加 agent_runtime / agent_tools / 新测试说明
```

未修改：`test_data/real_market_sample/` 中的真实 fixture、所有现有 CLI、现有 23 项测试。
