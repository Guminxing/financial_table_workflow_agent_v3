# 模式 B 两年长区间端到端测试记录（2026-07-17）

## 1. 测试概览

| 字段 | 记录 |
|---|---|
| 测试编号 | `NL-B-REAL-20260717-004` |
| 测试类型 | 自然语言 Agent 模式 B、2 个 ticker、两年长区间、真实行情 |
| 执行日期 | 2026-07-17 |
| 执行环境 | Windows PowerShell |
| 执行目录 | `D:\claude\dwzq\financial_table_workflow_agent_v3` |
| Run ID | `run_dff7f5d4` |
| Agent 运行状态 | `completed` |
| Pipeline 校验状态 | `passed_with_warnings` |
| 端到端验收结论 | **通过但有警告（PASS WITH WARNINGS）** |
| 进程退出码 | 原始记录未采集 |
| Python 版本 | 原始记录未采集 |
| LLM provider / model | 原始记录未采集 |
| Git commit | 原始记录未采集 |

> 本文件整理自测试人员提供的 PowerShell 输出、`fetch_metadata.json`、完整报告和一页
> 摘要。本次没有重新调用网络或 LLM，也没有补造原始日志未采集的信息。

## 2. 测试目标

验证 Agent 能否处理贵州茅台（600519）与五粮液（000858）两年真实行情，重点覆盖：

1. 2024-01-01 至 2025-12-31 长区间的数据覆盖。
2. 重复记录和 `(ticker, date)` 主键唯一性。
3. 交易日历、五日收益率、二十日 rolling 窗口和标签尾部缺失检查。
4. 未来函数和标签泄漏检查。
5. 行业服务失败时的显式降级。
6. 初始校验通过时 remediation 正确返回 `not_needed`。
7. 不训练模型、不输出投资建议。

## 3. 测试输入

### 3.1 自然语言需求

> 获取贵州茅台600519和五粮液000858从2024年1月1日至2025年12月31日的真实市场
> 数据，不使用当前基本面快照，生成用于五日收益率和二十日波动率研究的建模宽表。
> 检查长时间区间下的数据完整性、重复记录、交易日历、滚动窗口、标签尾部缺失、未来
> 函数和标签泄漏，必要时安全修复，最后生成完整中文报告。不训练模型，不输出投资建议。

### 3.2 执行命令

```powershell
$prompt = "获取贵州茅台600519和五粮液000858从2024年1月1日至2025年12月31日的真实市场数据，不使用当前基本面快照，生成用于五日收益率和二十日波动率研究的建模宽表。检查长时间区间下的数据完整性、重复记录、交易日历、滚动窗口、标签尾部缺失、未来函数和标签泄漏，必要时安全修复，最后生成完整中文报告。不训练模型，不输出投资建议。"

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
  "tickers": ["600519", "000858"],
  "start_date": "2024-01-01",
  "end_date": "2025-12-31",
  "snapshot_fundamentals": false
}
```

解析结果与用户请求一致。

## 4. 验收标准与结果

| 编号 | 验收项 | 结果 | 判定 |
|---|---|---|---|
| AC-01 | 2 个 ticker 全部取得真实行情 | 2/2 成功，metadata `errors=[]` | 通过 |
| AC-02 | 两年请求区间充分覆盖 | 2024-01-02 至 2025-12-31 | 通过 |
| AC-03 | 各 ticker 覆盖一致 | 两只股票均为 485 行 | 通过 |
| AC-04 | 重复及主键检查 | `primary_key_unique=True` | 通过 |
| AC-05 | rolling、日历、标签尾部检查 | Pipeline 报告列入 14 个通过项 | 通过（未独立重算） |
| AC-06 | 标签隔离 | `label_next_5d` 不在 approved features | 通过 |
| AC-07 | 未来函数检查 | 0 个失败项 | 通过 |
| AC-08 | 行业接口失败显式降级 | 记录 warning，行业值为 `unknown` | 通过 |

