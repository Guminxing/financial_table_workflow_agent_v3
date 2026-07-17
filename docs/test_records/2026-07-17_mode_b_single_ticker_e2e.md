# 模式 B 单股票全年行情端到端测试记录（2026-07-17）

## 1. 测试概览

| 字段 | 记录 |
|---|---|
| 测试编号 | `NL-B-REAL-20260717-002` |
| 测试类型 | 自然语言 Agent 模式 B、单 ticker、全年真实行情、端到端验收 |
| 执行日期 | 2026-07-17 |
| 执行环境 | Windows PowerShell |
| 执行目录 | `D:\claude\dwzq\financial_table_workflow_agent_v3` |
| Run ID | `run_7d8415f0` |
| 进程退出码 | `0` |
| 最终状态 | **通过但有警告（PASS WITH WARNINGS）** |
| Python 版本 | 原始记录未采集 |
| LLM provider / model | 原始记录未采集 |
| Git commit | 原始记录未采集 |

> 本文件整理自测试人员提供的 PowerShell 输出、`fetch_metadata.json`、完整中文报告和
> 一页摘要。本次只归档已有证据，没有重新发起网络请求或 LLM 调用。记录中没有 API
> Key、Token 或其他凭据。

## 2. 测试目标

验证 Agent 能否根据中文任务，抓取宁德时代（300750）2025 年全年真实行情，并生成
面向未来五日收益率研究的 analysis-ready 宽表。重点覆盖：

1. 单 ticker、完整年度的真实行情抓取。
2. 一日收益率、五日收益率、二十日波动率等时序特征的按日期处理。
3. 未来函数与 `label_next_5d` 标签泄漏检查。
4. 行业接口失败时的降级和警告记录。
5. 初始校验通过时 remediation 正确返回 `not_needed`。
6. 完整中文报告和一页摘要生成。

## 3. 测试输入

### 3.1 自然语言需求

> 获取宁德时代300750从2025年1月1日至2025年12月31日的真实市场数据，不使用当前
> 基本面快照，生成用于未来五日收益率研究的建模宽表。重点检查一日收益率、五日
> 收益率、二十日波动率和成交量相关字段的计算是否按股票和日期正确排序，检查未来
> 函数与标签泄漏，必要时执行安全修复，最后生成完整中文报告。不训练模型，不输出
> 投资建议。

### 3.2 执行命令

```powershell
$prompt = "获取宁德时代300750从2025年1月1日至2025年12月31日的真实市场数据，不使用当前基本面快照，生成用于未来五日收益率研究的建模宽表。重点检查一日收益率、五日收益率、二十日波动率和成交量相关字段的计算是否按股票和日期正确排序，检查未来函数与标签泄漏，必要时执行安全修复，最后生成完整中文报告。不训练模型，不输出投资建议。"

python -B src/chat_agent.py `
  --output_base outputs_agent `
  --max_tool_turns 20 `
  --prompt $prompt `
  --auto_approve_data_fetch `
  --auto_approve_remediation
```

### 3.3 Agent 解析后的抓取参数

```json
{
  "tickers": ["300750"],
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "snapshot_fundamentals": false
}
```

解析结果与用户请求一致。

## 4. 验收标准

| 编号 | 验收项 | 预期 |
|---|---|---|
| AC-01 | 股票与日期解析 | ticker 为 300750，日期为 2025 全年 |
| AC-02 | 真实行情抓取 | `price` 和 `volume` 非空，ticker 无抓取错误 |
| AC-03 | Pipeline 完整性 | 从抓取到报告的完整工具链执行结束 |
| AC-04 | 时序数据质量 | `(ticker, date)` 主键唯一，日期升序，关键价格无缺失 |
| AC-05 | 标签隔离 | `label_next_5d` 不进入 approved features |
| AC-06 | 时间安全 | 不存在非标签的未来位移或历史基本面回填 |
| AC-07 | 降级能力 | 行业接口失败时记录 warning，OHLCV 流程不中断 |
| AC-08 | 输出边界 | 不训练模型，不输出投资建议 |

## 5. 执行过程记录

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
Exit code: 0
```

两个 guarded 工具都先进入 ASK/awaiting approval，再由各自对应的 CLI 参数自动批准。
初始 Critic 没有失败项，因此 remediation 没有修改数据。

## 6. 真实数据抓取结果

### 6.1 数据来源

| 指标 | 结果 |
|---|---|
| 请求 / 解析 ticker | `300750` / `300750` |
| 请求日期 | 2025-01-01 至 2025-12-31 |
| 数据提供器 | `project_internal_astock_http` |
| Adapter / Data source 版本 | `0.2` / `1.0` |
| OHLCV 实际来源 | `sina_http_fallback` |
| 成交量单位 | `shares` |
| 抓取时间 | 2026-07-17 16:11:44 |
| 当前基本面快照 | 禁用 |
| ticker 抓取错误 | 0 |

