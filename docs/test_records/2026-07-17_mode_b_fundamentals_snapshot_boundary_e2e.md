# 模式 B 当前基本面快照时间边界端到端测试记录（2026-07-17）

## 1. 测试概览

| 字段 | 记录 |
|---|---|
| 测试编号 | `NL-B-REAL-20260717-005` |
| 测试类型 | 自然语言 Agent 模式 B、2 个 ticker、真实行情、当前基本面快照时间边界 |
| 执行日期 | 2026-07-17 |
| 执行环境 | Windows PowerShell |
| 执行目录 | `D:\claude\dwzq\financial_table_workflow_agent_v3` |
| Run ID | `run_abeccf48` |
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

验证 Agent 在允许抓取当前 PE、PB、ROE 快照时，能否严格遵守基本面数据的时间边界：

1. 当前快照的 `announce_date` 必须等于真实抓取日期。
2. 当前快照不得回填、插值或以其他方式映射到抓取日前的历史样本。
3. 当前快照不得被描述为历史 point-in-time 基本面。
4. 最终报告必须明确披露基本面来源和可用时间。
5. 财务表必须按 `announce_date` 做 backward as-of 对齐。
6. `label_next_5d` 必须与 approved features 隔离。
7. 初始校验通过时 remediation 应返回 `not_needed`。

## 3. 测试输入

### 3.1 自然语言需求

> 获取贵州茅台600519和平安银行000001从2026年1月1日至2026年7月15日的真实市场
> 数据，并允许获取当前 PE、PB、ROE 基本面快照。当前快照只能使用真实抓取日期作为
> announce_date，不得回填到历史日期，不得伪装成历史 point-in-time 基本面。生成用于
> 五日收益率研究的建模宽表，重点检查基本面时间对齐、未来函数和标签泄漏，必要时安全
> 修复，最后生成完整中文报告，并在报告中明确说明基本面数据的时间边界。

### 3.2 执行命令

```powershell
$prompt = "获取贵州茅台600519和平安银行000001从2026年1月1日至2026年7月15日的真实市场数据，并允许获取当前 PE、PB、ROE 基本面快照。当前快照只能使用真实抓取日期作为 announce_date，不得回填到历史日期，不得伪装成历史 point-in-time 基本面。生成用于五日收益率研究的建模宽表，重点检查基本面时间对齐、未来函数和标签泄漏，必要时安全修复，最后生成完整中文报告，并在报告中明确说明基本面数据的时间边界。"

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
  "tickers": ["600519", "000001"],
  "start_date": "2026-01-01",
  "end_date": "2026-07-15",
  "snapshot_fundamentals": true
}
```

解析结果与用户请求一致，且明确启用了当前基本面快照。

## 4. 验收标准与结果

| 编号 | 验收项 | 结果 | 判定 |
|---|---|---|---|
| AC-01 | 2 个 ticker 全部取得真实行情 | 2/2 成功，metadata `errors=[]` | 通过 |
| AC-02 | 当前基本面快照已启用并抓取 | `snapshot_fundamentals_enabled=true`，2 行 | 通过 |
| AC-03 | 快照使用真实抓取日作为 `announce_date` | metadata 与报告均声明 `announce_date=2026-07-17` | 通过（未逐行独立核对 CSV） |
| AC-04 | 快照未回填到历史样本 | 样本最晚 2026-07-15；历史面板 `pe/pb/roe` 均 100% 缺失 | 通过 |
| AC-05 | 最终报告披露基本面时间边界 | 报告第 2 节明确说明当前快照非历史 point-in-time 数据 | 通过 |
| AC-06 | 未来函数检查 | 14 通过、1 警告、0 失败 | 通过 |
| AC-07 | 标签隔离 | `label_next_5d` 不在 approved features | 通过 |
| AC-08 | 行业接口失败显式降级 | 记录 warning，行业值为 `unknown` | 通过 |

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
| 抓取时间 | 2026-07-17 16:21:45 |
| 抓取日期 | 2026-07-17 |
| 当前基本面快照 | 启用 |
| metadata errors | `[]` |

### 6.2 各 ticker 行数

| 股票 | ticker | 行数 |
|---|---:|---:|
| 贵州茅台 | 600519 | 127 |
| 平安银行 | 000001 | 127 |
| **合计** | — | **254** |

### 6.3 五张输入表

| 文件 | 行数 | 说明 |
|---|---:|---|
| `price.csv` | 254 | 2 × 127 行 OHLC |
| `volume.csv` | 254 | 2 × 127 行成交量 |
| `fundamentals.csv` | 2 | 每只股票一行当前 PE/PB/ROE 快照 |
| `industry.csv` | 2 | 行业服务失败，均降级为 `unknown` |
| `calendar.csv` | 196 | 请求区间自然日历及交易日标记 |

