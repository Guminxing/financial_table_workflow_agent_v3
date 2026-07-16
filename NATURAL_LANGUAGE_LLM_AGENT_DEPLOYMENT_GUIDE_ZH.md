# 自然语言 LLM Agent 部署与运行指南（中文）

> 本指南面向导师验收，从零开始讲解如何部署和运行 Financial Table Workflow Agent
> 的**自然语言 LLM Agent**（`src/chat_agent.py`）。按步骤操作即可独立完成部署与验收。
>
> 本指南**以当前代码实际实现为准**，不描述未实现的功能。文中命令均可在 PowerShell
> 中直接复制运行；PowerShell 续行使用反引号 `` ` ``。
>
> **安全声明**：本指南不含任何真实 API Key。API Key 只从环境变量读取，绝不写入
> 仓库、日志、事件或错误信息。

---

## 1. 文档目的与适用范围

本指南覆盖：

- 自然语言 LLM Agent 的两种工作模式（模式 A 处理已有 CSV、模式 B 自然语言抓取真实数据）。
- 从环境准备、依赖安装、LLM 与参考项目配置，到运行、验收、故障排查的完整流程。
- 确定性 Pipeline（`src/run_all.py`，无需 LLM）的中文报告验收流程。

本指南**不**覆盖：跨进程 session 持久化、MCP、多 Agent、插件系统、Web UI（均未实现）。

项目主目录：

```
D:\dwzq\financial_table_workflow_agent_v3
```

---

## 2. 系统概览

### 2.1 这是什么

一个面向 A 股金融表格的**自然语言数据准备 Agent**：用户用一句中文描述目标，
Agent 通过真实 LLM（OpenAI-compatible Chat Completions tool calling）自主选择并调用
11 个领域工具，把原始行情/成交/财务/行业/日历表格加工成一张 **analysis-ready 建模宽表**，
并产出中文最终报告。

调用链（来自 `src/chat_agent.py` 与 `src/agent_runtime/runtime.py`）：

```
用户自然语言
  → chat_agent.py
  → OpenAICompatibleModelClient（真实 LLM，OpenAI-compatible tool calling）
  → AgentRuntime（有界 tool-calling 循环 + 重复检测）
  → PolicyEngine（执行前 allow/ask/deny；guarded→ASK）
  → ToolRegistry → 11 个金融领域工具（含 fetch_real_market_data）
  → PipelineRunner（确定性金融数据处理）
  → ToolResult 回填模型上下文
  → 模型继续调用工具或输出最终中文总结
```

设计原则（来自 `prompts/financial_agent_system.md` 与源码注释）：

- **LLM 只负责理解意图、选择工具、决定下一步**；不直接执行金融计算。
- **金融计算由确定性 PipelineRunner 完成**；防未来函数、label 隔离、安全门均在其中。
- **Runtime 不直接调用 PipelineRunner**；只能通过 ToolRegistry 调用工具。
- **不输出投资建议**：不选股、不择时、不预测涨跌。

### 2.2 两种工作模式

| 模式 | 触发条件 | 流程 |
|---|---|---|
| **模式 A**（已有 CSV） | 传 `--input_dir` | `configure_workflow → profile → plan → prepare → validate → inspect failures → remediation → revalidate → report` |
| **模式 B**（自然语言抓取） | 不传 `--input_dir` | `fetch_real_market_data → configure_workflow → profile → plan → prepare → validate → inspect failures → remediation → revalidate → report` |

模式 B 下，Agent 从自然语言提取 tickers / start_date / end_date，先抓取真实数据到当前
run 的 `raw_data`，再走完整流程。两种模式互不破坏，模式 A 向后兼容。

### 2.3 11 个领域工具

来自 `src/agent_tools/pipeline_tools.py` 的 `build_default_registry_specs()`：

| 工具名 | 用途 | risk level |
|---|---|---|
| `fetch_real_market_data` | 模式 B：抓取真实 A 股数据到当前 run 的 raw_data | guarded |
| `configure_workflow` | 校验输入目录、创建当前 run 的 runner | workspace_write |
| `inspect_pipeline_status` | 只读：阶段状态、校验状态、修复轮数、标签安全 | read |
| `profile_financial_data` | Stage 1：剖析原始 CSV | workspace_write |
| `create_workflow_plan` | Stage 2：规划工作流 | workspace_write |
| `prepare_financial_panel` | Stage 3：生成 analysis-ready 宽表 | workspace_write |
| `validate_financial_panel` | Stage 4：初始有效性审查 | workspace_write |
| `run_safe_remediation` | Stage 5：有界多轮修复（guarded） | guarded |
| `validate_repaired_panel` | Stage 6：复审 | workspace_write |
| `generate_workflow_report` | Stage 7：生成最终报告 | workspace_write |
| `inspect_validation_failures` | 只读：失败项 / 警告 / 建议 | read |

---

## 3. 环境前提

### 3.1 操作系统与 Python

- **操作系统**：Windows 11（项目在 Windows 上开发；路径用 `pathlib`，兼容反斜杠）。
- **Python**：3.10 或更高（代码使用 `from __future__ import annotations` 等特性）。
  用 `python --version` 确认。

### 3.2 依赖

来自 `requirements.txt`，仅两个：

```
pandas>=1.5.0
requests>=2.32.0
```

无其他第三方依赖。`requests` 仅 LLM 适配器与真实数据抓取使用；确定性 Pipeline 只用
`pandas` 与标准库，离线可运行。

### 3.3 网络要求

- **模式 A**（已有 CSV）：无需网络。流水线处理本身离线可运行。
- **模式 B**（自然语言抓取）：需要网络访问以下域名（来自 `docs/stage8_real_data_adapter.md`）：
  - `money.finance.sina.com.cn`（Sina K-line fallback）
  - `qt.gtimg.cn`（腾讯 PE/PB 快照，仅当 `snapshot_fundamentals=true`）
  - `push2.eastmoney.com`（东财行业字段）
  - mootdx TCP 7709（可选；未安装 mootdx 时自动走 Sina HTTP fallback）
- **LLM 调用**：需要能访问你配置的 OpenAI-compatible base URL。

> 若运行环境无法联网，必须明确标记"网络限制"，**不得生成合成数据冒充测试成功**。

### 3.4 外部参考项目（仅模式 B 需要）

模式 B 的真实数据抓取复用参考项目 `TradingAgents-astock-main`（只读依赖，**不修改**它）。
解析逻辑来自 `src/real_data_adapter.py` 的 `resolve_tradingagents_path`，校验
`tradingagents/dataflows/a_stock.py` 存在。

路径解析优先级（来自源码）：

1. CLI `--tradingagents_path` 显式传入
2. 环境变量 `TRADINGAGENTS_ASTOCK_PATH`
3. 默认路径 `D:\dwzq\TradingAgents-astock-main`
4. 相对路径 `..\TradingAgents-astock-main`

> 代码中不硬编码绝对路径；导师可在任意机器上用 `--tradingagents_path` 指定实际路径。
> **LLM 不能从自然语言中任意指定本地路径**，路径由 CLI/环境变量受控配置。

---

## 4. 获取项目与目录确认

### 4.1 进入项目主目录

```powershell
cd D:\dwzq\financial_table_workflow_agent_v3
```

### 4.2 确认关键目录与文件存在

```powershell
# 源码目录
Test-Path src\chat_agent.py
Test-Path src\run_all.py
Test-Path src\agent_tools\pipeline_tools.py
Test-Path src\agent_runtime\runtime.py

