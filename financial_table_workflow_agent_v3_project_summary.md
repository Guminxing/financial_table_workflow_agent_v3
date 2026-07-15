# Financial Table Workflow Agent v3 项目总结

## 1. 项目目标

本项目面向真实金融表格数据，自动完成以下工作流：

```text
数据剖析
→ 工作流规划
→ 宽表生成
→ 有效性审查
→ 安全修复
→ 重新审查
→ 最终报告
```

最终目标是让用户不再手动输入固定命令，而是通过自然语言描述任务，由 Agent 自主选择并调用合适的金融数据处理工具。

## 2. 当前实现结果

项目已经从固定命令式 Shell 演进为可运行的自然语言 Tool-calling Agent Demo。

整体调用链如下：

```text
用户自然语言
→ OpenAI-compatible LLM
→ AgentRuntime
→ PolicyEngine
→ ToolRegistry
→ PipelineRunner 领域工具
→ ToolResult
→ LLM 继续决策或生成最终回答
```

模型负责理解用户目标和选择工具；金融计算、数据校验、修复策略及安全限制仍由确定性 Python 代码执行。

## 3. 主要能力

### 3.1 真实金融数据工作流

项目支持真实 A 股数据的：

- 数据画像和异常检测；
- Workflow Plan 生成；
- Analysis-ready 宽表构建；
- 标签泄漏和未来函数检查；
- 财务数据时间对齐检查；
- 有界多轮自动修复；
- 修复后重新审查；
- 最终报告生成。

项目不再使用或静默生成合成数据。输入数据不存在或不完整时会明确失败。

### 3.2 Agent Runtime

项目实现了通用的 Agent Runtime 基础组件和运行机制：

- `ModelClient`
- `ToolCall`
- `ToolResult`
- `ToolSpec`
- `ToolRegistry`
- `AgentEvent`
- 有界 Tool-calling 循环
- 最大工具轮数
- 重复调用检测
- 结构化错误反馈
- 每次运行的 `run_id` 产物隔离

### 3.3 权限与审批

项目实现了确定性的 `PolicyEngine`：

- `read`：自动允许；
- `workspace_write`：允许写入当前 run 目录；
- `guarded`：执行前请求用户批准；
- `deny`：拒绝执行，并将原因反馈给模型。

`run_safe_remediation` 默认需要批准。批准只代表允许执行该工具，不会绕过修复轮数、删行比例和标签泄漏等金融安全门。

### 3.4 自然语言 CLI

项目新增了 `src/chat_agent.py`，用户可以直接输入：

```powershell
python -B src/chat_agent.py `
  --input_dir test_data/real_market_sample `
  --output_base outputs_real `
  --prompt "检查这些真实市场数据，生成建模宽表，必要时安全修复并输出报告"
```

Agent 会根据当前状态自主选择以下工具：

```text
configure_workflow
→ profile_financial_data
→ create_workflow_plan
→ prepare_financial_panel
→ validate_financial_panel
→ run_safe_remediation
→ validate_repaired_panel
→ generate_workflow_report
```

### 3.5 模型接口

项目实现了 `OpenAICompatibleModelClient`，支持标准 Chat Completions Tool Calling，包括：

- `ToolSpec` 到 provider function schema 的转换；
- Runtime messages 到 provider messages 的转换；
- 单个或多个 tool calls；
- tool arguments JSON 解析；
- HTTP 错误、超时和非法响应处理；
- API Key 环境变量读取和错误信息脱敏。

## 4. 测试结果

运行命令：

```powershell
python -B -m unittest discover -s tests -v
```

测试结果：

```text
Ran 145 tests
OK
```

测试覆盖：

- 金融数据处理和修复闭环；
- `run_id` 运行隔离；
- `ToolRegistry` 和参数校验；
- `AgentRuntime`；
- allow/ask/deny 权限决策；
- 审批暂停与恢复；
- 多 ToolCall；
- OpenAI-compatible 协议转换；
- 自然语言 CLI；
- 模型错误和 HTTP 异常；
- API Key 脱敏；
- 原有 CLI 兼容性。

## 5. 项目特点

本项目采用“LLM 决策 + 确定性执行”的架构：

| 组件 | 职责 |
| --- | --- |
| LLM | 理解目标、选择工具、解释结果 |
| Python 工作流 | 金融计算、数据处理、有效性检查 |
| PolicyEngine | 控制是否允许工具执行 |
| Remediation Agent | 有界修复、复审和自主停止 |

这种方式既体现了 Agent 的自主工具选择能力，也保留了金融数据处理的可复现性和可审计性。

## 6. 当前边界

目前项目定位为可演示的 Agent 原型，而不是生产系统。当前限制包括：

- 只支持 OpenAI-compatible Chat Completions 接口；
- session 只保存在当前进程中；
- 尚未实现跨进程恢复；
- 未实现 MCP、插件系统和多 Agent；
- 不提供投资建议；
- 不允许模型直接执行任意 Shell 或 Python；
- API Key 由环境变量提供，不进入仓库。

## 7. 后续可扩展方向

后续可以继续研究：

- Agent session 持久化；
- 多轮对话恢复；
- MCP 金融数据源；
- LLM Planner 与受限 Operator Registry；
- 多 Agent Planner/Critic 协作；
- 可视化 Web 界面；
- 更完整的权限和审计机制。

当前版本已经完成自然语言输入、模型自主选择工具、金融工作流执行、修复闭环和最终报告输出，可以作为本阶段项目成果提交与演示。
