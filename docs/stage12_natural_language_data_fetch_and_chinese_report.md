# Stage 12 — 自然语言抓取真实数据 + 中文最终报告

> 本文档说明 Stage 12 的两项能力：
>
> 1. **自然语言抓取真实数据**：用户只发一个自然语言 Prompt，Agent 识别 A 股代码、
>    开始/结束日期，自主调用 `fetch_real_market_data` 抓取真实行情，再自动执行
>    fetch → configure → profile → plan → prepare → validate → remediation →
>    revalidate → report 全流程。
> 2. **固定 Markdown 最终报告中文化**：`final_workflow_report.md` 与
>    `final_workflow_one_page.md` 的用户可读正文改为中文，并新增"数据来源与时间边界"
>    章节。
>
> 本阶段保留现有"处理 `--input_dir` 中已有 CSV"的模式（模式 A），不破坏已有功能。

---

## 1. 两种工作模式

### 模式 A：处理已有 CSV

用户传 `--input_dir`，AgentContext 启动时即校验五张 CSV，直接走：

```
configure_workflow
  → profile_financial_data
  → create_workflow_plan
  → prepare_financial_panel
  → validate_financial_panel
  → inspect_validation_failures
  → run_safe_remediation
  → validate_repaired_panel
  → generate_workflow_report
```

### 模式 B：自然语言抓取真实数据

用户不传 `--input_dir`，Agent 从自然语言提取 tickers / start_date / end_date，
先抓取再走完整流程：

```
fetch_real_market_data
  → configure_workflow
  → profile_financial_data
  → create_workflow_plan
  → prepare_financial_panel
  → validate_financial_panel
  → inspect_validation_failures
  → run_safe_remediation
  → validate_repaired_panel
  → generate_workflow_report
```

模式 B 下，AgentContext 以"无 input_dir"状态启动；`fetch_real_market_data` 成功后
把当前 run 的 `raw_data` 设为 `input_dir`，`configure_workflow` 随后使用它创建
PipelineRunner。**绝不静默回退到 fixture 或合成数据**。

---

## 2. 新增工具：`fetch_real_market_data`

- **位置**：`src/agent_tools/pipeline_tools.py`（通过默认 ToolRegistry 注册）。
- **risk level**：`guarded`（涉及网络访问与工作区写入，默认 ASK 审批）。
- **不复制抓取实现**：直接复用 `src/real_data_adapter.py` 的 `RealDataFetchConfig` +
  `fetch_real_data`；不通过 subprocess 调 `run_fetch_real_data.py`。
- **不修改 TradingAgents-astock-main**；不生成合成数据；不把当前基本面快照回填到
  历史日期。

### 输入 JSON Schema

```json
{
  "tickers": {"type": "array", "items": {"type": "string"}},
  "start_date": {"type": "string"},
  "end_date": {"type": "string"},
  "snapshot_fundamentals": {"type": "boolean"}
}
```

> 注：registry 的基础 schema 校验不支持 `minItems`/`maxItems`/`minimum` 等高级关键字，
> 这些约束在工具 handler 内以代码实现。

### 工具职责

1. 从模型传入的结构化参数读取 tickers / start_date / end_date /
   snapshot_fundamentals（默认 `false`）。
2. 校验 A 股代码（6 位数字，可带 SH/SZ/BJ 前缀或 .SH/.SZ/.BJ 后缀）。
3. 校验日期格式 `YYYY-MM-DD`（含真实日历日期校验）。
4. 校验 `start_date <= end_date`。
5. 限制单次 ticker 数量（最多 20，`MAX_FETCH_TICKERS`），防止模型意外发起超大抓取。
6. 默认 `snapshot_fundamentals=false`。
7. 调用 `RealDataFetchConfig` + `fetch_real_data`。
8. 抓取产物写入当前 run 的 `run_root/raw_data/`（路径边界检查，禁止路径穿越，
   禁止写出 run_root；绝不覆盖 `data/real_market`）。
9. 全部股票失败或 `price.csv` 为空时返回结构化失败（`FETCH_NO_USABLE_DATA`）。
10. 部分股票失败时允许继续，但在 `warnings` / `errors` 中返回失败 ticker 与成功 ticker。
11. 抓取成功后把 `AgentContext.input_dir` 更新为该 run 的 `raw_data`。
12. 返回结构化 `ToolResult`，含 `requested_tickers` / `resolved_tickers` /
    `rows_by_ticker` / `summary_rows` / `warnings` / `errors` /
    `fetch_metadata.json` 路径 / 五张 CSV 路径 / `next_actions=["configure_workflow"]`。