# 测试 fixture（小型真实 A 股数据，提交 Git）
Test-Path test_data\real_market_sample\price.csv

# system prompt
Test-Path prompts\financial_agent_system.md

# 依赖与配置占位符
Test-Path requirements.txt
Test-Path .env.example
```

以上均应返回 `True`。

### 4.3 目录结构速览

```
financial_table_workflow_agent_v3/
├── src/                  # 运行代码
│   ├── chat_agent.py     # 自然语言 Agent CLI（本指南主入口）
│   ├── run_all.py        # 确定性 Pipeline 一键入口（无需 LLM）
│   ├── run_fetch_real_data.py  # 真实数据抓取 CLI（可 --run_pipeline）
│   ├── real_data_adapter.py     # 真实 A 股数据适配器
│   ├── pipeline_runner.py       # 统一调度器 + Remediation Agent
│   ├── report_generator.py      # 中文最终报告生成器
│   ├── agent_runtime/           # Agent Runtime（models/context/registry/policy/runtime + 模型适配器）
│   └── agent_tools/            # 11 个领域工具
├── tests/                # 测试代码
├── test_data/real_market_sample/  # 小型真实 fixture（提交 Git）
├── prompts/              # system prompt
├── docs/                 # 分阶段设计文档
├── requirements.txt
└── .env.example          # 环境变量占位符（不含真实密钥）
```

---

## 5. 安装 Python 依赖

### 5.1（可选）创建虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> 若 PowerShell 报执行策略错误，可先执行：
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

### 5.2 安装依赖

```powershell
pip install -r requirements.txt
```

确认：

```powershell
python -c "import pandas, requests; print('pandas', pandas.__version__); print('requests', requests.__version__)"
```

---

## 6. 配置 LLM 环境变量

### 6.1 三个环境变量

来自 `.env.example` 与 `src/agent_runtime/openai_compatible_client.py`：

| 环境变量 | 用途 |
|---|---|
| `FTA_LLM_API_KEY` | API Key，**只**放进 HTTP `Authorization: Bearer` 头 |
| `FTA_LLM_BASE_URL` | OpenAI-compatible base URL（如 `https://api.openai.com/v1`） |
| `FTA_LLM_MODEL` | 模型名（**必须支持 tool calling**） |

### 6.2 在 PowerShell 中设置（当前会话有效）

```powershell
$env:FTA_LLM_API_KEY = "sk-your-api-key-here"
$env:FTA_LLM_BASE_URL = "https://api.openai.com/v1"
$env:FTA_LLM_MODEL = "gpt-4o-mini"
```

