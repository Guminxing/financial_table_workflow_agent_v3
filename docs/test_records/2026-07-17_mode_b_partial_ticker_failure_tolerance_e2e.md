# 模式 B 部分 ticker 失败容错测试记录（2026-07-17）

## 1. 测试概览

| 字段 | 记录 |
|---|---|
| 测试编号 | `NL-B-REAL-20260717-006` |
| 测试类型 | 自然语言 Agent 模式 B、有效 ticker + 测试代码、部分 ticker 失败容错 |
| 执行日期 | 2026-07-17 |
| 执行环境 | Windows PowerShell |
| 执行目录 | `D:\claude\dwzq\financial_table_workflow_agent_v3` |
| Run ID | `run_ca27fa25` |
| Agent 运行状态 | `completed`（生成失败说明后结束） |
| 数据抓取工具状态 | `failed` |
| Pipeline 状态 | 未启动 |
| 端到端验收结论 | **失败（FAIL）** |
| 进程退出码 | 原始记录未采集 |
| Python 版本 | 原始记录未采集 |
| LLM provider / model | 原始记录未采集 |
| Git commit | 原始记录未采集 |

> 本文件整理自测试人员提供的 PowerShell 输出和 `fetch_metadata.json`。本次没有重新
> 调用网络或 LLM，也没有补造原始日志未采集的信息。

## 2. 测试目标

本用例原本要验证部分 ticker 抓取失败时的安全降级能力：

1. 贵州茅台（600519）应保留真实行情。
2. 测试代码（123456）若无数据，应明确记录失败原因和 0 行结果。
3. 不得为失败 ticker 生成合成数据或使用其他股票的数据替代。
4. 只要至少一个 ticker 有可用行情，Pipeline 就应继续生成建模宽表和中文报告。
5. 报告应披露成功 ticker、失败 ticker、各 ticker 行数和错误原因。
6. 最终宽表应通过未来函数和标签泄漏检查。

实际运行中 600519 与 123456 均返回 0 行，因而没有形成预期的“一个成功、一个失败”
场景，部分失败继续执行能力未得到验证。

## 3. 测试输入

### 3.1 自然语言需求

> 获取贵州茅台600519和测试代码123456从2025年1月1日至2025年3月31日的真实市场
> 数据，不使用当前基本面快照。若个别 ticker 无法取得真实数据，不得生成合成数据或
> 用其他股票数据替代；应保留成功 ticker，明确记录失败 ticker、错误原因和各 ticker
> 行数，并在仍有可用行情时继续生成建模宽表。检查未来函数和标签泄漏，最后生成中文
> 报告。

### 3.2 执行命令

```powershell
$prompt = "获取贵州茅台600519和测试代码123456从2025年1月1日至2025年3月31日的真实市场数据，不使用当前基本面快照。若个别 ticker 无法取得真实数据，不得生成合成数据或用其他股票数据替代；应保留成功 ticker，明确记录失败 ticker、错误原因和各 ticker 行数，并在仍有可用行情时继续生成建模宽表。检查未来函数和标签泄漏，最后生成中文报告。"

python -B src/chat_agent.py `
  --output_base outputs_agent `
  --max_tool_turns 20 `
  --prompt $prompt `
  --auto_approve_data_fetch `
  --auto_approve_remediation
