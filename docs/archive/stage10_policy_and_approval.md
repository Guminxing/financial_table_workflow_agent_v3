# Stage 10 — PolicyEngine + allow/ask/deny + 进程内审批恢复

> 本文档说明 Stage 10 引入的**确定性权限审批**与**进程内暂停/恢复**：
> 在每次工具执行前加入 PolicyEngine 判断（ALLOW 执行 / ASK 暂停 / DENY 拒绝），
> 并实现 `AgentRuntime.resume(ApprovalResponse)` 从暂停位置继续。
>
> **本阶段只实现进程内暂停/恢复**，不实现 session 持久化、真实 LLM、chat CLI、
> MCP、多 Agent 或任意 Shell/Python 工具。PolicyEngine 完全确定性，不调用模型。

---

## 1. 目标

把 Stage 9 的"模型一发 ToolCall 就直接执行"升级为"执行前先过权限门"：

```
ToolCall
→ PolicyEngine
  ├─ ALLOW：执行工具
  ├─ ASK：暂停并返回 PendingApproval
  └─ DENY：不执行，向模型回填结构化拒绝
→ ApprovalResponse
→ AgentRuntime.resume()
→ 执行或拒绝原 ToolCall
→ 继续 Agent 循环
```

同时清理 Stage 9 的一个边界问题：`pipeline_tools` 的 not_needed 分支不再
直接调用 `PipelineRunner` 的私有方法，改走新增的薄公开方法 `run_noop_repair()`。

---

## 2. 清理 Stage 9 边界：`run_noop_repair()`

`src/pipeline_runner.py` 新增薄公开方法 `run_noop_repair(initial_status=None,
no_op_kind="no_repair_needed")`：

- 封装 `run_full_pipeline` 中"initial critic 未 failed / auto_repair=False"分支的
  no-op 逻辑（mark_skipped + no-op 产物 + repair_history）。
- `run_full_pipeline` 的 else 分支改为计算 `no_op_kind` 后调用
  `self.run_noop_repair(initial_status, no_op_kind)`，**单一事实源**（DRY）。
- `pipeline_tools._tool_run_safe_remediation` 的 not_needed 分支改为单行
  `runner.run_noop_repair(initial_status, "no_repair_needed")`，删除所有对
  `_write_noop_repair_artifacts` / `_write_repair_history` / `_mark_skipped` /
  `repair_rounds_run` / `termination_reason` 等私有方法/字段的直接赋值。

源码断言（`test_runtime_approval.TestNoopRepairPublicAPIOnly`）：`pipeline_tools.py`
不再以 `._xxx(` 形式调用上述私有方法。

---

## 3. 数据模型：`src/agent_runtime/policy.py`

全部 dataclass 带 `to_dict()`，可 JSON 序列化。

| 结构 | 字段 | 说明 |
|---|---|---|
| `PolicyAction` | ALLOW / ASK / DENY | str Enum |
| `PolicyRule` | rule_id / action / tool_names / risk_levels / priority | priority 按 action 自动赋值（DENY=3/ASK=2/ALLOW=1） |
| `PolicyConfig` | risk_defaults / rules / default_action | `default()` → read=ALLOW/write=ALLOW/guarded=ASK/未知=DENY；`with_overrides({tool:action})` 便捷工厂 |
| `PolicyDecision` | action / reason / rule_id / tool_name / risk_level | 一次决策结果 |
| `PendingApproval` | request_id(uuid4) / call_id / tool_name / arguments / fingerprint / run_id | ASK 暂停时创建 |
| `ApprovalResponse` | request_id / approved / note | 用户响应 |

`PolicyEngine.decide(tool_name, risk_level, *, run_id=None) -> PolicyDecision`：
完全确定性，不调用模型、不做 IO、不读墙钟。

---

## 4. 默认策略与优先级

默认策略（`PolicyConfig.default()`）：

| risk_level | 默认动作 |
|---|---|
| `read` | ALLOW |
| `workspace_write` | ALLOW |
| `guarded` | ASK |
| 未知 risk | DENY（兜底 `default_action`） |