> 把 `sk-your-api-key-here` 替换为你自己的真实 Key。**不要把真实 Key 写入任何文件**。
> 项目**不自动读取 `.env`**；`.env.example` 只含占位符，`.env` 被 `.gitignore` 忽略。

### 6.3 API Key 安全规范（强制）

- API Key **只从环境变量读取**，不写入日志、事件或错误信息。
- `OpenAICompatibleModelClient._scrub` 兜底：若错误信息意外包含 Key，替换为 `***`。
- HTTP payload 不含 Key；Key 只出现在 `Authorization` 头。
- **不得**把真实 Key 提交到 Git；**不得**创建含真实密钥的 `.env` 并提交。
- 导师验收时，请用一次性/限额 Key，验收后撤销。

### 6.4 验证 LLM 配置是否就绪

```powershell
python -c "import os; print('API_KEY set:', bool(os.environ.get('FTA_LLM_API_KEY'))); print('BASE_URL:', os.environ.get('FTA_LLM_BASE_URL')); print('MODEL:', os.environ.get('FTA_LLM_MODEL'))"
```

三个字段均非空即配置就绪。若任一缺失，`chat_agent.py` 会以退出码 1 报错并提示
`model not configured`（见 `src/chat_agent.py` 的 `run_chat`）。

---

## 7. 配置 TradingAgents 参考项目路径（仅模式 B 需要）

若要运行模式 B（自然语言抓取真实数据），需让 Agent 能找到 `TradingAgents-astock-main`。

### 7.1 方式一：CLI 参数（推荐，最明确）

```powershell
--tradingagents_path D:\dwzq\TradingAgents-astock-main
```

### 7.2 方式二：环境变量

```powershell
$env:TRADINGAGENTS_ASTOCK_PATH = "D:\dwzq\TradingAgents-astock-main"
```

### 7.3 方式三：默认/相对路径

若不传 CLI 也不设环境变量，`resolve_tradingagents_path` 会依次尝试默认路径
`D:\dwzq\TradingAgents-astock-main` 与相对路径 `..\TradingAgents-astock-main`，
校验 `tradingagents/dataflows/a_stock.py` 存在。

### 7.4 验证参考项目可用

```powershell
# 假设参考项目在 D:\dwzq\TradingAgents-astock-main
Test-Path D:\dwzq\TradingAgents-astock-main\tradingagents\dataflows\a_stock.py
```

应返回 `True`。若为 `False`，模式 B 抓取会失败并报 `a_stock.py not found`。

---

## 8. 验证安装：运行测试套件

在正式运行 Agent 前，先跑测试套件确认环境与代码完整。

### 8.1 运行全量测试

```powershell
python -B -m unittest discover -s tests -v
```

> 若使用虚拟环境：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`

**预期**：所有测试通过，末尾输出 `OK`。测试**不访问真实网络、不依赖真实 LLM、
不修改提交的 fixture**（抓取测试用 `unittest.mock.patch.object` mock
`real_data_adapter.fetch_real_data`，见 `tests/test_fetch_tool.py` 与
`tests/test_chat_agent.py`）。

### 8.2 关键测试文件

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/test_fetch_tool.py` | `fetch_real_market_data` 注册、11 个工具、ticker/日期/数量校验、raw_data 隔离、产物不逃出 run_root、fetch 后更新 input_dir、全失败/部分失败、risk=guarded |
| `tests/test_chinese_report.py` | 中文报告标题、一页摘要标题、数值来自真实产物、summary.json 结构兼容、label 不进 approved、数据来源章节 |
| `tests/test_chat_agent.py` | CLI 参数、模式 A/B 链路、按工具名审批、fetch 默认 ASK、fetch 拒绝不执行、无 input_dir 时 PRECONDITION_NOT_MET |
| `tests/test_runtime_approval.py` | 审批暂停/恢复、防篡改/防跨 run/防重放、多 ToolCall 恢复、guarded 默认 ASK |
| `tests/test_pipeline_tools.py` | 11 个工具输入校验、不生成合成数据、stage 失败传递、label 不进 features |

### 8.3 单独运行某测试文件（可选）

```powershell
python -B -m unittest tests.test_fetch_tool -v
python -B -m unittest tests.test_chinese_report -v
python -B -m unittest tests.test_chat_agent -v
```

---

## 9. 模式 A 部署与运行：处理已有 CSV

模式 A 用提交的小型真实 fixture（`test_data/real_market_sample`，ticker 600519，
2024-01-01..2024-01-10），无需网络与参考项目。

### 9.1 运行命令

```powershell
python -B src/chat_agent.py `
  --input_dir test_data/real_market_sample `
  --output_base outputs_agent `
  --prompt "检查已有数据并生成中文报告" `
  --auto_approve_remediation
```

> 必须先按 §6 设置 `FTA_LLM_*` 环境变量，否则退出码 1。

### 9.2 预期终端输出（形态，非逐字）

