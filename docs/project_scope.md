# 金融表格数据 Analysis-Ready Workflow Agent：第一阶段 Data Profiler

## 1. 项目目标

构建一个以"数据准备"为核心的 workflow agent，把原始金融/券商业务表格（行情、成交、财务、行业、交易日历等）自动**剖析 → 清洗 → 校验**，最终生成可用于分析建模的 **analysis-ready table（宽表）**。

本项目与 NTU clinical table capstone 同构：金融数据准备的痛点与临床数据准备一一对应，方法论可双向迁移。

## 2. 当前只做 Data Profiler

第一阶段范围严格限定为：

```
raw financial tables → Data Profiler → profile.json → profile_report.md
```

- 纯确定性 Python/Pandas 实现，**不调用任何 LLM API**。
- 不依赖真实 Agent 框架，先产出结构化数据画像，为后续 Planner Agent 提供输入。
- 可完全离线运行，使用模拟数据验证 workflow。

## 3. 不做真实投资建议

- 本项目对标"临床 analysis-ready cohort table"——临床 capstone 的核心是把脏数据加工成可建模宽表，而非诊断或开药。
- 金融同构项目因此只做数据准备，**不做选股、择时、收益预测、投资组合建议**。
- 数据准备是确定性、可审计的；投资建议涉及预测与决策，不确定性高、合规风险大，不在本阶段范围。

## 4. 不连接真实生产系统

- 不连接真实券商系统、行情接口、交易接口。
- 不读取真实生产数据库。
- 当前所有数据均为 `generate_sample_data.py` 生成的模拟数据，仅用于验证 workflow 逻辑。

## 5. 不追求预测收益率

- 不构建收益率预测模型。
- 不做回测。
- 不做策略评估。
- 产出物是"干净的宽表"，不是"预测结果"。

## 6. 当前模拟数据用于验证 workflow

模拟数据故意注入以下"脏数据"特征，用于验证 profiler 的检测能力：

- 缺失值（price/volume/fundamentals 均有）
- 重复 (date, ticker) 主键（price.csv）
- 字段口径不一致（price 用 `trade_date`/`ticker`，volume 用 `date`/`stock_code`）
- 财务公告滞后（fundamentals 同时有 `report_date` 与 `announce_date`）
- 行业缺失/拼写异常（industry.csv）
- price 与 volume 覆盖不一致（部分 key 在 volume 中缺失）
- 交易日历含非交易日（calendar.csv）

## 7. 后续如何迁移到临床 clinical table capstone

金融与临床数据准备的痛点同构，方法论可迁移：

| 金融场景 | 临床场景 | 共性问题 |
|---|---|---|
| 金融未来函数（用未来才知道的财务数据） | 临床时间泄漏（用入组后才知道的检验结果） | 时间因果性 / look-ahead bias |
| 金融字段口径不一致（`trade_date` vs `date`、`ticker` vs `stock_code`） | 临床编码体系不一致（ICD 版本、科室编码差异） | 字段语义统一 |
| 金融交易日错位（非交易日、停牌） | 临床事件时间错位（入院/出院/手术时间口径） | 时间对齐 |
| 金融建模宽表（行情+财务+行业拼成 panel） | 临床 analysis-ready cohort table（人口学+检验+诊断拼成队列表） | 多源拼宽表 |

迁移路径：把 Data Profiler 的检测规则（日期列、ID 列、缺失、重复、跨表不一致、未来函数提示）抽象成通用接口，后续可同时服务金融与临床两类数据。