---

## 3. 每个 run 隔离原始抓取数据

模式 B 下，每次 Agent run 的抓取数据写入：

```
<output_base>/runs/<run_id>/raw_data/
├── price.csv
├── volume.csv
├── fundamentals.csv
├── industry.csv
├── calendar.csv
└── fetch_metadata.json
```

- 所有抓取产物经 `AgentContext.ensure_path_in_run_root` / `ensure_artifact_in_run_root`
  路径边界检查，禁止路径穿越，也不能写出当前 `run_root`。
- 抓取成功后 `AgentContext.set_input_dir(raw_data)` 把当前 run 的 `raw_data` 设为
  `input_dir`（再次校验五张 CSV 齐全 + 路径边界）。
- `configure_workflow` 随后使用这个 `input_dir` 创建 PipelineRunner。
- 原始抓取 CSV 只读，后续 Pipeline 不得覆盖它们（PipelineRunner 只写 `run_root`
  下的派生产物）。

---

## 4. AgentContext 无 input_dir 启动状态

`src/agent_runtime/context.py` 重构（向后兼容）：

- 新增 `AgentContext.create_without_input_dir` 工厂：以 `input_dir=None` 启动，
  不校验五张 CSV；校验 run_id 与 run_root。
- 新增 `set_input_dir(input_dir)`：fetch 成功后设置 `input_dir`，校验五张 CSV 齐全
  + 路径边界（raw_data 必须位于 run_root 之下）。
- 新增 `has_input_dir()` / `ensure_path_in_run_root(path)`。
- `configure_runner` 在 `input_dir` 为 None 时抛 `RuntimeError`（由
  `configure_workflow` 工具转 `PRECONDITION_NOT_MET`）。
- 只有以下情况才校验五张 CSV：用户显式传 `--input_dir`、`configure_workflow` 准备
  创建 PipelineRunner、Pipeline 阶段开始运行。
- 没有 input_dir 时，`profile` / `plan` / `prepare` 等工具返回清晰的
  `PRECONDITION_NOT_MET`（建议先 `fetch_real_market_data` 或 `configure_workflow`）。
- 已有 `--input_dir` 模式继续正常工作；不允许缺少输入时静默回退到 fixture 或合成数据。

---

## 5. 真实数据抓取的审批

`fetch_real_market_data` 的 risk level 为 `guarded`，默认 PolicyEngine 行为为 `ASK`。

CLI 新增独立参数：

- `--auto_approve_data_fetch`：只自动批准 `fetch_real_market_data`。
- `--auto_approve_remediation`：只自动批准 `run_safe_remediation`（保持原行为）。

`chat_agent._handle_approval` 根据 `pending.tool_name` 决定是否自动批准
（`_should_auto_approve`）：

- `fetch_real_market_data` → 仅当 `--auto_approve_data_fetch` 时自动批准。
- `run_safe_remediation` → 仅当 `--auto_approve_remediation` 时自动批准。
- 其他 guarded 工具 → 不自动批准（交互式 `y/N`）。
- 两个 flag 互不越权：`--auto_approve_remediation` 不会自动批准 fetch，
  `--auto_approve_data_fetch` 不会自动批准 remediation。
- 拒绝抓取后模型收到结构化拒绝结果（`TOOL_REJECTED_BY_USER`）。
- approval 的防篡改、防跨 run、防重放机制（request_id / run_id / fingerprint 校验）
  保持不变。

---

## 6. TradingAgents 路径配置

复用 `real_data_adapter.resolve_tradingagents_path`。优先级：

1. `chat_agent.py` 新增可选参数 `--tradingagents_path`。
2. 环境变量 `TRADINGAGENTS_ASTOCK_PATH`。
3. 现有 resolver 默认路径与相对路径。

解析后的路径保存在 `AgentContext.tradingagents_path`，供 `fetch_real_market_data`
受控使用。**LLM 不能从自然语言中任意指定本地 TradingAgents 路径**，避免模型控制
文件系统路径。代码中不硬编码绝对路径。