决策优先级（`PolicyEngine.decide` 解析顺序）：

1. **工具级 DENY**（tool 级规则匹配且 action=DENY）
2. **工具级 ASK**
3. **工具级 ALLOW**
4. **risk 默认策略**（`risk_defaults`）
5. **默认 DENY**（`default_action`）

实现：收集所有匹配的 tool 级规则（`tool_names` 非空且含 `tool_name`，且
`risk_levels` 为空或含 `risk_level`），按 action 优先级（DENY>ASK>ALLOW）取
**第一个**返回；无 tool 级匹配 → `risk_defaults.get(risk_level)`；再无 →
`default_action`。

**PolicyEngine 完全确定性**：相同 `(tool_name, risk_level)` 永远得到相同决策；
不调用模型、不做 IO、不读墙钟。模型和用户文本**不能**自行修改策略或声明
"已授权"——策略只能由代码（`PolicyConfig`）在构造时确定。

---

## 5. Runtime 集成

`src/agent_runtime/runtime.py` 构造器增 `policy: PolicyEngine | None = None`
（None → 默认策略）。Runtime 重构为"模型轮 → 处理调用"两段，处理调用可中途暂停。

**执行工具前必须先调用 PolicyEngine**（每个已知工具的唯一授权入口）：

- **ALLOW**：记录 `policy_decision` 事件并执行 handler。
- **ASK**：**不执行 handler**；创建 `PendingApproval`（request_id=uuid4，
  fingerprint=tool name+arguments+run_id），存运行时状态，记录
  `approval_requested` + `runtime_stop{awaiting_approval}` 事件，暂停。
- **DENY**：**不执行 handler**；记录 `tool_call` + `tool_denied` 事件，构造
  `ToolResult.failure(status="denied", code="TOOL_DENIED_BY_POLICY", retryable=False)`，
  记录 `tool_result`，回填模型，让模型选择安全替代方案。

未知工具不过 policy，直接走 registry（返回 `UNKNOWN_TOOL`）。

新增事件（`EventType`）：`policy_decision` / `approval_requested` /
`approval_resolved` / `tool_denied`。

新增 stop_reason：`awaiting_approval`。`AgentRunResult` 增 `pending_approval`
字段（仅 `awaiting_approval` 时非 None）。

---

## 6. pause/resume 与防篡改、防重放设计

`AgentRuntime.resume(ApprovalResponse) -> AgentRunResult`：

**校验**（任一失败即拒绝，**保留 pending 不消费**，返回 `awaiting_approval`）：

| 校验 | 失败 outcome | 行为 |
|---|---|---|
| 当前必须有 pending | `no_pending` | 返回 `runtime_error`（防重放：二次 resume 无 pending） |
| `response.request_id == pending.request_id` | `rejected_wrong_id` | 保留 pending |
| `pending.run_id == context.run_id` | `rejected_cross_run` | 保留 pending（防跨 run） |
| `make_fingerprint(pending.tool_name, pending.arguments, pending.run_id) == pending.fingerprint` | `rejected_tampered` | 保留 pending（防参数篡改） |

**fingerprint** 至少包含 tool name + arguments + run_id（`make_fingerprint` 用
`json.dumps(sort_keys=True)`，参数顺序不影响指纹）。

**校验通过后一次性消费** pending（清空 `_pending`/`_paused_turn`/`_paused_index`）：

- `approved=True`：执行原 ToolCall **一次**，回填结果。
- `approved=False`：回填 `TOOL_REJECTED_BY_USER`（`retryable=False`），**不执行**。

然后从暂停位置 +1 继续处理同一轮剩余 ToolCall，再继续 Agent 循环。

**resume 不重置** `max_tool_turns` 与重复调用检测状态（`_tool_turns` 与
`_prev_calls_fp` 只在 `_do_model_turn` 新模型轮更新）。

---

## 7. 多 ToolCall 恢复方式

若一个 AssistantTurn 有多个 ToolCall：