### 6.2 五张输入表

| 文件 | 行数 | 说明 |
|---|---:|---|
| `price.csv` | 243 | 宁德时代 2025 年交易日 OHLC |
| `volume.csv` | 243 | 成交量；单位为 shares |
| `fundamentals.csv` | 0 | 仅表头；按请求禁用当前快照 |
| `industry.csv` | 1 | 行业接口失败，值降级为 `unknown` |
| `calendar.csv` | 365 | 2025 全年自然日历及交易日标记 |

### 6.3 抓取级警告

本次 `errors=[]`，但 metadata 记录了两类 warning：

1. `snapshot_fundamentals=False`，因此 `fundamentals.csv` 只有表头。
2. 东方财富行业接口发生代理连接错误，未获得真实行业分类；`industry_name` 设置为
   `unknown`，OHLCV 抓取和后续 Pipeline 不受影响。

这属于显式降级：系统没有猜测或伪造行业分类。

## 7. Pipeline 结果

| 阶段 | 状态 | 关键结果 |
|---|---|---|
| Stage 0：真实数据抓取 | 完成 | 1/1 ticker 成功，243 行行情 |
| Stage 1：数据剖析 | 完成 | 5 张输入表，4 个 profile issue |
| Stage 2：工作流规划 | 完成 | 13 个步骤、12 项校验、8 个特征、1 个标签 |
| Stage 3：生成宽表 | 完成 | **243 行 × 22 列**，主键唯一 |
| Stage 4：初始校验 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 5：安全修复 | `not_needed` | 0 轮修复、0 行删除 |
| Stage 6：复审 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 7：最终报告 | 完成 | 完整报告和一页摘要已生成 |

宽表覆盖 1 个 ticker，实际交易日期范围为 2025-01-02 至 2025-12-31。请求起始日
2025-01-01 没有被伪造成交易日行情。

## 8. 时序特征与安全检查

| 检查项 | 结果 | 判定 |
|---|---|---|
| 宽表规模 | 243 行 × 22 列 | 通过 |
| `(ticker, date)` 主键 | 唯一 | 通过 |
| `close` 缺失率 | 0 | 通过 |
| 初始 / 复审失败项 | 0 / 0 | 通过 |
| 数据删除 | 0 行 | 通过 |
| 修复轮数 | 0 | 符合预期 |
| `label_next_5d` 在 approved features 中 | `False` | 通过 |
| `label_not_in_approved_features` | `True` | 通过 |
| 未来函数检查 | 未发现违规 | 通过 |
| 标签泄漏检查 | 未发现 | 通过 |
| 人工复核要求 | `false` | 通过 |

批准的特征为：

```text
return_1d, return_5d, volatility_20d, turnover_20d,
pe, pb, roe, industry_name
```

原始报告说明 rolling / pct_change 按 ticker 分组并按日期处理；本例只有一个 ticker。
Agent 总结将一日收益率、五日收益率、二十日波动率和成交量相关字段的排序判为通过。
原始证据没有附这些列的逐行抽样或独立数值重算，因此本记录不把该结论扩大为独立的
数值复核。特别是原始日志没有单独报告 `turnover_20d` 的非空率。

## 9. Critic 警告与限制

Critic 的唯一 warning 是 `missing_rate_after_join`：`close` 缺失率为 0%，但因基本面
快照被禁用，`pe/pb/roe` 缺失率为 100%；行业接口失败也使 `industry_name` 不可用。
这些问题不会造成未来函数或标签泄漏，但会限制估值及行业特征的可用性。

- 如需估值因子，应接入历史 point-in-time 财务数据，不能回填当前快照。
- 本流程生成研究数据输入，不训练或评估预测模型。
- 本结果不构成选股、择时、交易或投资建议。
- 行情来自公开 HTTP 数据接口，不是券商成交或交易系统数据。

## 10. 运行产物

原始运行根目录：

```text
D:/claude/dwzq/financial_table_workflow_agent_v3/outputs_agent/runs/run_7d8415f0
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

`outputs_agent/` 不提交 Git。本记录保留关键结果，但不能替代原始产物归档。后续测试应
同时采集 Git commit、Python 版本、LLM provider/model，并对关键产物计算 SHA-256。

## 11. 验收结论

**结论：通过但有警告（PASS WITH WARNINGS）。**

宁德时代（300750）2025 年全年 243 个交易日的真实行情已成功加工为
**243 行 × 22 列**的 analysis-ready 建模宽表。Pipeline 初始校验与复审均为 0 个
失败项，`label_next_5d` 未进入 approved features，未发现未来函数或标签泄漏，进程
退出码为 0。基本面全缺失和行业 `unknown` 是已明确记录的数据可用性限制；该宽表可
作为五日收益率研究的数据准备输入，但不代表模型预测能力或投资建议。