```text
Financial Table Workflow Agent

Run: run_xxxxxxxx
Input: test_data/real_market_sample  (mode A: existing CSVs)

[user]
检查已有数据并生成中文报告

[tool] configure_workflow        ... configured
[tool] profile_financial_data    ... completed
[tool] create_workflow_plan      ... completed
[tool] prepare_financial_panel   ... completed
[tool] validate_financial_panel  ... completed
[tool] inspect_validation_failures ... ok
[tool] run_safe_remediation      ... not_needed
[tool] validate_repaired_panel   ... completed
[tool] generate_workflow_report  ... completed
[stop] completed

[assistant]
（模型生成的中文总结）
Run root: .../outputs_agent/runs/run_xxxxxxxx
Final report: .../final_report/final_workflow_report.md
```

> 进度行内容由模型实际选择决定；`[tool]` 行基于 `AgentEvent` 回调打印，不打印完整
> messages / 隐藏推理 / API Key。

### 9.3 退出码（来自 `src/chat_agent.py` 的 `run_chat`）

| 退出码 | 含义 |
|---|---|
| 0 | 模型返回 `final_text`，正常完成 |
| 1 | 配置/运行错误（如 LLM 未配置、input_dir 非法、runtime 异常） |
| 2 | 需要人工介入（`manual_review_required` / `requires_user_action` / 审批被拒绝后模型未完成） |

```powershell
echo $LASTEXITCODE
```

> PowerShell 中用 `$LASTEXITCODE` 查看上一条命令退出码。

---

## 10. 模式 B 部署与运行：自然语言抓取真实数据

模式 B 不传 `--input_dir`，由模型从自然语言提取参数并抓取真实数据。**需要网络与
参考项目可用**。

### 10.1 运行命令

```powershell
python -B src/chat_agent.py `
  --output_base outputs_agent `
  --tradingagents_path D:\dwzq\TradingAgents-astock-main `
  --max_tool_turns 20 `
  --prompt "获取贵州茅台600519和平安银行000001从2024年1月1日至2024年6月30日的真实市场数据，不使用当前基本面快照，生成用于五日收益率研究的建模宽表，检查未来函数和标签泄漏，必要时安全修复，最后生成完整中文报告。" `
  --auto_approve_data_fetch `
  --auto_approve_remediation
```

> 把 `--tradingagents_path` 换成你机器上参考项目的实际路径。

### 10.2 关键参数说明

| 参数 | 说明 |
|---|---|
| `--output_base` | 产物根；每次 run 隔离在 `<base>/runs/<run_id>/` |
| `--tradingagents_path` | 参考项目路径（见 §7） |
| `--max_tool_turns 20` | 模式 B 抓取链路较长，建议设 20（默认 12 可能不够） |
| `--prompt` | 自然语言请求；必须含 tickers 与日期，否则模型会要求补充 |
| `--auto_approve_data_fetch` | 只自动批准 `fetch_real_market_data` |
| `--auto_approve_remediation` | 只自动批准 `run_safe_remediation` |

### 10.3 模型如何提取参数

`fetch_real_market_data` 的输入 schema（来自 `src/agent_tools/pipeline_tools.py`）：

```json
{
  "tickers": {"type": "array", "items": {"type": "string"}},
  "start_date": {"type": "string"},
  "end_date": {"type": "string"},
  "snapshot_fundamentals": {"type": "boolean"}
}
```

system prompt（`prompts/financial_agent_system.md`）要求模型：

- 从自然语言提取 `tickers`（6 位数字，可带 SH/SZ/BJ 前缀或 .SH/.SZ/.BJ 后缀）、
  `start_date` / `end_date`（`YYYY-MM-DD`）。
- `snapshot_fundamentals` **默认 false**（当前 PE/PB/ROE 快照不是历史 point-in-time
  基本面，不能回填到历史日期）。
- **缺少关键参数时不猜测**，用最终文本要求用户补充。

### 10.4 工具层校验（代码实现，不依赖模型自觉）

来自 `_validate_fetch_tickers` / `_validate_fetch_date`：

- ticker 必须是 6 位数字（可带交易所前后缀）；非法格式返回 `INVALID_TOOL_ARGUMENTS`。
- 日期必须 `YYYY-MM-DD` 且是真实日历日期。
- `start_date <= end_date`，否则拒绝。
- 单次 ticker 数量上限 20（`MAX_FETCH_TICKERS`），防止模型意外发起超大抓取。
- `snapshot_fundamentals` 必须是布尔值。

### 10.5 抓取产物隔离

每次 run 的抓取数据写入（来自 `src/agent_tools/pipeline_tools.py` 与
`src/agent_runtime/context.py`）：

```
<output_base>/runs/<run_id>/raw_data/
├── price.csv
├── volume.csv
├── fundamentals.csv
├── industry.csv
├── calendar.csv
└── fetch_metadata.json
```

- 所有抓取产物经 `AgentContext.ensure_path_in_run_root` 路径边界检查，**禁止路径穿越，
  禁止写出 run_root**。
- **绝不覆盖 `data/real_market`**（只写当前 run 的 raw_data）。
- 抓取成功后 `AgentContext.set_input_dir(raw_data)` 把 raw_data 设为 `input_dir`
  （再次校验五张 CSV 齐全 + 路径边界）。
