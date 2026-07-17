# 模式 B 多股票横截面端到端测试记录（2026-07-17）

## 1. 测试概览

| 字段 | 记录 |
|---|---|
| 测试编号 | `NL-B-REAL-20260717-003` |
| 测试类型 | 自然语言 Agent 模式 B、4 个 ticker、横截面分组、真实行情 |
| 执行日期 | 2026-07-17 |
| 执行环境 | Windows PowerShell |
| 执行目录 | `D:\claude\dwzq\financial_table_workflow_agent_v3` |
| Run ID | `run_6429bb12` |
| Agent 运行状态 | `completed` |
| Pipeline 校验状态 | `passed_with_warnings` |
| **端到端验收结论** | **失败（FAIL：请求日期覆盖不完整）** |
| 进程退出码 | 原始记录未采集 |
| Python 版本 | 原始记录未采集 |
| LLM provider / model | 原始记录未采集 |
| Git commit | 原始记录未采集 |

> 本文件整理自测试人员提供的 PowerShell 输出、`fetch_metadata.json`、完整中文报告和
> 一页摘要。本次只归档和审查已有证据，没有重新调用网络或 LLM。Agent/Pipeline 的
> `completed` 或 `passed_with_warnings` 描述的是程序在现有数据上的执行结果，不自动
> 等同于端到端用户需求验收通过。

## 2. 测试目标

验证 Agent 能否抓取 4 只 A 股在 2025 年上半年的真实行情，并生成多股票横截面建模
宽表，重点检查：

1. 4 个 ticker 均被正确解析和抓取。
2. 请求日期区间得到充分覆盖。
3. 收益率与 rolling 特征按 ticker 分组，不跨股票串行计算。
4. `(ticker, date)` 主键唯一。
5. 不存在未来函数和标签泄漏。
6. 数据源降级与缺失信息被明确报告。
7. 生成完整中文报告，但不比较股票优劣、不输出投资建议。

## 3. 测试输入

### 3.1 自然语言需求

> 获取贵州茅台600519、平安银行000001、宁德时代300750和中国平安601318从2025年
> 1月1日至2025年6月30日的真实市场数据，不使用当前基本面快照，生成用于多股票横截面
> 研究的建模宽表。确保所有收益率和滚动特征都按 ticker 分组计算，不允许不同股票之间
> 串行计算，检查主键唯一性、日期覆盖、未来函数和标签泄漏，必要时安全修复，最后生成
> 完整中文报告。不比较股票优劣，不输出投资建议。

### 3.2 执行命令

```powershell
$prompt = "获取贵州茅台600519、平安银行000001、宁德时代300750和中国平安601318从2025年1月1日至2025年6月30日的真实市场数据，不使用当前基本面快照，生成用于多股票横截面研究的建模宽表。确保所有收益率和滚动特征都按 ticker 分组计算，不允许不同股票之间串行计算，检查主键唯一性、日期覆盖、未来函数和标签泄漏，必要时安全修复，最后生成完整中文报告。不比较股票优劣，不输出投资建议。"

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
  "tickers": ["600519", "000001", "300750", "601318"],
  "start_date": "2025-01-01",
  "end_date": "2025-06-30",
  "snapshot_fundamentals": false
}
```

解析结果与用户请求一致。

## 4. 验收标准与结果

| 编号 | 验收项 | 结果 | 判定 |
|---|---|---|---|
| AC-01 | 4 个 ticker 全部解析并取得真实行情 | 4/4，metadata 无 ticker error | 通过 |
| AC-02 | 覆盖请求区间 2025-01-01 至 2025-06-30 | 实际只有 2025-04-22 至 2025-06-30 | **失败** |
| AC-03 | 特征按 ticker 分组 | 报告声明 rolling/pct_change 按 ticker 分组 | 通过（未独立重算） |
| AC-04 | 主键唯一 | `primary_key_unique=True` | 通过 |
| AC-05 | 标签隔离 | `label_next_5d` 不在 approved features | 通过 |
| AC-06 | 未来函数检查 | 0 个失败项 | 通过 |
| AC-07 | 行业接口失败显式降级 | 4 个 ticker 均记录 warning，行业为 `unknown` | 通过 |
| AC-08 | 生成中文报告且无投资建议 | 报告已生成，无股票优劣或交易建议 | 通过 |