---

## 7. System Prompt 更新

`prompts/financial_agent_system.md` 明确两种模式（A / B），并要求模型：

1. 从用户自然语言中提取 tickers / start_date / end_date。
2. 缺少关键参数时用最终文本要求用户补充，**不得猜测**。
3. 抓取成功前不得调用 `profile`。
4. `configure` 前必须已有有效 `input_dir`。
5. `validate` 后必须先 `inspect_validation_failures` 查看失败和警告。
6. 无论初始校验是否 failed，都必须调用 `run_safe_remediation`
   （failed 时修复；passed/passed_with_warnings 时生成 no-op 修复产物）。
7. 然后必须 `validate_repaired_panel`。
8. 最后才能 `generate_workflow_report`。
9. 不得在修复和复审产物未生成时提前生成最终报告。
10. 最终回答必须使用中文。
11. 不得编造抓取来源、行数、指标或路径。
12. 不输出投资建议。

---

## 8. 固定 Markdown 最终报告中文化

`src/report_generator.py` 修改，使 `final_workflow_report.md` 与
`final_workflow_one_page.md` 的用户可读正文改为中文，包括：

- 标题、元数据说明、执行摘要、工作流架构说明、各阶段说明、数据剖析结果、规划摘要、
  宽表生成结果、初始审查、修复过程、修复后复审、特征列表说明、标签泄漏说明、
  警告和未解决问题、局限性、最终结论、一页摘要全部内容。

要求：

1. JSON 字段名、机器状态值、文件名、工具名和代码标识保留英文。
2. 用户可读叙述必须是中文。
3. Mermaid 节点说明尽量中文化。
4. 不改变 JSON 产物结构（`final_workflow_summary.json` 字段保持兼容，仅新增
   `data_source_summary`）。
5. 不硬编码测试 fixture 的 7 行、22 列等数值；所有数值动态读取实际运行结果。
6. `passed_with_warnings` 等机器状态值保留原文，显示为
   `passed_with_warnings（通过但有警告）`，不覆盖原始值。
7. 不建议对历史基本面直接插值；报告明确：当前 PE/PB/ROE 快照不是历史
   point-in-time 基本面，不能回填到过去，否则会引入未来信息泄漏。

---

## 9. 数据来源章节

中文最终报告新增"数据来源与时间边界"章节。从 `fetch_metadata.json` 读取并展示：

- `requested_tickers` / `resolved_tickers`
- `start_date` / `end_date` / `fetch_date`
- `ohlcv_source_by_ticker`
- `rows_by_ticker` / `summary_rows`
- `snapshot_fundamentals_enabled`
- `fundamentals_limitation`
- `warnings` / `errors`

如果运行使用的是用户已有 CSV 且没有 `fetch_metadata.json`：

- 明确显示"本次使用用户提供的已有 CSV"。
- 不编造其外部来源。
- 列出 `input_dir` 与发现的 CSV 文件。

---

## 10. 运行命令

### 10.1 模式 A：处理已有 CSV

```powershell
python -B src/chat_agent.py `
  --input_dir test_data/real_market_sample `
  --output_base outputs_agent `
  --prompt "检查已有数据并生成中文报告" `
  --auto_approve_remediation
```

### 10.2 模式 B：自然语言自动抓取

```powershell
python -B src/chat_agent.py `
  --output_base outputs_agent `
  --tradingagents_path ..\TradingAgents-astock-main `
  --max_tool_turns 20 `
  --prompt "获取贵州茅台600519和平安银行000001从2024年1月1日至2024年6月30日的真实市场数据，不使用当前基本面快照，生成用于五日收益率研究的建模宽表，检查未来函数和标签泄漏，必要时安全修复，最后生成完整中文报告。" `
  --auto_approve_data_fetch `
  --auto_approve_remediation
```

> PowerShell 使用反引号续行。模式 B 需要网络与 TradingAgents 依赖可用；自动测试
> 全部 mock 网络，不访问真实网络。

### 10.3 确定性 Pipeline（无需 LLM，验证中文报告）

```powershell
python -B src/run_all.py `
  --input_dir test_data/real_market_sample `
  --output_root outputs_chinese_report_smoke
```

---

## 11. 测试

新增/更新测试（全部不访问网络、不依赖真实 LLM、不修改提交的 fixture）：