- 原始抓取 CSV 只读，后续 Pipeline 不得覆盖它们。

### 10.6 失败处理

- **全部 ticker 失败或 price.csv 为空**：返回结构化失败 `FETCH_NO_USABLE_DATA`，
  不更新 `input_dir`，不进入后续流程。
- **部分 ticker 失败**：保留成功结果，在 `warnings` / `errors` 中记录失败与成功 ticker，
  继续后续流程。

---

## 11. 确定性 Pipeline 验收（无需 LLM）

若只想验证中文报告与流水线闭环（不调 LLM、不抓取网络数据），用 `src/run_all.py`
+ 提交的 fixture。

### 11.1 运行命令

```powershell
python -B src/run_all.py `
  --input_dir test_data/real_market_sample `
  --output_root outputs_chinese_report_smoke
```

### 11.2 预期输出（形态）

```text
[run_all] Financial Table Workflow Agent

Input dir: test_data/real_market_sample
Output root: outputs_chinese_report_smoke
...

Stage 1 Data Profiler ................. completed
Stage 2 Workflow Planner .............. completed
Stage 3 Code Executor ................. completed
Stage 4 Validity Critic ............... completed
Stage 5 Repair Loop ................... skipped
Stage 6 Re-run Critic ................. skipped
Stage 7 Final Report .................. completed

Final status: passed_with_warnings
...
Final report: outputs_chinese_report_smoke/final_report/final_workflow_report.md
One-page summary: outputs_chinese_report_smoke/final_report/final_workflow_one_page.md
```

> fixture 的初始 Critic 状态为 `passed_with_warnings`（非 failed），故 Stage 5 跳过
> 实际修复，生成 no-op 修复产物（`no_repair_needed`），Stage 6 同步跳过。这是预期行为。

### 11.3 退出码（来自 `src/run_all.py` 的 `_compute_exit_code`）

| 退出码 | 含义 |
|---|---|
| 0 | 最终 validation 为 `passed` / `passed_with_warnings`，且不需要人工处理 |
| 1 | 阶段异常或必要产物失败（任一 stage status=failed） |
| 2 | 流水线正常执行，但最终 failed、blocked 或 manual_review_required |

```powershell
echo $LASTEXITCODE
```

---

## 12. 审批机制与安全门

### 12.1 PolicyEngine 默认策略

来自 `src/agent_runtime/policy.py` 的 `PolicyConfig.default()`：

| risk level | 默认动作 |
|---|---|
| `read` | ALLOW（自动允许） |
| `workspace_write` | ALLOW（允许写当前 run 目录） |
| `guarded` | ASK（执行前请求用户批准） |
| 未知 | DENY（拒绝） |

`fetch_real_market_data` 与 `run_safe_remediation` 均为 `guarded`，默认触发 ASK。

### 12.2 按工具名分别自动批准（Stage 12）

来自 `src/chat_agent.py` 的 `_should_auto_approve`：

| 工具 | 自动批准 flag | 说明 |
|---|---|---|
| `fetch_real_market_data` | `--auto_approve_data_fetch` | 只批准 fetch |
| `run_safe_remediation` | `--auto_approve_remediation` | 只批准 remediation |
| 其他 guarded | 无 | 交互式 `Approve? [y/N]` |

**两个 flag 互不越权**：`--auto_approve_remediation` 不会自动批准 fetch，
`--auto_approve_data_fetch` 不会自动批准 remediation。

### 12.3 交互式审批

不传对应 flag 时，终端会提示：

```text
Agent requests:
  fetch_real_market_data
  arguments: {'tickers': ['600519'], 'start_date': '2024-01-01', 'end_date': '2024-01-10'}
Approve? [y/N]
```

- 输入 `y` 或 `yes` → 批准执行。
- 其他或回车 → 拒绝；模型收到结构化拒绝 `TOOL_REJECTED_BY_USER`，不执行该工具。

### 12.4 审批的安全保障（保持不变）

来自 `src/agent_runtime/policy.py` 与 `runtime.py`：

- `PendingApproval` 含 `request_id` / `call_id` / `tool_name` / `arguments` /
  `fingerprint` / `run_id`。
- `resume(ApprovalResponse)` 校验：`request_id` 匹配、`run_id` 属于当前 run
  （防跨 run）、`fingerprint` 匹配（防参数篡改）、无 pending 时拒绝（防重放）。
- **审批只决定"是否执行"，执行仍走 PipelineRunner → Remediation Agent，不绕过
  删行阈值、轮数限制、标签泄漏保护等内部安全门**。

### 12.5 金融安全门（执行时约束，审批不绕过）

- `max_repair_rounds`（默认 3）：修复最大轮数。
- `max_row_loss_ratio`（默认 0.05，即 5%）：累计删行上限，超过转人工。
- `no_progress`：failed 集合 + panel 指纹连续不变 → 停止。
- `manual_review_required`：安全门超限或无法收敛 → `requires_user_action=True`，
  Runtime 停止自动循环。
- `label_next_5d` 永远不进入 `approved_feature_columns`；若检测到标签泄漏，返回
  `LABEL_LEAKAGE_DETECTED` 并转人工。