由于 AC-02 是用户明确要求的核心数据完整性条件，尽管其他检查通过，本次端到端验收仍
判定为 **FAIL**。

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
[tool] inspect_pipeline_status      ... ok
[stop] completed
```

工具链完整执行。初始 Critic 没有失败项，因此 remediation 返回 `not_needed`；但当前
Critic 没有把“实际行情起始日期显著晚于请求起始日期”识别为失败。

## 6. 真实数据抓取结果

### 6.1 数据来源

| 指标 | 结果 |
|---|---|
| 数据提供器 | `project_internal_astock_http` |
| Adapter / Data source 版本 | `0.2` / `1.0` |
| OHLCV 实际来源 | 4 个 ticker 均为 `sina_http_fallback` |
| 成交量单位 | `shares` |
| 抓取时间 | 2026-07-17 16:17:24 |
| 当前基本面快照 | 禁用 |
| metadata errors | `[]` |

### 6.2 各 ticker 行数

| 股票 | ticker | 行数 |
|---|---:|---:|
| 贵州茅台 | 600519 | 46 |
| 平安银行 | 000001 | 46 |
| 宁德时代 | 300750 | 46 |
| 中国平安 | 601318 | 46 |
| **合计** | — | **184** |

### 6.3 五张输入表

| 文件 | 行数 | 说明 |
|---|---:|---|
| `price.csv` | 184 | 4 × 46 行行情 |
| `volume.csv` | 184 | 4 × 46 行成交量 |
| `fundamentals.csv` | 0 | 仅表头；按请求禁用当前快照 |
| `industry.csv` | 4 | 行业接口失败，均降级为 `unknown` |
| `calendar.csv` | 181 | 请求区间的自然日历记录 |

### 6.4 抓取级警告

1. `snapshot_fundamentals=False`，因此不提供 PE/PB/ROE 历史值。
2. 4 个 ticker 的东方财富行业请求均发生代理连接错误，行业值为 `unknown`。

metadata 没有针对行情日期覆盖不足给出 warning 或 error。

## 7. 日期覆盖缺陷

| 项目 | 日期 |
|---|---|
| 用户请求起始日 | 2025-01-01 |
| 用户请求结束日 | 2025-06-30 |
| 实际宽表起始日 | **2025-04-22** |
| 实际宽表结束日 | 2025-06-30 |

四只股票均只有 46 行，且实际范围统一从 2025-04-22 开始。这不是单只股票停牌造成的
局部缺口，而是系统性缺少请求区间前半段。`calendar.csv` 有完整 181 个自然日不能证明
行情完整，因为 price/volume 在 2025-04-22 之前没有数据。

### 缺陷记录

| 字段 | 内容 |
|---|---|
| 缺陷编号 | `DEFECT-DATA-001` |
| 标题 | Sina fallback 返回部分区间但被标记为成功 |
| 严重程度 | 高：可能导致研究样本区间与用户请求不一致 |
| 当前行为 | 只要返回非空行情就继续，metadata `errors=[]`，最终报告称日期覆盖通过 |
| 期望行为 | 覆盖不足时补抓、切换数据源，或至少输出明确 warning/error 并在报告中披露 |

**可能原因（基于现象的推断，尚未在本次记录中做代码级修复验证）：** Sina K-line
fallback 按“最近 N 根”返回数据，而不是严格以请求 `end_date` 为锚点分页；当抓取日在
2026-07-17、请求区间在 2025 年上半年时，返回窗口可能只覆盖到 2025-04-22。修复时
应结合 provider 行为验证这一推断。

## 8. Pipeline 结果

| 阶段 | 状态 | 关键结果 |
|---|---|---|
| Stage 0：真实数据抓取 | completed | 4/4 非空，但日期覆盖不完整 |
| Stage 1：数据剖析 | completed | 5 张表，4 个 profile issue |
| Stage 2：工作流规划 | completed | 13 个步骤、12 项校验、8 个特征、1 个标签 |
| Stage 3：生成宽表 | completed | **184 行 × 22 列**，4 个 ticker |
| Stage 4：初始校验 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 5：安全修复 | `not_needed` | 0 轮修复、0 行删除 |
| Stage 6：复审 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 7：最终报告 | completed | 报告和一页摘要已生成 |

Pipeline 在已获取的 184 行数据上保持主键唯一、`close` 无缺失、标签隔离成立。这说明
数据加工的内部一致性通过，但不能弥补输入日期覆盖不足。

## 9. 横截面与标签安全

| 检查项 | 结果 |
|---|---|
| ticker 数 | 4 |
| 每 ticker 行数 | 均为 46 |
| `(ticker, date)` 主键 | 唯一 |
| `close` 缺失率 | 0 |
| 初始 / 复审失败项 | 0 / 0 |
| `label_next_5d` 在 approved features 中 | `False` |
| `label_not_in_approved_features` | `True` |
| 删除行数 / 修复轮数 | 0 / 0 |
| 人工复核要求 | `false` |

报告声明收益率、rolling 和 pct_change 按 ticker 分组，避免跨股票串行计算。原始记录
没有提供特征列逐行抽样或独立重算结果，因此该项属于 Pipeline 报告验证，不是第二套
独立数值实现的交叉验证。

## 10. 运行产物

原始运行根目录：

```text
D:/claude/dwzq/financial_table_workflow_agent_v3/outputs_agent/runs/run_6429bb12
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

`outputs_agent/` 不提交 Git。本记录没有采集进程退出码、Git commit、Python 版本或 LLM
provider/model；后续复测应一并记录，并对关键产物计算 SHA-256。

## 11. 验收结论

**端到端验收结论：失败（FAIL）。**

Agent 成功完成 4 ticker 的抓取、横截面宽表加工、标签隔离、校验和报告生成，现有
184 行 × 22 列宽表本身主键唯一、无 Critic 失败项。然而用户要求的日期范围是
2025-01-01 至 2025-06-30，实际数据仅覆盖 2025-04-22 至 2025-06-30。系统没有补抓，
也没有把覆盖不足记录为 warning/error，最终总结反而将日期覆盖判为通过。因此本次不能
作为“2025 年上半年完整横截面数据”验收通过，应先修复 `DEFECT-DATA-001` 后复测。