### 6.4 抓取级警告

1. `fundamentals.csv` 中的 PE/PB/ROE 是当前快照，`announce_date` 为抓取日期，不能
   视为抓取日前的历史 point-in-time 数据。
2. 两只股票的行业请求均发生代理连接错误，`industry_name` 设置为 `unknown`；OHLCV
   主流程不受影响。

## 7. 基本面时间边界专项检查

本次测试的关键时间关系如下：

| 时间点 | 日期 |
|---|---|
| 请求行情起始日 | 2026-01-01 |
| 宽表实际起始日 | 2026-01-05 |
| 请求及宽表结束日 | 2026-07-15 |
| 当前快照抓取日 / `announce_date` | 2026-07-17 |

当前基本面快照在整个研究区间结束后两天才可获得。Executor 按 `announce_date` 使用
`merge_asof(direction='backward')` 对齐，因此 2026-07-15 及以前的样本不能匹配到
2026-07-17 才公布的快照。最终宽表中 `pe/pb/roe` 的缺失率均为 100%，这不是抓取
失败，而是正确执行时间因果约束的结果。

必须强调：不能通过前向/后向填充、插值或人为修改 `announce_date`，把当前快照扩散
到历史样本。若研究需要历史估值或盈利能力特征，必须接入带真实公告日期的历史
point-in-time 财务数据源。

## 8. Pipeline 结果

| 阶段 | 状态 | 关键结果 |
|---|---|---|
| Stage 0：真实数据抓取 | completed | 2/2 ticker 成功，254 行行情，2 行基本面快照 |
| Stage 1：数据剖析 | completed | 5 张表，6 个 profile issue |
| Stage 2：工作流规划 | completed | 13 个步骤、12 项校验、8 个特征、1 个标签 |
| Stage 3：生成宽表 | completed | **254 行 × 22 列**，2 个 ticker |
| Stage 4：初始校验 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 5：安全修复 | `not_needed` | 0 轮修复、0 行删除 |
| Stage 6：复审 | `passed_with_warnings` | 14 通过、1 警告、0 失败 |
| Stage 7：最终报告 | completed | 报告和一页摘要已生成 |

## 9. 数据质量与标签安全

| 检查项 | 结果 |
|---|---|
| 宽表规模 | 254 行 × 22 列 |
| 实际日期范围 | 2026-01-05 至 2026-07-15 |
| ticker 数 | 2 |
| `(ticker, date)` 主键 | 唯一 |
| `close` 缺失率 | 0 |
| `pe/pb/roe` 缺失率 | 各 100%（符合快照时间边界） |
| 初始 / 复审失败项 | 0 / 0 |
| 删除行数 / 修复轮数 | 0 / 0 |
| `label_next_5d` 在 approved features 中 | `False` |
| `label_not_in_approved_features` | `True` |
| 人工复核要求 | `false` |

虽然 `pe/pb/roe` 名称仍出现在 approved feature columns 中，但在本历史样本内没有可用
值，因此不能直接作为有效基本面特征训练模型。approved 列表只证明角色和泄漏检查
通过，不代表该列在当前样本中具备数据可用性。

## 10. 警告、证据边界与限制

唯一 Critic warning 为 `missing_rate_after_join`：`close` 无缺失，`pe/pb/roe` 缺失率
为 100%；行业服务失败还使 `industry_name` 不可用。这些是已披露的数据可用性限制，
没有导致 OHLCV 特征发生未来函数或标签泄漏。

- 原始记录展示了 metadata 和生成报告，但没有展示 `fundamentals.csv` 的两行原始值，
  因此本记录未独立核对每行 `announce_date` 和 PE/PB/ROE 数值。
- 原始记录没有附关键产物 SHA-256，也没有进行第二套独立算法重算。
- 本流程只准备数据，没有训练或评估预测模型。
- 本结果不构成选股、择时、交易或投资建议。

## 11. 运行产物

原始运行根目录：

```text
D:/claude/dwzq/financial_table_workflow_agent_v3/outputs_agent/runs/run_abeccf48
```

| 产物 | Run root 下的相对路径 |
|---|---|
| 抓取元数据 | `raw_data/fetch_metadata.json` |
| 当前基本面快照 | `raw_data/fundamentals.csv` |
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

贵州茅台与平安银行的 254 行真实行情已加工为 **254 行 × 22 列**建模宽表。两行当前
基本面快照使用 2026-07-17 作为可用时间；由于研究样本截止于 2026-07-15，快照没有
被倒灌至历史样本，`pe/pb/roe` 在宽表中保持全缺失。该行为正确避免了未来函数，最终
报告也明确披露了时间边界。行业数据降级为 `unknown`，且原始 CSV 行值和产物哈希未在
记录中采集，因此结论保留警告。