---

## 13. 输出产物结构与解读

### 13.1 run_root 结构

每次运行隔离在 `<output_base>/runs/<run_id>/` 下（`run_id` 由 `AgentContext`
规范化，路径穿越防护）。目录与文件名以 `pipeline_runner.py` 路径常量为准：

| 目录 | 关键产物 | 产生阶段 |
|---|---|---|
| `raw_data/` | 五张 CSV + `fetch_metadata.json`（仅模式 B） | fetch |
| `profiles/` | `profile.json` / `profile_report.md` | Stage 1 |
| `plans/` | `workflow_plan.json` / `workflow_plan_report.md` | Stage 2 |
| `prepared/` | `prepared_panel.csv` / `data_dictionary.json` / `execution_log.json` / `execution_report.md` | Stage 3 |
| `validation/` | `validation_report.json` / `validation_report.md` / `approved_feature_columns.json` | Stage 4 |
| `repaired/` | `repair_plan.json` / `repaired_panel.csv` / `repair_log.json` / `repair_report.md` / `repair_history.json` | Stage 5 |
| `validation_repaired/` | 复审 Critic 产物 | Stage 6 |
| `final_report/` | `final_workflow_summary.json` / `final_workflow_report.md` / `final_workflow_one_page.md` / `pipeline_artifacts_index.json` | Stage 7 |
| `sessions/` | `latest_session.json` / `session_*.json` | Stage 7 |

> `repair_history.json` 即使 blocked / failed / 异常也保存，保证审计文件始终存在。

### 13.2 关键产物解读

- `prepared_panel.csv` / `repaired_panel.csv`：analysis-ready 建模宽表（修复前/后）。
- `approved_feature_columns.json`：approved 特征列 + `label_column`；`label_next_5d`
  不在其中。
- `fetch_metadata.json`（模式 B）：可审计的抓取元数据（tickers、日期、来源、行数、
  基本面限制、warnings/errors）。
- `final_workflow_summary.json`：机器可读汇总（顶层含 `initial_validation_status` /
  `final_validation_status` / `rows_removed_by_repair` / `closed_loop_result` /
  `data_source_summary` 等）。
- `final_workflow_report.md` / `final_workflow_one_page.md`：中文人类可读报告。

### 13.3 查看产物

```powershell
# 列出本次 run 的产物
Get-ChildItem -Recurse outputs_agent\runs | Select-Object FullName

# 查看最终报告
Get-Content outputs_agent\runs\run_xxxxxxxx\final_report\final_workflow_report.md -Encoding utf8

# 解析 summary.json
python -c "import json; print(json.dumps(json.load(open('outputs_agent/runs/run_xxxxxxxx/final_report/final_workflow_summary.json', encoding='utf-8')), ensure_ascii=False, indent=2))"
```

---

## 14. 中文最终报告验收

### 14.1 验收要点

来自 `src/report_generator.py` 与 `tests/test_chinese_report.py`：

1. `final_workflow_report.md` 为中文（标题、执行摘要、各阶段说明、闭环、特征、
   标签泄漏、警告、局限、结论等）。
2. `final_workflow_one_page.md` 为中文（目标、数据来源、六个模块、闭环结果、
   为什么重要、局限性）。
3. `final_workflow_summary.json` 可正常解析，结构兼容（顶层关键字段齐全）。
4. `label_next_5d` 不在 `approved_feature_columns` 中。
5. `passed_with_warnings` 等机器状态值保留原文，显示为
   `passed_with_warnings（通过但有警告）`，不覆盖原始值。
6. 报告所有数值动态读取实际运行结果，不硬编码 fixture 行数/列数。
7. 报告含"数据来源与时间边界"章节：
   - 有 `fetch_metadata.json`（模式 B）时显示抓取来源（tickers、日期、来源、行数、
     基本面限制）。
   - 无 `fetch_metadata.json`（模式 A 用 fixture）时显示"本次使用用户提供的已有 CSV"，
     不编造外部来源。
8. 报告明确：当前 PE/PB/ROE 快照不是历史 point-in-time 基本面，不能回填到过去。

### 14.2 验收命令（确定性 Pipeline，无需 LLM）

```powershell
# 生成中文报告
python -B src/run_all.py `
  --input_dir test_data/real_market_sample `
  --output_root outputs_chinese_report_smoke

# 确认报告为中文（应输出 True）
python -c "p='outputs_chinese_report_smoke/final_report/final_workflow_report.md'; t=open(p,encoding='utf-8').read(); print('中文标题:', '金融表格 analysis-ready 工作流 — 最终报告' in t); print('数据来源章节:', '数据来源与时间边界' in t); print('用户已有CSV说明:', '用户提供的已有 CSV' in t)"

# 确认 summary.json 可解析 + label 不在 approved
python -c "import json; s=json.load(open('outputs_chinese_report_smoke/final_report/final_workflow_summary.json',encoding='utf-8')); print('可解析: True'); print('label_next_5d 不在 approved:', 'label_next_5d' not in s['approved_feature_columns']); print('source_kind:', s['data_source_summary']['source_kind'])"
```

