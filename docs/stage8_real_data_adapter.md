# 第八阶段：真实 A 股数据接入适配器

## 1. 为什么需要 Stage 8

前七阶段（Profiler → Planner → Executor → Critic → Repair → Re-run Critic → Final Report → One-Click Runner + Agent Shell）已形成完整闭环。v3 起项目**只使用真实市场数据**作为正式输入，合成样例数据及其自动生成逻辑已彻底移除。本阶段把参考项目 `TradingAgents-astock-main` 的真实 A 股行情获取能力接入当前项目，输出本项目约定的五张 CSV，并确保现有流水线能完整处理真实数据。

**重要边界**：本阶段只新增数据接入适配器与配套 CLI，**不修改前六阶段核心数据处理逻辑**（仅做必要的空基本面兼容与无需 Repair 时的 no-op 产物修复），**不修改参考项目**（参考项目是只读依赖）。

---

## 2. 数据接入架构

```
TradingAgents-astock-main (只读依赖)
  └─ tradingagents/dataflows/a_stock.py
       ├─ _load_ohlcv_astock  (mootdx TCP -> Sina HTTP fallback，内部决定)
       ├─ _sina_kline_fallback
       ├─ _normalize_ticker
       ├─ _tencent_quote      (PE/PB 当前快照)
       └─ _em_get             (东财行业 f127)
            │
            ▼
src/real_data_adapter.py  (RealDataFetchConfig + fetch_real_data)
            │  复用参考项目函数，不复制逻辑
            ▼
data/real_market/*.csv  (price / volume / fundamentals / industry / calendar)
  + fetch_metadata.json
            │
            ▼
python src/run_all.py --input_dir data/real_market --output_root outputs_real
  (或 src/run_fetch_real_data.py --run_pipeline 一条命令完成抓取+流水线)
```

缓存与日志写到当前项目的 `outputs/cache` 下（通过 `TRADINGAGENTS_CACHE_DIR` 环境变量指向当前项目目录），**不写入参考项目目录**。`data/real_market/` 与 `outputs_real/` 均不提交 Git。

---

## 3. 运行命令

### 3.1 仅抓取真实数据

```bash
python src/run_fetch_real_data.py --tickers 600519,000001,300750 \
    --start_date 2024-01-01 --end_date 2024-06-30 \
    --output_dir data/real_market \
    --tradingagents_path D:\dwzq\TradingAgents-astock-main
```

### 3.2 抓取并直接运行完整流水线

```bash
python src/run_fetch_real_data.py --tickers 600519,000001 \
    --start_date 2024-01-01 --end_date 2024-06-30 \
    --output_dir data/real_market \
    --tradingagents_path D:\dwzq\TradingAgents-astock-main \
    --run_pipeline --output_root outputs_real
```

### 3.3 抓取时不取基本面快照（fundamentals.csv 只输出表头）

```bash
python src/run_fetch_real_data.py --tickers 600519 \
    --start_date 2024-01-01 --end_date 2024-01-10 \
    --output_dir test_data/real_market_sample \
    --tradingagents_path D:\dwzq\TradingAgents-astock-main \
    --no_snapshot_fundamentals
```

### 3.4 用抓取到的真实数据单独跑流水线

```bash
python src/run_all.py --input_dir data/real_market --output_root outputs_real
```

---

## 4. TradingAgents 项目路径解析优先级

1. 命令行 `--tradingagents_path` 显式传入
2. 环境变量 `TRADINGAGENTS_ASTOCK_PATH`
3. 默认路径 `D:\dwzq\TradingAgents-astock-main`
4. 相对路径 `..\TradingAgents-astock-main`

解析时校验 `tradingagents/dataflows/a_stock.py` 存在。

---

## 5. 五张 CSV 契约

| 文件 | 列 | 说明 |
|---|---|---|
| `price.csv` | trade_date, ticker, open, high, low, close | 真实 OHLCV 的 OHLC 部分 |
| `volume.csv` | date, stock_code, volume, turnover | volume 来自真实 OHLCV；turnover 无可靠来源时留空，不伪造 |
| `fundamentals.csv` | report_date, announce_date, ticker, pe, pb, roe | 当前快照，announce_date = 抓取日期 |
| `industry.csv` | ticker, industry_name | 优先东财 f127 真实行业；失败时 unknown |
| `calendar.csv` | date, is_trading_day | 覆盖请求区间；有真实行情的日期标记 1，其余 0 |

OHLCV 严格按 `start_date ~ end_date` 过滤；按 (date, ticker) 去重（keep last）；按 date/ticker 排序；open/high/low/close/volume 转为数值。**禁止用随机数/样例/前值填充伪造**。

---

## 6. fetch_metadata.json 字段

- `generated_at` / `fetch_date`
- `tradingagents_path` / `cache_dir`
- `requested_tickers` / `resolved_tickers`
- `start_date` / `end_date`
- `ohlcv_source_by_ticker`（如实记录：`internal_fallback` / `sina_http_direct` / `unknown`，不猜测具体是 mootdx 还是 Sina）
- `rows_by_ticker`（每个 ticker 的行情行数）
- `per_ticker_errors` / `per_ticker_warnings`
- `summary_rows`（各输出表行数）
- `output_files`（各 CSV 绝对路径，正斜杠）
- `fundamentals_limitation`（明确说明当前快照非历史 point-in-time）
- `warnings` / `errors`

