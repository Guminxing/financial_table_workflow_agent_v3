# 归档：分阶段开发过程记录

本目录保存项目的**开发过程记录**，按 Stage 时间顺序组织。内容已合并进当前的主文档：

- LLM Agent 部分 → [../LLM_AGENT.md](../LLM_AGENT.md)
- 确定性 Pipeline 部分 → [../PIPELINE.md](../PIPELINE.md)
- 项目总览 → [../../README.md](../../README.md)

> **这些文档描述的是各阶段完成时的状态，可能与当前代码不符。**
> 需要了解当前实现，请看上面三份主文档；本目录仅用于回溯"当时为什么这么做"。

## 内容

| 文件 | 阶段 | 主题 |
|---|---|---|
| `project_scope.md` | Stage 1 期 | 最初的项目范围界定（Data Profiler 阶段） |
| `stage2_workflow_planner.md` | 2 | Workflow Planner |
| `stage3_code_executor.md` | 3 | Code Executor |
| `stage4_validity_critic.md` | 4 | Validity Critic |
| `stage5_remediation_loop.md` | 5 | 有界多轮 Remediation Loop |
| `stage6_report_generator.md` | 6 | Final Report Generator |
| `stage7_agent_shell.md` | 7 | One-Click Runner + 交互式 Agent Shell |
| `stage8_real_data_adapter.md` | 8 | 项目内置真实 A 股数据源 |
| `stage9_agent_runtime_mvp.md` | 9 | Agent Runtime MVP（Fake Model 驱动） |
| `stage10_policy_and_approval.md` | 10 | PolicyEngine + allow/ask/deny + 进程内审批恢复 |
| `stage11_natural_language_demo.md` | 11 | 接入真实 LLM + 自然语言 CLI |
| `stage12_natural_language_data_fetch_and_chinese_report.md` | 12 | 自然语言抓取真实数据 + 中文报告 |
| `project_overview_zh.md` | 六阶段期 | 面向导师的项目总说明 |
| `project_summary.md` | Stage 12 期 | 项目总结 |
| `NATURAL_LANGUAGE_LLM_AGENT_DEPLOYMENT_GUIDE_ZH.md` | Stage 12 期 | 自然语言 LLM Agent 部署与运行指南 |

## 已知过期点（勿照此理解当前系统）

归档时发现的、与当前实现直接矛盾的表述，列在这里以免误导：

- `project_overview_zh.md` 称"全部六阶段均为确定性 baseline，**不调用任何 LLM API**"，且只描述六阶段。
  实际上 Stage 9–12 已引入 Agent Runtime 与真实 LLM，当前为七阶段 Pipeline + LLM Agent。
  文中的闭环数字（300 行 → 298 行等）来自已被移除的**合成数据**时代，不对应任何当前产物。
- `project_scope.md` 停留在 Stage 1，并提到迁移到"临床 clinical table capstone"——非当前项目方向。
- `stage7_agent_shell.md` 有"为什么暂时不接入真实市场数据"一节，已被 Stage 8 推翻。
- `stage9_agent_runtime_mvp.md` 描述 **10 个**工具且明确"不接入真实 LLM"——Stage 11 已接入，
  Stage 12 增至 **11 个**工具（新增 `fetch_real_market_data`）。
- `project_summary.md` 记录 **145 项**测试，`stage11` 记录 102/145 项，`stage12` 记录 191 项——
  当前实际为 **199 项**。
- `NATURAL_LANGUAGE_LLM_AGENT_DEPLOYMENT_GUIDE_ZH.md` 把项目主目录写死为 `D:\dwzq\financial_table_workflow_agent_v3`，
  该路径不具可移植性。当前主文档一律使用相对路径。
- 各 Stage 文档末尾的"下一阶段计划"多数**已经实现**（PolicyEngine、真实模型适配器、`chat_agent.py` 等），
  不要当作待办事项阅读。