```

### 3.3 Agent 解析后的参数

```json
{
  "tickers": ["600519", "123456"],
  "start_date": "2025-01-01",
  "end_date": "2025-03-31",
  "snapshot_fundamentals": false
}
```

解析结果与用户请求一致。

## 4. 验收标准与结果

| 编号 | 验收项 | 结果 | 判定 |
|---|---|---|---|
| AC-01 | 600519 取得真实行情 | 0 行，抓取失败 | **失败** |
| AC-02 | 123456 失败被明确记录 | 0 行，`per_ticker_errors` 有具体错误 | 通过 |
| AC-03 | 不生成合成或替代数据 | `price.csv`、`volume.csv` 均为 0 行 | 通过 |
| AC-04 | 保留成功 ticker 并继续 | 没有成功 ticker，目标场景未形成 | 未验证 |
| AC-05 | 所有行情为空时安全停止 | 工具返回 `failed`，后续 Pipeline 未启动 | 通过 |
| AC-06 | 生成建模宽表 | 未生成 | **失败** |
| AC-07 | 运行未来函数和标签泄漏检查 | Critic 未运行 | **失败** |
| AC-08 | 生成正式中文 Pipeline 报告 | `final_report/` 不存在 | **失败** |

虽然 Agent 最后输出了一段中文失败说明，但它不是
`final_report/final_workflow_report.md`，不能替代用户要求的正式 Pipeline 报告。

## 5. 执行过程记录

```text
[approval] requested: fetch_real_market_data
[stop] awaiting_approval
(auto-approved via --auto_approve_data_fetch)
[tool] fetch_real_market_data       ... running
[tool] fetch_real_market_data       ... failed
[stop] completed
```

本次只执行了真实数据抓取工具。由于所有 ticker 的 OHLCV 均为空，系统没有配置
`input_dir`，`configure_workflow`、profile、plan、prepare、critic、remediation 和 report
均未启动。这里的 `[stop] completed` 表示 Agent 已完成失败说明，不代表数据工作流成功。

## 6. 真实数据抓取结果

### 6.1 数据来源

| 指标 | 结果 |
|---|---|
| 数据提供器 | `project_internal_astock_http` |
| Adapter / Data source 版本 | `0.2` / `1.0` |
| 成交量单位 | `shares` |
| 抓取时间 | 2026-07-17 16:37:38 |
| 请求区间 | 2025-01-01 至 2025-03-31 |
| 当前基本面快照 | 禁用 |
| metadata errors | 2 条 |

### 6.2 各 ticker 结果

| ticker | 预期角色 | 行数 | metadata 错误 |
|---|---|---:|---|
| 600519 | 有效股票，用于验证成功分支 | 0 | `no OHLCV rows returned for 600519 in [2025-01-01, 2025-03-31]` |
| 123456 | 测试代码，用于验证失败分支 | 0 | `no OHLCV rows returned for 123456 in [2025-01-01, 2025-03-31]` |

`ohlcv_source_by_ticker` 为空，说明没有任何 ticker 成功解析为行情数据。metadata 只证明
两个请求均无返回行；它没有提供足够证据把 600519 的失败归因于证券代码无效。

### 6.3 五张输出表

| 文件 | 行数 | 说明 |
|---|---:|---|
| `price.csv` | 0 | 仅表头，无行情数据 |
| `volume.csv` | 0 | 仅表头，无成交量数据 |
| `fundamentals.csv` | 0 | 按请求禁用当前快照 |
| `industry.csv` | 2 | 行业接口失败，均降级为 `unknown` |
| `calendar.csv` | 90 | 请求区间自然日历及交易日标记 |

### 6.4 抓取级警告

1. `snapshot_fundamentals=False`，`fundamentals.csv` 仅保留表头。
2. 两只股票的行业接口均返回 HTTP 502，`industry_name` 设置为 `unknown`。
3. 600519 和 123456 均没有 OHLCV 行，导致抓取整体失败。

## 7. 失败分析

### 7.1 核心失败

600519 是本用例用于建立“成功 ticker”分支的标的，但指定的 2025 年第一季度区间没有
返回任何行情。因为系统要求至少有一个成功 ticker 才能配置后续 Pipeline，本次没有
机会验证“保留成功 ticker、跳过失败 ticker并继续”的核心能力。

### 7.2 与已知日期覆盖问题的关系

测试 `NL-B-REAL-20260717-003` 请求 2025-01-01 至 2025-06-30 时，600519 等标的只返回
2025-04-22 之后的数据。本次请求更早且更短的 2025 年第一季度，600519 直接返回 0 行。
两次现象方向一致，进一步支持 `DEFECT-DATA-001`：短历史区间下，Sina fallback 的返回
窗口可能不足以覆盖请求起点。

这是根据两次测试结果作出的推断；当前日志没有 HTTP 原始响应或数据源内部调试信息，
因此尚不能仅凭本记录确定根因。

### 7.3 失败处理中的正确行为

- 没有生成合成行情。
- 没有用其他股票数据替代失败 ticker。
- `rows_by_ticker` 和 `per_ticker_errors` 完整保留了两个失败结果。
- 所有行情为空时没有强行启动 Pipeline 或伪造成功报告。

这些安全行为通过，但不能抵消有效 ticker 抓取失败和核心端到端目标未完成。

## 8. 未生成的 Pipeline 产物

以下步骤和产物均不存在：

| 阶段 / 产物 | 状态 |
|---|---|
| `configure_workflow` | 未运行 |
| profile / workflow plan | 未生成 |
| `prepared_panel.csv` | 未生成 |
| 初始及复审 Critic | 未运行 |
| `approved_feature_columns.json` | 未生成 |
| `repaired_panel.csv` | 未生成 |
| `final_workflow_report.md` | 未生成 |
| `final_workflow_one_page.md` | 未生成 |

测试人员随后读取两个 final report 文件时均收到 PowerShell `PathNotFound`，与流程在抓取
阶段停止的结果一致。

## 9. 证据边界与后续复测条件

- 原始记录没有采集进程退出码、Python 版本、LLM provider/model、Git commit 或产物哈希。
- Agent 总结称错误不可重试，但展示的 metadata 没有 `retryable` 字段；本记录不把该说法
  作为已验证事实。
- metadata 没有证券有效性校验结果，只记录“未返回 OHLCV”；因此不能仅凭本次结果断言
  123456 是被代码主动识别并拒绝的无效证券。

修复日期覆盖问题后，应使用同一 prompt 复测。预期结果是 600519 行数大于 0、123456
行数为 0，`per_ticker_errors` 只包含 123456，随后 Pipeline 使用 600519 的真实行情继续
完成，并在正式报告中披露部分失败。

## 10. 运行产物

原始运行根目录：

```text
D:/claude/dwzq/financial_table_workflow_agent_v3/outputs_agent/runs/run_ca27fa25
```

本次只确认存在：

| 产物 | Run root 下的相对路径 |
|---|---|
| 抓取元数据 | `raw_data/fetch_metadata.json` |
| 空行情表 | `raw_data/price.csv` |
| 空成交量表 | `raw_data/volume.csv` |
| 空基本面表 | `raw_data/fundamentals.csv` |
| 行业降级表 | `raw_data/industry.csv` |
| 交易日历 | `raw_data/calendar.csv` |

`outputs_agent/` 不提交 Git。

## 11. 验收结论

**结论：失败（FAIL）。**

系统正确记录了两个 ticker 的 0 行结果和错误原因，也没有生成合成或替代数据；但用于
建立成功分支的贵州茅台 600519 同样抓取失败，导致部分失败降级场景未被验证，建模宽表、
Critic 检查和正式中文报告全部未生成。本次结果进一步暴露了短历史区间的行情覆盖问题，
应在修复 `DEFECT-DATA-001` 后使用相同输入复测。
