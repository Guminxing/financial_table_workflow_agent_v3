# 真实环境测试记录

本目录保存需要真实网络或真实 LLM 的端到端验收记录。它们与仓库中的离线自动化
`unittest` 分开统计；每份记录均说明证据来源、运行参数、结果、警告和未采集信息。

| 测试编号 | 日期 | 场景 | 结果 | 记录 |
|---|---|---|---|---|
| `NL-B-REAL-20260717-001` | 2026-07-17 | 贵州茅台 + 平安银行，多 ticker、2026 年区间 | PASS WITH WARNINGS | [查看](2026-07-17_mode_b_real_market_e2e.md) |
| `NL-B-REAL-20260717-002` | 2026-07-17 | 宁德时代，单 ticker、2025 全年 | PASS WITH WARNINGS | [查看](2026-07-17_mode_b_single_ticker_e2e.md) |
| `NL-B-REAL-20260717-003` | 2026-07-17 | 4 个 ticker、2025 上半年横截面 | **FAIL：日期覆盖不完整** | [查看](2026-07-17_mode_b_multi_ticker_cross_section_e2e.md) |
| `NL-B-REAL-20260717-004` | 2026-07-17 | 贵州茅台 + 五粮液，2024–2025 两年长区间 | PASS WITH WARNINGS | [查看](2026-07-17_mode_b_long_range_e2e.md) |
| `NL-B-REAL-20260717-005` | 2026-07-17 | 贵州茅台 + 平安银行，当前基本面快照时间边界 | PASS WITH WARNINGS | [查看](2026-07-17_mode_b_fundamentals_snapshot_boundary_e2e.md) |
| `NL-B-REAL-20260717-006` | 2026-07-17 | 部分 ticker 失败容错（实际全部失败） | **FAIL：有效 ticker 亦无行情** | [查看](2026-07-17_mode_b_partial_ticker_failure_tolerance_e2e.md) |
| `NL-B-REAL-20260717-007` | 2026-07-17 | 全部抓取失败后的安全停止 | PASS WITH WARNINGS | [查看](2026-07-17_mode_b_no_usable_data_safe_stop_e2e.md) |

运行产物目录（如 `outputs_agent/`）默认不提交 Git。需要严格复现实验时，除本目录中的
Markdown 外，还应保存 Git commit、Python 版本、LLM provider/model、退出码以及关键
产物的 SHA-256。