- `tests/test_fetch_tool.py`（28 项）：fetch 工具注册、11 个工具、ticker/日期/数量
  校验、默认禁用 snapshot、raw_data 隔离、产物不逃出 run_root、fetch 后更新
  input_dir、全失败结构化错误、部分失败保留成功 + warning、mock adapter、risk=guarded。
- `tests/test_chinese_report.py`（9 项）：中文报告标题、一页摘要标题、数值来自真实
  产物、summary.json 结构兼容、label 不进 approved、passed_with_warnings 中文显示、
  数据来源章节（有/无 fetch_metadata）。
- `tests/test_chat_agent.py`（21 项，新增 9 项）：`--input_dir` 可选、
  `--auto_approve_data_fetch` / `--tradingagents_path` 参数、`_should_auto_approve`
  按工具名分别授权、fetch 默认 ASK、模式 B 完整链路（mock fetch）、fetch 拒绝不执行、
  无 input_dir 时 profile/configure 返回 PRECONDITION_NOT_MET。
- `tests/test_pipeline_tools.py`：不 configure 直接 profile 现在返回
  `PRECONDITION_NOT_MET`（原 `TOOL_EXECUTION_ERROR`）。

全量测试：`python -B -m unittest discover -s tests -v` → 191 项全部通过
（原 145 + 新增 46）。

---

## 12. 验收

```powershell
# 1. 全量测试
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v

# 2. 无需网络的 fixture 验收（确认中文报告 + summary.json + label 不在 approved + fixture 未改）
.\.venv\Scripts\python.exe -B src\run_all.py `
  --input_dir test_data\real_market_sample `
  --output_root outputs_chinese_report_smoke
```

确认：

1. `final_workflow_report.md` 为中文。
2. `final_workflow_one_page.md` 为中文。
3. `final_workflow_summary.json` 可以正常解析。
4. `label_next_5d` 不在 approved features 中。
5. Git 仓库中的 fixture 未被修改。

---

## 13. 当前限制（明确声明）

- 模式 B 真实抓取需要网络与 TradingAgents-astock-main 依赖可用；自动测试全部 mock，
  不访问真实网络。
- 只支持 OpenAI-compatible Chat Completions 接口。
- session 只存在进程内；不实现跨进程持久化。
- 不实现 MCP / 多 Agent / 插件系统。
- 不是生产级安全加固：审批是进程内交互；API Key 由调用方负责保管。
- 当前 PE/PB/ROE 是快照，不是历史 point-in-time 基本面，不回填到历史日期。
- 不输出投资建议。

---

## 14. 新增 / 修改的文件

新增：

```
tests/test_fetch_tool.py                                       # 28 项 fetch 工具测试
tests/test_chinese_report.py                                   # 9 项中文报告测试
docs/stage12_natural_language_data_fetch_and_chinese_report.md # 本文件
```

修改：

```
src/agent_runtime/context.py          # 无 input_dir 启动 + set_input_dir + 路径边界
src/agent_tools/pipeline_tools.py     # 新增 fetch_real_market_data（guarded）+ 11 工具 + PRECONDITION_NOT_MET
src/chat_agent.py                    # --input_dir 可选 + --auto_approve_data_fetch + --tradingagents_path + 按工具名审批
src/report_generator.py              # 中文报告 + 数据来源章节 + fetch_metadata/input_dir 可选输入
src/pipeline_runner.py               # _final_report_impl 传 fetch_metadata/input_dir 给 ReportGenerator
src/real_data_adapter.py             # metadata 增加 snapshot_fundamentals_enabled 字段
prompts/financial_agent_system.md    # 双模式 system prompt
tests/test_chat_agent.py             # 模式 B 链路 + 按工具名审批测试
tests/test_pipeline_tools.py         # 不 configure 直接 profile → PRECONDITION_NOT_MET
README.md / CODE_STRUCTURE.md        # Stage 12 说明
docs/stage8_real_data_adapter.md     # Stage 12 抓取工具说明
docs/stage11_natural_language_demo.md # 模式 B 与中文报告说明
```

未修改：`test_data/real_market_sample/` 真实 fixture、外部
`TradingAgents-astock-main`、Stage 9–10 的核心 Runtime/Policy 逻辑（行为不变）。