**全部 ticker 抓取失败或 price.csv 为空时**，`errors` 非空，CLI 返回非零退出码且不运行后续流水线；部分失败时继续处理成功 ticker，metadata 记录失败项。

---

## 7. 网络访问要求

真实数据抓取需要网络访问以下域名（参考项目直连 HTTP，零第三方数据库依赖）：

- `money.finance.sina.com.cn`（Sina K-line fallback）
- `qt.gtimg.cn`（腾讯 PE/PB 快照）
- `push2.eastmoney.com`（东财行业字段）
- mootdx TCP 7709（若安装 mootdx；未安装时自动走 Sina HTTP fallback）

若运行环境无法联网，必须明确标记"网络限制"，**不得生成合成数据冒充测试成功**。流水线处理本身离线可运行（只读已抓取的 CSV）。

---

## 8. 基本面数据的时间点限制（关键）

参考项目 `TradingAgents-astock-main` 中的 PE/PB/ROE 更接近**当前快照**，不是完整的历史 point-in-time 基本面数据库。必须严格遵守：

- **不得**把当前 PE/PB/ROE 伪装成历史基本面。
- 当前快照的 `announce_date` **必须**使用真实抓取日期（`date.today()`）。
- **不得**使用用户指定的历史 `end_date` 作为当前快照的 `announce_date`。
- **不得**把当前快照复制到整个历史区间。
- `--no_snapshot_fundamentals` 或抓取失败时，输出**只有表头**的 `fundamentals.csv`。
- `fundamentals.csv` 为空时，流水线应继续运行并产生 warning，不能崩溃。
- **不允许**为了通过 Critic 而伪造 `announce_date`。

`fetch_metadata.json` 的 `fundamentals_limitation` 字段明确说明该限制。

---

## 9. mootdx 与 Sina fallback 的关系

- mootdx 是可选依赖（TCP 7709 通达信行情）。**不强制安装** mootdx。
- mootdx 不存在时，参考项目的 `_load_ohlcv_astock` 内部自动走 Sina HTTP fallback。
- 适配器调用 `_load_ohlcv_astock`；若其抛错，再直接调用 `_sina_kline_fallback`。
- metadata 的 `ohlcv_source_by_ticker` 如实记录来源标签；**无法确认实际是 mootdx 还是 Sina 时记 `unknown` / `internal_fallback`，不猜测**。

---

## 10. 与 Stage 1-7 的关系

- **数据入口复用**：真实数据抓取后输出到 `data/real_market`，通过 `run_all.py --input_dir data/real_market` 或 `run_fetch_real_data.py --run_pipeline` 复用现有六阶段 workflow，**不改 Stage 1-6 核心数据处理逻辑**。
- **空基本面兼容**（`executor.py`）：某 ticker 无基本面时补 `announce_date(NaT)` + pe/pb/roe(NA) 列，保证 panel 始终含 announce_date 列。
- **Critic 防御**（`critic.py`）：announce_date 列缺失时区分两种情况——无基本面值则 warning（正常），有基本面值却无 announce_date 则 failed（防时间泄漏）。
- **无需 Repair 时的 no-op 产物**（`pipeline_runner.py` + `report_generator.py`）：initial critic 未失败时生成 no-op repair artifacts（prepared→repaired 复制、repair_plan/log/report 说明"无需修复"、initial validation 复制为 repaired validation），让 Final Report 正常生成。区分两种 no-op：`no_repair_needed`（initial 未失败）与 `repair_disabled`（initial failed + --no_repair，最终仍 failed）。Final Report 动态读取实际结果，不硬编码行数。
- v3 已移除合成样例数据与自动生成逻辑；正式运行只读真实市场数据。

---

## 11. Stage 12：作为 Agent 工具复用

Stage 12 把本适配器包装成 Agent 领域工具 `fetch_real_market_data`（见
`src/agent_tools/pipeline_tools.py`），让自然语言 Agent 能"先抓取再 configure"：

- 工具直接复用 `RealDataFetchConfig` + `fetch_real_data`，**不复制抓取实现，不通过
  subprocess 调 `run_fetch_real_data.py`**。
- 抓取产物写入当前 run 的 `run_root/raw_data/`（路径边界检查，绝不覆盖
  `data/real_market`）；`fetch_metadata.json` 含 `snapshot_fundamentals_enabled` 字段。
- 工具校验 A 股代码（6 位数字）、日期格式、`start<=end`、ticker 数量上限（20），
  默认 `snapshot_fundamentals=false`。
- risk level = `guarded`（默认 ASK 审批）；`--auto_approve_data_fetch` 只自动批准
  此工具。
- TradingAgents 路径由 CLI `--tradingagents_path` / 环境变量 / 默认解析，存入
  `AgentContext.tradingagents_path`，LLM 不能从自然语言任意指定。

详见 `docs/stage12_natural_language_data_fetch_and_chinese_report.md`。

---

## 12. 下一阶段计划

- 真实数据源已接入（当前阶段完成）；Stage 12 已将其暴露为自然语言 Agent 工具。
- Multi Planner Voting / LLM Planner/Critic/Repair / baseline comparison 仍为后续阶段。
- 当前阶段**不训练模型、不输出投资建议、不连接真实券商交易系统、不做 Streamlit、不做多 Agent 投票**。
