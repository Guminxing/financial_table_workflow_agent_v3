# Stage 8：项目内置真实 A 股数据源

## 1. 目标

本阶段为 Financial Table Workflow Agent 提供独立的真实市场数据能力。数据获取、
缓存、CSV 转换和元数据记录都在本仓库内完成，运行时不加载、调用或修改其他 Agent
项目。

输出契约保持不变：

- `price.csv`：`trade_date,ticker,open,high,low,close`
- `volume.csv`：`date,stock_code,volume,turnover`
- `fundamentals.csv`：`report_date,announce_date,ticker,pe,pb,roe`
- `industry.csv`：`ticker,industry_name`
- `calendar.csv`：`date,is_trading_day`
- `fetch_metadata.json`：来源、时间边界、行数、警告和错误

## 2. 架构

```text
run_fetch_real_data.py / fetch_real_market_data tool
  → real_data_adapter.fetch_real_data
  → data_sources.astock.AStockDataSource
       ├─ 东方财富 HTTP：日频 OHLCV（主源）
       ├─ 新浪 HTTP：日频 OHLCV（fallback）
       ├─ 腾讯 HTTP：当前 PE/PB 快照
       └─ 东方财富 HTTP：当前行业标签
  → 五张 CSV + fetch_metadata.json
  → PipelineRunner
```

职责边界：

- `data_sources/astock.py` 只负责 HTTP、解析、标准化和隔离缓存。
- `real_data_adapter.py` 负责五表数据契约、时间点约束和抓取元数据。
- 东方财富日线成交量从“手”统一换算为“股”，与新浪回退和 `volume.csv` 契约一致；
  `fetch_metadata.json` 明确记录 `volume_unit: shares`。
- Agent 工具只负责权限审批、run 路径隔离和结构化 `ToolResult`。
- Pipeline 不直接访问网络。

## 3. 独立性

项目不再包含以下运行时行为：

- `sys.path` 注入另一个仓库；
- `importlib` 动态加载其他 Agent 模块；
- `--tradingagents_path` 或外部仓库环境变量；
- 进程级共享缓存环境变量；
- 调用外部项目的下划线私有函数。

部分 ticker 规范化、腾讯响应解析和新浪 fallback 逻辑由 Apache-2.0 项目
TradingAgents-Astock 的相关代码改造而来，许可证和改造说明见仓库 `NOTICE` 与
`third_party/licenses/Apache-2.0.txt`。这些代码已经成为本项目内部模块，不构成
运行时依赖。

## 4. 缓存隔离

- 独立 CLI 默认缓存：`<output_dir>/cache/`。
- Agent 模式默认缓存：`<run_root>/raw_data/cache/`。
- 缓存文件按 ticker 和日期区间命名。
- 不写全局 `outputs/cache`，不同 run 不共享可变缓存。

## 5. 使用方法

仅抓取：

```powershell
python -B src/run_fetch_real_data.py `
  --tickers 600519,000001 `
  --start_date 2024-01-01 `
  --end_date 2024-06-30 `
  --output_dir data/real_market `
  --no_snapshot_fundamentals
```

抓取后运行完整 Pipeline：

```powershell
python -B src/run_fetch_real_data.py `
  --tickers 600519,000001 `
  --start_date 2024-01-01 `
  --end_date 2024-06-30 `
  --output_dir data/real_market `
  --no_snapshot_fundamentals `
  --run_pipeline `
  --output_root outputs_real
```

可用 `--cache_dir` 显式指定缓存目录；Agent 工具不会接受任意缓存路径，始终写入
当前 run。

## 6. 基本面时间点约束

腾讯接口提供的是当前 PE/PB 快照，不是历史 point-in-time 基本面库：

- 默认的自然语言抓取工具设置 `snapshot_fundamentals=false`；
- 快照关闭或抓取失败时，`fundamentals.csv` 只有表头；
- 如果启用快照，`announce_date` 使用真实抓取日期；
- 不使用用户输入的历史 `end_date` 伪造公告日期；
- 不把当前快照复制到历史区间。

## 7. 错误与降级

- 东方财富日线失败或返回空数据时，尝试新浪日线 fallback。
- 单 ticker 失败记录在 `per_ticker_errors`；其他 ticker 可继续。
- 行业获取失败写 `unknown` 并记录 warning，不阻断 OHLCV。
- 全部 ticker 无有效价格时，工具返回 `FETCH_NO_USABLE_DATA`。
- 不生成随机或合成数据作为静默回退。

## 8. 测试

`tests/test_astock_data_source.py` 使用注入的假 HTTP Session 覆盖：

- ticker 规范化与路径字符拒绝；
- 东方财富 K 线解析；
- 新浪 fallback；
- 腾讯快照解析；
- 行业解析；
- 当前输出目录内缓存；
- 运行时代码不含其他 Agent 项目依赖。

Agent 工具和完整流水线继续由 `test_fetch_tool.py`、`test_chat_agent.py` 和其余
集成测试覆盖。自动测试不访问真实网络；发布前另做小区间真实数据 smoke test。