- **按顺序执行**（不并行）。
- 遇到第一个 ASK 时**暂停**，保存 `_paused_turn`（整轮）与 `_paused_index`
  （当前 call 索引）。
- resume 后从 `_paused_index + 1` **继续**处理剩余调用。
- **不丢失、不重复**：整轮的 assistant 消息（含全部 tool_calls）在
  `_do_model_turn` 一次性追加；`_process_calls`/`resume` 只追加 tool 结果。
  暂停时消息序列是 `assistant[c1,c2,c3] + tool(c1)`，resume 后续追加
  `tool(c2)` / `tool(c3)`。

DENY 不中断后续调用：c1(READ)→c2(GUARDED,DENY)→c3(READ)，c2 回填拒绝后
c3 照常执行。

---

## 8. awaiting_approval vs requires_user_action

两者**不混淆**：

| 维度 | `awaiting_approval` | `requires_user_action` |
|---|---|---|
| 时机 | 工具**执行前** | 工具**执行后** |
| handler | 未执行 | 已执行 |
| pending_approval | 非 None | None |
| 触发 | PolicyEngine ASK | ToolResult.requires_user_action=True（如 manual_review_required） |
| 恢复 | `resume(ApprovalResponse)` | 人工处理后重新 `run()` |

---

## 9. 审批不绕过金融安全门

审批**只决定"是否执行"**，执行仍走 ToolRegistry → PipelineRunner → Remediation
Agent，内部安全门照常生效：

- `max_repair_rounds`（默认 3）
- `max_row_loss_ratio`（默认 5% 累计删行）
- `no_progress` / `manual_review_required` / `unresolved_checks`
- label 泄漏保护（`label_next_5d` 永不进 `approved_feature_columns`）

测试 `test_approval_does_not_bypass_safety_gate`：注入 1 行 close 缺失
（14% > 5% 门），批准 `run_safe_remediation` 后执行仍触发
`manual_review_required` + `requires_user_action`，未被绕过。

---

## 10. 本阶段不接入真实 LLM

明确声明：

- 本阶段**没有**接入任何真实模型。
- 本阶段**没有**添加 API Key 或读取环境变量凭据。
- 本阶段**没有**实现网络模型调用。
- PolicyEngine 完全确定性，不调用模型。
- 当前使用 Fake Model（`ScriptedFakeModel`）验证审批暂停/恢复、防篡改/防重放、
  多 ToolCall 恢复、安全门不被绕过。

---

## 11. 下一阶段计划

本阶段完成后停止，等待用户确认，再进入：

- **session 持久化**：把 `AgentRunResult` + 事件流 + pending approval 持久化到
  run_root，跨进程恢复。
- **真实模型适配器**：实现 `ModelClient` 的真实 LLM 适配器（仍受 PolicyEngine 约束）。
- **`chat_agent.py`**：自然语言 CLI 入口。

---

## 12. 新增 / 修改的文件

新增：

```
src/agent_runtime/policy.py            # PolicyEngine + 审批数据模型
tests/test_policy_engine.py            # 16 项策略测试
tests/test_runtime_approval.py         # 18 项审批恢复测试
docs/stage10_policy_and_approval.md    # 本文件
```

修改：

```
src/pipeline_runner.py                 # 新增薄公开方法 run_noop_repair()；run_full_pipeline else 分支委托它
src/agent_tools/pipeline_tools.py     # not_needed 分支改调 run_noop_repair()，不再触碰私有方法
src/agent_runtime/models.py           # StopReason.AWAITING_APPROVAL；EventType policy/approval/tool_denied；AgentRunResult.pending_approval
src/agent_runtime/__init__.py         # re-export policy 模块
src/agent_runtime/runtime.py          # policy 集成 + resume() + 多 ToolCall 恢复
tests/test_agent_runtime.py            # _user_action_tool risk 改 READ（适配默认 guarded→ASK）
README.md                              # Stage 10 小节
CODE_STRUCTURE.md                      # Stage 10 结构说明
```

未修改：`test_data/real_market_sample/` 真实 fixture、所有现有 CLI、
Stage 9 的 68 项测试（行为不变）。