> 用 fixture 跑 `run_all.py` 时，`data_source_summary.source_kind` 为
> `fetched_real_market_data`（因为 fixture 目录含 `fetch_metadata.json`）；
> 若 input_dir 无 `fetch_metadata.json`，则为 `user_provided_existing_csv`。

### 14.3 验收命令（自然语言 Agent，模式 A）

```powershell
python -B src/chat_agent.py `
  --input_dir test_data/real_market_sample `
  --output_base outputs_agent `
  --prompt "检查已有数据并生成中文报告" `
  --auto_approve_remediation
```

终端末尾会打印 `Run root:` 与 `Final report:` 路径，按路径打开
`final_workflow_report.md` 确认中文。

---

## 15. 常见问题与故障排查

### 15.1 `model not configured`（退出码 1）

**原因**：`FTA_LLM_API_KEY` / `FTA_LLM_BASE_URL` / `FTA_LLM_MODEL` 任一未设置。

**解决**：按 §6.2 在当前 PowerShell 会话设置环境变量后重试。注意环境变量仅当前
会话有效，新开窗口需重新设置。

### 15.2 `a_stock.py not found` / 抓取失败（模式 B）

**原因**：`--tradingagents_path` 指向的路径不含 `tradingagents/dataflows/a_stock.py`。

**解决**：按 §7 确认参考项目路径正确，或用 `--tradingagents_path` 显式传入。

### 15.3 `FETCH_NO_USABLE_DATA`（模式 B）

**原因**：全部 ticker 抓取失败或 `price.csv` 为空（网络不通、ticker 代码错误、
日期区间无交易日等）。

**解决**：检查网络（§3.3）、ticker 代码（6 位数字）、日期区间是否含交易日。
查看 `run_root/raw_data/fetch_metadata.json` 的 `errors` / `per_ticker_errors` 字段。

### 15.4 `PRECONDITION_NOT_MET`

**原因**：模式 B 下，`fetch_real_market_data` 成功前调用了 `profile` 等后续工具，
或 `configure_workflow` 在 `input_dir` 未配置时被调用。

**解决**：这是预期行为。system prompt 要求模型先 fetch 再 configure；若模型顺序错误，
工具会返回 `PRECONDITION_NOT_MET` 并建议先 `fetch_real_market_data`，模型应自行纠正。
若模型反复出错，增大 `--max_tool_turns` 或换更强的模型。

### 15.5 `max_tool_turns` 停止

**原因**：模型工具调用轮数达到上限（默认 12）。

**解决**：模式 B 建议设 `--max_tool_turns 20`。

### 15.6 `manual_review_required`（退出码 2）

**原因**：修复安全门超限（累计删行 > 5%）、`no_progress`、标签泄漏、空 panel 等。

**解决**：这是安全停止，不是 bug。查看 `run_root/repaired/repair_history.json` 与
`validation_repaired/validation_report.json`，按提示人工处理。可放宽
`--max_row_loss_ratio`（如 0.5）后重跑，但需理解其含义。

### 15.7 审批被拒绝后停止

**原因**：用户对 guarded 工具输入 `n`，模型未给出最终文本。

**解决**：这是预期行为。拒绝后模型收到 `TOOL_REJECTED_BY_USER`，应选择替代方案或
给出最终总结。退出码 2 表示需人工介入。

### 15.8 中文报告乱码

**原因**：用非 UTF-8 工具查看。

**解决**：用 `Get-Content -Encoding utf8` 或支持 UTF-8 的编辑器（VS Code）打开。
报告文件以 UTF-8 编码写入（`report_generator.py` 的 `write_text(full, encoding="utf-8")`）。

### 15.9 测试失败

**原因**：环境问题或代码被修改。

**解决**：先确认 `pip install -r requirements.txt` 成功；确认 Python 3.10+；
确认 `test_data/real_market_sample/` 未被修改（`git diff --stat test_data/` 应为空）。

---

## 16. 安全规范与边界

### 16.1 API Key 安全

- 只从环境变量读取，不写入仓库/日志/事件/错误信息。
- 不创建含真实密钥的 `.env` 并提交；`.env` 被 `.gitignore` 忽略。
- 导师验收用一次性/限额 Key，验收后撤销。

### 16.2 数据安全

- **不生成合成数据冒充真实行情**；输入缺失时明确失败，绝不静默回退。
- **不把当前基本面快照回填到历史日期**（防未来信息泄漏）。
- **不修改外部 TradingAgents-astock-main**（只读依赖）。
- **run_id 隔离 + 路径穿越防护**：`run_root` 严格位于 `output_base/runs/run_id`；
  工具只写当前 run_root，不覆盖原始输入 CSV。
- **label 隔离**：`label_next_5d` 永远不进入 `approved_feature_columns`。

### 16.3 不输出投资建议

- 不选股、不择时、不预测涨跌、不给买卖信号、不预测收益率。
- 只汇报数据准备状态、质量指标、产物路径与未解决问题。

### 16.4 已知限制（明确声明，不描述为已实现）

