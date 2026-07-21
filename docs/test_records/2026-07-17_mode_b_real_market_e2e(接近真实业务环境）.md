# 模式 B 真实行情端到端测试记录（2026-07-17）

## 1. 测试概览

| 字段 | 记录 |
|---|---|
| 测试编号 | `NL-B-REAL-20260717-001` |
| 测试类型 | 自然语言 Agent 模式 B、真实网络数据、端到端验收 |
| 执行日期 | 2026-07-17 |
| 执行环境 | Windows PowerShell |
| 执行目录 | `D:\claude\dwzq\financial_table_workflow_agent_v3` |
| Run ID | `run_7faaf3a9` |
| 最终状态 | **通过但有警告（PASS WITH WARNINGS）** |
| Python 版本 | 原始记录未采集 |
| LLM provider / model | 原始记录未采集 |
| Git commit | 原始记录未采集 |

> 本文件整理自测试人员提供的原始 PowerShell 输出、`fetch_metadata.json`、最终报告和
> 一页摘要。本次只做 Markdown 归档，没有重新发起网络请求或 LLM 调用。原始记录中
> 没有出现 API Key、Token 或其他凭据。

## 2. 测试目标

验证自然语言 Agent 在不提供 `--input_dir` 的模式 B 下，能否完成以下闭环：

1. 从中文请求中提取股票代码、日期范围和基本面快照选项。
2. 经审批调用项目内置数据源，抓取两只 A 股的真实行情。
3. 自动完成 configure → profile → plan → prepare → validate。
4. 经安全审批进入 remediation；初始校验未失败时正确返回 `not_needed`。
5. 完成复审并生成中文最终报告。
6. 保证 `label_next_5d` 不进入 approved features，且不存在未来函数或标签泄漏。

## 3. 测试输入

### 3.1 自然语言需求

> 获取贵州茅台600519和平安银行000001从2026年1月1日至2026年7月15日的真实市场
> 数据，不使用当前基本面快照，生成用于五日收益率研究的建模宽表，检查未来函数和
> 标签泄漏，必要时安全修复，最后生成完整中文报告。

### 3.2 执行命令

```powershell
cd D:\claude\dwzq\financial_table_workflow_agent_v3
$env:PYTHONIOENCODING = "utf-8"

python -B src/chat_agent.py `
  --output_base outputs_agent `
  --max_tool_turns 20 `
  --prompt "获取贵州茅台600519和平安银行000001从2026年1月1日至2026年7月15日的真实市场数据，不使用当前基本面快照，生成用于五日收益率研究的建模宽表，检查未来函数和标签泄漏，必要时安全修复，最后生成完整中文报告。" `
  --auto_approve_data_fetch `
  --auto_approve_remediation
```

### 3.3 Agent 解析后的抓取参数

```json
{
  "tickers": ["600519", "000001"],
  "start_date": "2026-01-01",
  "end_date": "2026-07-15",
  "snapshot_fundamentals": false
}
```

解析结果与用户请求一致。

## 4. 验收标准

| 编号 | 验收项 | 预期 |
|---|---|---|
| AC-01 | 真实数据抓取 | 2 个 ticker 均成功，`errors` 为空 |
| AC-02 | 输入表产出 | 生成 price、volume、fundamentals、industry、calendar 五张表 |
| AC-03 | Pipeline 完整性 | profile、plan、prepare、validate、remediation、re-validate、report 均被调用 |
| AC-04 | 宽表质量 | 主键唯一，关键价格字段无缺失，宽表非空 |
| AC-05 | 标签隔离 | `label_next_5d` 不在 `approved_feature_columns` 中 |
| AC-06 | 时间安全 | 无未来函数、无标签泄漏 |
| AC-07 | 报告产出 | 生成完整中文报告、一页摘要、JSON 摘要和产物索引 |
| AC-08 | 基本面边界 | 不使用当前快照；fundamentals 为空时明确警告而非伪造历史值 |

## 5. 执行过程记录

关键终端事件如下。两个 guarded 工具都先产生审批请求，再由对应的自动审批参数批准；
自动审批没有绕过 PolicyEngine 的 ASK 流程。

```text
[approval] requested: fetch_real_market_data
[stop] awaiting_approval
(auto-approved via --auto_approve_data_fetch)
[tool] fetch_real_market_data       ... completed
[tool] configure_workflow           ... configured
[tool] profile_financial_data       ... completed
[tool] create_workflow_plan         ... completed
[tool] prepare_financial_panel      ... completed
[tool] validate_financial_panel     ... completed
[tool] inspect_validation_failures  ... ok
[approval] requested: run_safe_remediation
[stop] awaiting_approval
(auto-approved via --auto_approve_remediation)
[tool] run_safe_remediation         ... not_needed
[tool] validate_repaired_panel      ... completed
[tool] generate_workflow_report     ... completed
[stop] completed
```

原始记录显示 Agent 正常返回 PowerShell 提示符，但没有单独采集 `$LASTEXITCODE`，因此
本记录不补写未经采集的进程退出码。

## 6. 真实数据抓取结果

### 6.1 数据来源

| 指标 | 结果 |
|---|---|
| 数据提供器 | `project_internal_astock_http` |
| Adapter 版本 | `0.2` |
| Data source 版本 | `1.0` |
| 行情实际来源 | 两只股票均为 `sina_http_fallback` |
| 成交量单位 | `shares` |
| 抓取时间 | 2026-07-17 15:07:55 |
| 当前基本面快照 | 禁用 |
| 抓取错误 | 0 |

### 6.2 各股票行情行数

| 股票 | 代码 | 行数 |
|---|---:|---:|
| 贵州茅台 | 600519 | 127 |
| 平安银行 | 000001 | 127 |
| **合计** | — | **254** |

### 6.3 五张输入表

| 文件 | 行数 | 列数/说明 |
|---|---:|---|
| `price.csv` | 254 | OHLC 行情 |
| `volume.csv` | 254 | 成交量；单位为 shares |
| `fundamentals.csv` | 0 | 仅表头；按请求禁用当前快照 |
| `industry.csv` | 2 | 每只股票一行 |
| `calendar.csv` | 196 | 请求区间内的日历记录 |

`fetch_metadata.json` 中 `per_ticker_errors={}`、`errors=[]`。唯一抓取级警告为：

```text
snapshot_fundamentals=False: fundamentals.csv is header-only by request
(--no_snapshot_fundamentals).
```

该警告符合用户明确要求。系统没有把当前 PE/PB/ROE 快照回填到历史日期，避免制造
`announce_date` 和引入 look-ahead bias。

## 7. Pipeline 结果

| 阶段 | 状态 | 关键结果 |
|---|---|---|
| Stage 0：真实数据抓取 | 完成 | 2/2 ticker 成功，0 个抓取错误 |
| Stage 1：数据剖析 | 完成 | 5 张输入表，识别 4 类 profile 问题 |
| Stage 2：工作流规划 | 完成 | 13 个步骤、12 项校验、8 个特征、1 个标签 |
| Stage 3：生成宽表 | 完成 | **254 行 × 22 列**，主键唯一 |
| Stage 4：初始校验 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 5：安全修复 | `not_needed` | 0 轮修复、0 行删除 |
| Stage 6：复审 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 7：最终报告 | 完成 | 完整报告及一页摘要已生成 |

宽表覆盖 2 个 ticker，实际交易日期范围为 2026-01-05 至 2026-07-15。请求开始日期
2026-01-01 不是有效行情日期，系统没有补造非交易日行情。

## 8. 质量与安全检查

| 检查项 | 结果 | 判定 |
|---|---|---|
| 宽表规模 | 254 行 × 22 列 | 通过 |
| 主键唯一性 | `True` | 通过 |
| `close` 缺失率 | 0 | 通过 |
| 初始失败项 | 0 | 通过 |
| 复审失败项 | 0 | 通过 |
| 删除行数 | 0 | 通过 |
| 修复轮数 | 0 | 符合预期 |
| `label_next_5d` 在 approved features 中 | `False` | 通过 |
| `label_not_in_approved_features` | `True` | 通过 |
| 未来函数检查 | 未发现 | 通过 |
| 标签泄漏检查 | 未发现 | 通过 |
| 人工复核要求 | `false` | 通过 |

批准的 8 个特征为：

```text
return_1d, return_5d, volatility_20d, turnover_20d,
pe, pb, roe, industry_name
```

标签为 `label_next_5d`，其角色是 label，未进入上述特征列表。

## 9. 警告与限制

本次最终状态不是无条件的 `passed`，而是 `passed_with_warnings`。主要原因是用户禁用
当前基本面快照后，`pe`、`pb`、`roe` 缺失率为 100%。这是预期的数据可用性边界，
不是未来函数或标签泄漏失败。

- 当前数据源不提供历史 point-in-time PE/PB/ROE，本项目不会把当前快照伪装成历史值。
- 本流程只准备建模数据，没有训练模型，也没有验证任何预测收益。
- 本记录不构成选股、择时、交易或投资建议。
- 行情来自公开 HTTP 数据接口，不是券商成交或交易系统数据。

## 10. 运行产物

原始运行根目录：

```text
D:/claude/dwzq/financial_table_workflow_agent_v3/outputs_agent/runs/run_7faaf3a9
```

| 产物 | Run root 下的相对路径 |
|---|---|
| 抓取元数据 | `raw_data/fetch_metadata.json` |
| 建模宽表 | `prepared/prepared_panel.csv` |
| 修复后宽表 | `repaired/repaired_panel.csv` |
| 数据字典 | `prepared/data_dictionary.json` |
| 复审批准特征 | `validation_repaired/approved_feature_columns.json` |
| 完整中文报告 | `final_report/final_workflow_report.md` |
| 一页摘要 | `final_report/final_workflow_one_page.md` |
| 最终摘要 | `final_report/final_workflow_summary.json` |
| 产物索引 | `final_report/pipeline_artifacts_index.json` |

`outputs_agent/` 是运行时目录，不提交 Git。本记录保存可审计摘要和关键指标，但不能
替代原始产物归档。如果需要严格复现实验，应同时记录 Git commit、Python 版本、LLM
provider/model、`$LASTEXITCODE`，并对关键 JSON/CSV/Markdown 产物计算 SHA-256。

## 11. 验收结论

**结论：通过但有警告（PASS WITH WARNINGS）。**

贵州茅台与平安银行 2026-01-01 至 2026-07-15 的真实行情成功加工为
**254 行 × 22 列**的 analysis-ready 建模宽表。`label_next_5d` 已安全隔离，初始校验
与复审均为 0 个失败项，未发现未来函数或标签泄漏；唯一主要警告来自按请求禁用基本面
快照造成的 `pe/pb/roe` 全缺失。该产物可作为五日收益率研究的数据准备输入，但不代表
模型有效性、预测效果或投资建议。