请求起始日 2024-01-01 为非交易日，实际行情从 2024-01-02 开始符合预期。

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
```

两个 guarded 工具均经过 ASK/approval 流程。初始 Critic 没有失败项，因此 remediation
没有改写数据，`repaired_panel.csv` 是 prepared panel 的 no-op 副本。

## 6. 真实数据抓取结果

### 6.1 数据来源

| 指标 | 结果 |
|---|---|
| 数据提供器 | `project_internal_astock_http` |
| Adapter / Data source 版本 | `0.2` / `1.0` |
| OHLCV 实际来源 | 两只股票均为 `sina_http_fallback` |
| 成交量单位 | `shares` |
| 抓取时间 | 2026-07-17 16:24:49 |
| 当前基本面快照 | 禁用 |
| metadata errors | `[]` |

### 6.2 各 ticker 行数

| 股票 | ticker | 行数 |
|---|---:|---:|
| 贵州茅台 | 600519 | 485 |
| 五粮液 | 000858 | 485 |
| **合计** | — | **970** |

### 6.3 五张输入表

| 文件 | 行数 | 说明 |
|---|---:|---|
| `price.csv` | 970 | 2 × 485 行 OHLC |
| `volume.csv` | 970 | 2 × 485 行成交量 |
| `fundamentals.csv` | 0 | 仅表头；按请求禁用当前快照 |
| `industry.csv` | 2 | 行业服务失败，均降级为 `unknown` |
| `calendar.csv` | 731 | 两年自然日历及交易日标记 |

### 6.4 抓取级警告

1. `snapshot_fundamentals=False`，因此没有 PE/PB/ROE 历史数据。
2. 贵州茅台行业请求发生代理连接错误；五粮液行业请求返回 HTTP 502。两只股票的
   `industry_name` 均设置为 `unknown`，OHLCV 主流程继续执行。

系统没有猜测或伪造行业分类。

## 7. Pipeline 结果

| 阶段 | 状态 | 关键结果 |
|---|---|---|
| Stage 0：真实数据抓取 | completed | 2/2 ticker 成功，970 行行情 |
| Stage 1：数据剖析 | completed | 5 张表，4 个 profile issue |
| Stage 2：工作流规划 | completed | 13 个步骤、12 项校验、8 个特征、1 个标签 |
| Stage 3：生成宽表 | completed | **970 行 × 22 列**，2 个 ticker |
| Stage 4：初始校验 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 5：安全修复 | `not_needed` | 0 轮修复、0 行删除 |
| Stage 6：复审 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 7：最终报告 | completed | 报告和一页摘要已生成 |

## 8. 数据质量与标签安全

| 检查项 | 结果 |
|---|---|
| 宽表规模 | 970 行 × 22 列 |
| 实际日期范围 | 2024-01-02 至 2025-12-31 |
| ticker 数 | 2 |
| `(ticker, date)` 主键 | 唯一 |
| `close` 缺失率 | 0 |
| 初始 / 复审失败项 | 0 / 0 |
| 删除行数 / 修复轮数 | 0 / 0 |
| `label_next_5d` 在 approved features 中 | `False` |
| `label_not_in_approved_features` | `True` |
| 人工复核要求 | `false` |

报告把数据完整性、重复记录、交易日历、rolling 窗口、标签尾部缺失、未来函数和标签
泄漏列入 14 个通过项。原始证据没有附这些特征的逐行抽样或第二套独立数值重算，因此
本记录将其标记为 Pipeline 验证结果，而不是独立算法交叉验证。

## 9. Critic 警告与限制

唯一 Critic warning 为 `missing_rate_after_join`：`close` 无缺失，但由于基本面快照被
禁用，`pe/pb/roe` 缺失率为 100%；行业服务失败导致 `industry_name` 不可用。这不会
造成 OHLCV 特征的未来函数或标签泄漏，但限制了估值和行业因子的可用性。

- 历史研究如需估值因子，应接入 point-in-time 基本面数据，不能回填当前快照。
- 本流程只准备数据，没有训练或评估预测模型。
- 本结果不构成选股、择时、交易或投资建议。

## 10. 与短历史区间缺陷的关系

本次两年请求获得了完整边界，但测试 `NL-B-REAL-20260717-003` 的 2025 上半年请求只
获得 2025-04-22 之后的数据。两者并不矛盾：当前 Sina fallback 的 `datalen` 根据请求
区间长度计算；两年请求产生更大的返回窗口，可能足以覆盖目标区间，而较短但较早的
历史请求可能不足以回溯到 `start_date`。

因此本测试通过不代表 `DEFECT-DATA-001` 已解决。系统仍需在每次抓取后验证实际日期
覆盖，而不能以“返回非空”为成功的唯一条件。

## 11. 运行产物

原始运行根目录：

```text
D:/claude/dwzq/financial_table_workflow_agent_v3/outputs_agent/runs/run_dff7f5d4
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
provider/model；后续测试应一并记录，并对关键产物计算 SHA-256。

## 12. 验收结论

**结论：通过但有警告（PASS WITH WARNINGS）。**

贵州茅台与五粮液 2024–2025 两年真实行情已成功加工为 **970 行 × 22 列**的建模
宽表，实际范围从首个交易日 2024-01-02 覆盖到 2025-12-31。宽表主键唯一、`close`
无缺失、标签隔离成立，初始校验和复审均为 0 个失败项。基本面全缺失和行业 `unknown`
是已披露的数据可用性限制；本结果可作为五日收益率和二十日波动率研究的数据准备输入，
但不代表模型预测能力或投资建议。