- 只支持 OpenAI-compatible Chat Completions 接口。
- session 只存在进程内；不实现跨进程持久化。
- 不实现 MCP / 多 Agent / 插件系统。
- 不是生产级安全加固：审批是进程内交互。
- 模式 B 真实抓取需网络与参考项目依赖；自动测试全部 mock，不访问真实网络。
- 当前 PE/PB/ROE 是快照，不是历史 point-in-time 基本面，不回填到历史日期。

---

## 17. 导师验收清单

按顺序执行以下步骤，全部通过即验收完成。

### 17.1 环境与安装

- [ ] Python 3.10+：`python --version`
- [ ] 依赖安装：`pip install -r requirements.txt`
- [ ] LLM 环境变量已设置（§6.4 验证）
- [ ] 参考项目路径可用（仅模式 B，§7.4 验证）

### 17.2 测试套件

- [ ] `python -B -m unittest discover -s tests -v` 末尾输出 `OK`
- [ ] `test_data/real_market_sample/` 未被修改：`git diff --stat test_data/` 为空

### 17.3 确定性 Pipeline 验收（无需 LLM）

- [ ] 运行 `python -B src/run_all.py --input_dir test_data/real_market_sample --output_root outputs_chinese_report_smoke`
- [ ] 退出码 0：`echo $LASTEXITCODE`
- [ ] `final_workflow_report.md` 为中文
- [ ] `final_workflow_one_page.md` 为中文
- [ ] `final_workflow_summary.json` 可正常解析
- [ ] `label_next_5d` 不在 approved features 中
- [ ] 报告含"数据来源与时间边界"章节

### 17.4 自然语言 Agent 验收（模式 A，需 LLM）

- [ ] 运行 §9.1 命令，退出码 0
- [ ] 终端打印 `Run root:` 与 `Final report:` 路径
- [ ] `final_workflow_report.md` 存在且为中文
- [ ] 模型最终回答为中文

### 17.5 自然语言 Agent 验收（模式 B，需 LLM + 网络 + 参考项目）

- [ ] 运行 §10.1 命令（替换 `--tradingagents_path` 为实际路径）
- [ ] `fetch_real_market_data` 被调用（终端出现 `[tool] fetch_real_market_data ... completed`）
- [ ] `run_root/raw_data/` 下含五张 CSV + `fetch_metadata.json`
- [ ] 退出码 0
- [ ] `final_workflow_report.md` 为中文，"数据来源与时间边界"章节显示抓取来源
  （tickers、日期、行数）

### 17.6 审批机制验收（可选）

- [ ] 不传 `--auto_approve_data_fetch` 运行模式 B，终端出现 `Approve? [y/N]`
- [ ] 输入 `n`，fetch 不执行，模型给出最终文本
- [ ] 输入 `y`，fetch 执行一次

### 17.7 清理

- [ ] 验收完成后撤销一次性 API Key
- [ ] 确认无真实 Key 被提交：`git log --all -p | findstr "sk-"`（应无真实 Key）

---

## 18. 附：可直接复制的完整命令

### 18.1 测试

```powershell
python -B -m unittest discover -s tests -v
```

### 18.2 确定性 Pipeline（无需 LLM，验证中文报告）

```powershell
python -B src/run_all.py `
  --input_dir test_data/real_market_sample `
  --output_root outputs_chinese_report_smoke
```

### 18.3 模式 A（已有 CSV，需 LLM）

```powershell
python -B src/chat_agent.py `
  --input_dir test_data/real_market_sample `
  --output_base outputs_agent `
  --prompt "检查已有数据并生成中文报告" `
  --auto_approve_remediation
```

### 18.4 模式 B（自然语言抓取，需 LLM + 网络 + 参考项目）

```powershell
python -B src/chat_agent.py `
  --output_base outputs_agent `
  --tradingagents_path D:\dwzq\TradingAgents-astock-main `
  --max_tool_turns 20 `
  --prompt "获取贵州茅台600519和平安银行000001从2024年1月1日至2024年6月30日的真实市场数据，不使用当前基本面快照，生成用于五日收益率研究的建模宽表，检查未来函数和标签泄漏，必要时安全修复，最后生成完整中文报告。" `
  --auto_approve_data_fetch `
  --auto_approve_remediation
```

> 运行前务必先设置 `FTA_LLM_*` 环境变量（§6.2）。把 `--tradingagents_path` 换成
> 你机器上参考项目的实际路径。

---

## 19. 参考文档

- `README.md` — 项目总览与快速开始
- `CODE_STRUCTURE.md` — 目录结构、模块职责、调用链
- `docs/stage8_real_data_adapter.md` — 真实数据适配器
- `docs/stage11_natural_language_demo.md` — 自然语言 Agent Demo
- `docs/stage12_natural_language_data_fetch_and_chinese_report.md` — 自然语言抓取 + 中文报告
- `prompts/financial_agent_system.md` — system prompt（双模式）
- `.env.example` — 环境变量占位符

---

> 本指南基于上述源码与文档的实际实现编写。如代码后续演进，以源码与
> `README.md` / `CODE_STRUCTURE.md` 为准。
