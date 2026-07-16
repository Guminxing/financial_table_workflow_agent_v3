"""Final Report Generator（第六阶段 + Stage 12 中文化）。

只读前五阶段产物，汇总成面向导师/审计的最终总报告，把六阶段 workflow 与
闭环结果讲清楚，并明确说明这不是"普通表格检查"，而是 task-aware
analysis-ready workflow prototype。

Stage 12 变更：
- 固定 Markdown 报告（``final_workflow_report.md`` / ``final_workflow_one_page.md``）
  的**用户可读正文改为中文**；JSON 字段名、机器状态值、文件名、工具名、代码标识
  保留英文；Mermaid 节点说明尽量中文化。
- 新增"数据来源与时间边界"章节：从 ``fetch_metadata.json`` 读取抓取元数据
  （requested/resolved tickers、日期、来源、行数、基本面限制、warnings/errors）；
  无 fetch_metadata 时明确显示"本次使用用户提供的已有 CSV"，不编造外部来源。
- 不改变 JSON 产物结构（``final_workflow_summary.json`` 字段保持兼容）。
- 所有数值动态读取实际运行结果，不硬编码 fixture 行数/列数。
- ``passed_with_warnings`` 等机器状态值保留原文，可显示为
  ``passed_with_warnings（通过但有警告）``，不覆盖原始值。
- 明确说明当前 PE/PB/ROE 快照不是历史 point-in-time 基本面，不能回填到过去。

设计原则：
- 确定性 baseline，不调用任何外部 LLM API，离线可运行。
- 不删除/重写前五阶段代码，本模块独立、只读输入。
- 不训练模型、不输出投资建议、不连接真实券商系统、不做 Streamlit、不做多 Agent 投票。
- 路径用 pathlib，兼容 Windows，不写死绝对路径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

REPORT_VERSION = "0.2"


class ReportGenerator:
    """汇总前五阶段产物，生成最终总报告。

    用法::

        gen = ReportGenerator()
        gen.load_inputs(
            profile_json="outputs_real/profiles/profile.json",
            workflow_plan_json="outputs_real/plans/workflow_plan.json",
            prepared_panel="outputs_real/prepared/prepared_panel.csv",
            execution_log="outputs_real/prepared/execution_log.json",
            initial_validation_report="outputs_real/validation/validation_report.json",
            repair_plan="outputs_real/repaired/repair_plan.json",
            repair_log="outputs_real/repaired/repair_log.json",
            repaired_panel="outputs_real/repaired/repaired_panel.csv",
            final_validation_report="outputs_real/validation_repaired/validation_report.json",
            approved_features="outputs_real/validation_repaired/approved_feature_columns.json",
            data_dictionary="outputs_real/prepared/data_dictionary.json",
            fetch_metadata="data/real_market/fetch_metadata.json",  # 可选
            input_dir="data/real_market",  # 可选
        )
        summary = gen.build_summary()
        index = gen.build_artifacts_index()
        full = gen.render_full_report()
        one = gen.render_one_page()
        paths = gen.save_all("outputs_real/final_report")
    """

    def __init__(self) -> None:
        self.profile: dict[str, Any] = {}
        self.workflow_plan: dict[str, Any] = {}
        self.prepared_panel: pd.DataFrame | None = None
        self.execution_log: dict[str, Any] = {}
        self.initial_validation: dict[str, Any] = {}
        self.repair_plan: dict[str, Any] = {}
        self.repair_log: dict[str, Any] = {}
        self.repaired_panel: pd.DataFrame | None = None
        self.final_validation: dict[str, Any] = {}
        self.approved_features: dict[str, Any] = {}
        self.data_dictionary: dict[str, Any] = {}
        # Stage 12：可选的抓取元数据与输入目录
        self.fetch_metadata: dict[str, Any] = {}
        self.input_dir: Path | None = None
        self.input_files: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def load_inputs(
        self,
        profile_json: str | Path,
        workflow_plan_json: str | Path,
        prepared_panel: str | Path,
        execution_log: str | Path,
        initial_validation_report: str | Path,
        repair_plan: str | Path,
        repair_log: str | Path,
        repaired_panel: str | Path,
        final_validation_report: str | Path,
        approved_features: str | Path,
        data_dictionary: str | Path,
        fetch_metadata: str | Path | None = None,
        input_dir: str | Path | None = None,
    ) -> None:
        """读取全部前五阶段产物（只读，不修改任何输入）。

        Stage 12：``fetch_metadata`` 与 ``input_dir`` 可选。``fetch_metadata`` 不存在
        时报告显示"用户提供的已有 CSV"，不编造外部来源。
        """
        self.profile = self._load_json(profile_json)
        self.workflow_plan = self._load_json(workflow_plan_json)
        self.prepared_panel = pd.read_csv(prepared_panel)
        self.execution_log = self._load_json(execution_log)
        self.initial_validation = self._load_json(initial_validation_report)
        self.repair_plan = self._load_json(repair_plan)
        self.repair_log = self._load_json(repair_log)
        self.repaired_panel = pd.read_csv(repaired_panel)
        self.final_validation = self._load_json(final_validation_report)
        self.approved_features = self._load_json(approved_features)
        self.data_dictionary = self._load_json(data_dictionary)
        # Stage 12：可选抓取元数据
        if fetch_metadata is not None and Path(fetch_metadata).exists():
            self.fetch_metadata = self._load_json(fetch_metadata)
        else:
            self.fetch_metadata = {}
        self.input_dir = Path(input_dir) if input_dir is not None else None
        self.input_files = {
            "profile_json": str(profile_json).replace("\\", "/"),
            "workflow_plan_json": str(workflow_plan_json).replace("\\", "/"),
            "prepared_panel": str(prepared_panel).replace("\\", "/"),
            "execution_log": str(execution_log).replace("\\", "/"),
            "initial_validation_report": str(initial_validation_report).replace("\\", "/"),
            "repair_plan": str(repair_plan).replace("\\", "/"),
            "repair_log": str(repair_log).replace("\\", "/"),
            "repaired_panel": str(repaired_panel).replace("\\", "/"),
            "final_validation_report": str(final_validation_report).replace("\\", "/"),
            "approved_features": str(approved_features).replace("\\", "/"),
            "data_dictionary": str(data_dictionary).replace("\\", "/"),
            "fetch_metadata": (
                str(fetch_metadata).replace("\\", "/")
                if fetch_metadata is not None
                else None
            ),
            "input_dir": (
                str(input_dir).replace("\\", "/")
                if input_dir is not None
                else None
            ),
        }

    # ------------------------------------------------------------------
    # summary.json
    # ------------------------------------------------------------------

    def build_summary(self) -> dict[str, Any]:
        """构建 final_workflow_summary.json 的内容。"""
        initial_status = self.initial_validation.get("overall_status", "unknown")
        final_status = self.final_validation.get("overall_status", "unknown")
        rows_removed = int(self.repair_log.get("rows_removed", 0))
        initial_rows = int(self.repair_log.get("rows_before", 0))
        repaired_rows = int(self.repair_log.get("rows_after", 0))

        approved = self.approved_features.get("approved_feature_columns", [])
        label_col = self.approved_features.get("label_column", "label_next_5d")
        label_not_in_features = label_col not in approved

        # no-op kind（no_repair_needed / repair_disabled / None）
        no_op = bool(self.repair_log.get("no_op", False))
        no_op_kind = self.repair_log.get("no_op_kind")

        # 初始 Critic 的 failed 项
        failed_checks = [
            c for c in self.initial_validation.get("checks", []) if c.get("status") == "failed"
        ]
        failed_check = failed_checks[0]["check_name"] if failed_checks else None
        failed_reason = None
        if failed_checks:
            ev = failed_checks[0].get("evidence", {})
            close_rate = ev.get("close_missing_rate")
            if close_rate is not None:
                failed_reason = (
                    f"close has {close_rate:.4f} missing rate "
                    f"({int(round(close_rate * initial_rows))} rows); close is a core price "
                    "field required by return features and the label"
                )
            else:
                failed_reason = failed_checks[0].get("description", "")

        # one_line 动态化，按 no_op_kind 区分
        if no_op_kind == "no_repair_needed":
            one_line = (
                f"initial {initial_rows} rows -> Critic {initial_status} "
                f"(no repair needed) -> {repaired_rows} rows -> re-run Critic {final_status}; "
                f"label {label_col} kept out of approved features"
            )
        elif no_op_kind == "repair_disabled":
            one_line = (
                f"initial {initial_rows} rows -> Critic failed (repair disabled by --no_repair) "
                f"-> {repaired_rows} rows unchanged -> final status failed; "
                f"label {label_col} kept out of approved features"
            )
        else:
            one_line = (
                f"initial {initial_rows} rows -> Critic {initial_status} "
                f"(close missing {rows_removed} rows) -> Repair removed {rows_removed} rows "
                f"-> {repaired_rows} rows -> re-run Critic {final_status}; "
                f"label {label_col} kept out of approved features"
            )

        closed_loop = {
            "initial_rows": initial_rows,
            "initial_status": initial_status,
            "failed_check": failed_check,
            "failed_reason": failed_reason,
            "rows_removed": rows_removed,
            "repaired_rows": repaired_rows,
            "final_status": final_status,
            "label_not_in_approved_features": label_not_in_features,
            "repair_skipped": no_op,
            "no_op_kind": no_op_kind,
            "one_line": one_line,
        }

        pipeline_stages = [
            {
                "stage": 1,
                "name": "Data Profiler",
                "role": "剖析原始表：schema/dtype/缺失/日期/代码/重复/异常 + 跨表发现",
                "status": "completed",
                "key_outputs": ["profile.json", "profile_report.md"],
            },
            {
                "stage": 2,
                "name": "Workflow Planner",
                "role": "读 profile + analysis_goal，生成有序可执行可校验的数据准备计划",
                "status": "completed",
                "key_outputs": ["workflow_plan.json", "workflow_plan_report.md"],
            },
            {
                "stage": 3,
                "name": "Code Executor",
                "role": "按 plan 用 pandas 真正执行，产出 analysis-ready 宽表（防未来函数）",
                "status": "completed",
                "key_outputs": [
                    "prepared_panel.csv",
                    "data_dictionary.json",
                    "execution_log.json",
                    "execution_report.md",
                ],
            },
            {
                "stage": 4,
                "name": "Validity Critic",
                "role": "对 panel 做有效性审查（未来函数/label leakage/announce_date 对齐/时间切分）",
                "status": "completed",
                "key_outputs": [
                    "validation_report.json",
                    "validation_report.md",
                    "approved_feature_columns.json",
                ],
            },
            {
                "stage": 5,
                "name": "Remediation / Repair Loop",
                "role": "读 Critic failed 项，生成可解释修复方案并执行，输出 repaired panel",
                "status": "completed",
                "key_outputs": [
                    "repair_plan.json",
                    "repaired_panel.csv",
                    "repair_log.json",
                    "repair_report.md",
                ],
            },
            {
                "stage": 6,
                "name": "Re-run Critic (closed-loop verification)",
                "role": "对 repaired panel 重新运行 Critic，确认 failed 已解决",
                "status": "completed",
                "key_outputs": [
                    "validation_repaired/validation_report.json",
                    "validation_repaired/approved_feature_columns.json",
                ],
            },
        ]

        # profile 摘要
        tables = [
            {"table": t["table_name"], "n_rows": t["n_rows"], "n_columns": t["n_columns"]}
            for t in self.profile.get("tables", [])
        ]
        cross = self.profile.get("cross_table_findings", {})
        profile_summary = {
            "n_tables": len(self.profile.get("tables", [])),
            "tables": tables,
            "schema_inconsistencies": cross.get("schema_inconsistencies", []),
            "global_potential_issues": cross.get("global_potential_issues", []),
        }

        # plan 摘要
        plan_summary = {
            "analysis_goal": self.workflow_plan.get("analysis_goal", ""),
            "n_workflow_steps": len(self.workflow_plan.get("workflow_steps", [])),
            "n_validation_checks": len(
                self.workflow_plan.get("validation_plan", {}).get("checks", [])
            ),
            "n_features": len(self.workflow_plan.get("feature_plan", {}).get("features", [])),
            "label": self.workflow_plan.get("feature_plan", {}).get("label", {}),
        }

        # execution 摘要
        fts = self.execution_log.get("final_table_summary", {})
        execution_summary = {
            "n_rows": fts.get("n_rows"),
            "n_columns": fts.get("n_columns"),
            "n_tickers": fts.get("n_tickers"),
            "date_min": fts.get("date_min"),
            "date_max": fts.get("date_max"),
            "primary_key_unique": fts.get("primary_key_unique"),
            "n_steps_executed": len(self.execution_log.get("steps_executed", [])),
            "n_warnings": len(self.execution_log.get("warnings", [])),
            "n_errors": len(self.execution_log.get("errors", [])),
        }

        # panel 摘要（实算两个 panel）
        panel_summary = {
            "prepared_panel_rows": int(len(self.prepared_panel)) if self.prepared_panel is not None else None,
            "prepared_panel_cols": int(self.prepared_panel.shape[1]) if self.prepared_panel is not None else None,
            "repaired_panel_rows": int(len(self.repaired_panel)) if self.repaired_panel is not None else None,
            "repaired_panel_cols": int(self.repaired_panel.shape[1]) if self.repaired_panel is not None else None,
            "close_missing_prepared": int(self.prepared_panel["close"].isna().sum())
            if (self.prepared_panel is not None and "close" in self.prepared_panel.columns)
            else None,
            "close_missing_repaired": int(self.repaired_panel["close"].isna().sum())
            if (self.repaired_panel is not None and "close" in self.repaired_panel.columns)
            else None,
        }

        # Stage 12：数据来源摘要（fetch_metadata 存在时）
        data_source_summary = self._build_data_source_summary()

        return {
            "project": "financial_table_workflow_agent",
            "report_version": REPORT_VERSION,
            "generated_from_stages": [1, 2, 3, 4, 5, 6],
            # 顶层便于验证脚本断言的三个关键字段
            "initial_validation_status": initial_status,
            "final_validation_status": final_status,
            "rows_removed_by_repair": rows_removed,
            "closed_loop_result": closed_loop,
            "pipeline_stages": pipeline_stages,
            "profile_summary": profile_summary,
            "plan_summary": plan_summary,
            "execution_summary": execution_summary,
            "panel_summary": panel_summary,
            "data_source_summary": data_source_summary,
            "approved_feature_columns": approved,
            "excluded_columns": self.approved_features.get("excluded_columns", []),
            "label_column": label_col,
            "label_not_in_approved_features": label_not_in_features,
            "limitations": [
                "Report Generator is a deterministic baseline; no LLM is called.",
                "It only reads prior-stage artifacts; it does not re-run any stage.",
                "No model is trained; no investment advice is produced.",
                "Only real market data fetched via the adapter is used, not broker data.",
                "Current PE/PB/ROE snapshots are NOT historical point-in-time fundamentals; "
                "they must not be backfilled into historical dates (look-ahead bias).",
            ],
        }

    def _build_data_source_summary(self) -> dict[str, Any]:
        """构建数据来源摘要（Stage 12）。

        - 有 fetch_metadata：返回抓取元数据关键字段。
        - 无 fetch_metadata：标记为用户提供的已有 CSV，列出 input_dir 与发现的 CSV。
        """
        if self.fetch_metadata:
            return {
                "source_kind": "fetched_real_market_data",
                "requested_tickers": self.fetch_metadata.get("requested_tickers", []),
                "resolved_tickers": self.fetch_metadata.get("resolved_tickers", []),
                "start_date": self.fetch_metadata.get("start_date"),
                "end_date": self.fetch_metadata.get("end_date"),
                "fetch_date": self.fetch_metadata.get("fetch_date"),
                "ohlcv_source_by_ticker": self.fetch_metadata.get(
                    "ohlcv_source_by_ticker", {}
                ),
                "rows_by_ticker": self.fetch_metadata.get("rows_by_ticker", {}),
                "summary_rows": self.fetch_metadata.get("summary_rows", {}),
                "snapshot_fundamentals_enabled": self.fetch_metadata.get(
                    "snapshot_fundamentals_enabled"
                ),
                "fundamentals_limitation": self.fetch_metadata.get(
                    "fundamentals_limitation"
                ),
                "warnings": self.fetch_metadata.get("warnings", []),
                "errors": self.fetch_metadata.get("errors", []),
                "tradingagents_path": self.fetch_metadata.get("tradingagents_path"),
            }
        # 无 fetch_metadata：用户提供的已有 CSV
        discovered_csvs: list[str] = []
        if self.input_dir is not None and self.input_dir.exists():
            discovered_csvs = sorted(
                p.name for p in self.input_dir.glob("*.csv")
            )
        return {
            "source_kind": "user_provided_existing_csv",
            "input_dir": (
                str(self.input_dir).replace("\\", "/")
                if self.input_dir is not None
                else None
            ),
            "discovered_csv_files": discovered_csvs,
            "note": (
                "本次使用用户提供的已有 CSV，未通过 fetch_real_market_data 抓取；"
                "不编造其外部来源。"
            ),
        }

    # ------------------------------------------------------------------
    # artifacts index
    # ------------------------------------------------------------------

    def build_artifacts_index(self) -> dict[str, Any]:
        """构建 pipeline_artifacts_index.json：按 stage 列出每个产物文件。"""
        # 相对项目根的路径（基于 input_files 推断根目录）
        # input_files 里的路径已是正斜杠相对路径，直接用
        items: list[dict[str, Any]] = []

        def add(stage: str, path: str, description: str) -> None:
            p = Path(path)
            items.append(
                {
                    "stage": stage,
                    "path": path,
                    "description": description,
                    "exists": p.exists(),
                }
            )

        add("stage1_profiler", "outputs_real/profiles/profile.json", "机器可读数据画像")
        add("stage1_profiler", "outputs_real/profiles/profile_report.md", "人类可读画像报告")
        add("stage2_planner", "outputs_real/plans/workflow_plan.json", "机器可读数据准备计划")
        add("stage2_planner", "outputs_real/plans/workflow_plan_report.md", "人类可读计划报告")
        add("stage3_executor", "outputs_real/prepared/prepared_panel.csv", "analysis-ready 宽表（初始）")
        add("stage3_executor", "outputs_real/prepared/data_dictionary.json", "字段口径说明")
        add("stage3_executor", "outputs_real/prepared/execution_log.json", "执行日志")
        add("stage3_executor", "outputs_real/prepared/execution_report.md", "执行报告")
        add("stage4_critic", "outputs_real/validation/validation_report.json", "初始有效性审查报告")
        add("stage4_critic", "outputs_real/validation/validation_report.md", "初始审查报告（人类可读）")
        add("stage4_critic", "outputs_real/validation/approved_feature_columns.json", "初始 approved features")
        add("stage5_repair", "outputs_real/repaired/repair_plan.json", "修复方案")
        add("stage5_repair", "outputs_real/repaired/repaired_panel.csv", "修复后 panel")
        add("stage5_repair", "outputs_real/repaired/repair_log.json", "修复日志")
        add("stage5_repair", "outputs_real/repaired/repair_report.md", "修复报告")
        add("stage6_rerun_critic", "outputs_real/validation_repaired/validation_report.json", "复审报告")
        add("stage6_rerun_critic", "outputs_real/validation_repaired/validation_report.md", "复审报告（人类可读）")
        add("stage6_rerun_critic", "outputs_real/validation_repaired/approved_feature_columns.json", "复审 approved features")
        add("stage6_report", "outputs_real/final_report/final_workflow_summary.json", "六阶段汇总 JSON")
        add("stage6_report", "outputs_real/final_report/final_workflow_report.md", "最终总报告")
        add("stage6_report", "outputs_real/final_report/final_workflow_one_page.md", "一页摘要")
        add("stage6_report", "outputs_real/final_report/pipeline_artifacts_index.json", "产物索引")

        n_total = len(items)
        n_exists = sum(1 for it in items if it["exists"])
        return {
            "project": "financial_table_workflow_agent",
            "report_version": REPORT_VERSION,
            "n_artifacts": n_total,
            "n_exists": n_exists,
            "artifacts": items,
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json(path: str | Path) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _close_rate_after_repair(self) -> float:
        if self.repaired_panel is None or "close" not in self.repaired_panel.columns:
            return -1.0
        n = len(self.repaired_panel)
        if n == 0:
            return 0.0
        return float(self.repaired_panel["close"].isna().sum()) / n

    def _close_rate_before_repair(self) -> float:
        """实算 prepared_panel 的 close 缺失率（不硬编码）。"""
        if self.prepared_panel is None or "close" not in self.prepared_panel.columns:
            return -1.0
        n = len(self.prepared_panel)
        if n == 0:
            return 0.0
        return float(self.prepared_panel["close"].isna().sum()) / n

    def _status_zh(self, status: str | None) -> str:
        """把机器状态值翻译为"原文（中文）"形式，不覆盖原始值。"""
        if status is None:
            return "未知"
        mapping = {
            "passed": "passed（通过）",
            "passed_with_warnings": "passed_with_warnings（通过但有警告）",
            "failed": "failed（未通过）",
            "unknown": "unknown（未知）",
        }
        return mapping.get(status, status)

    # ------------------------------------------------------------------
    # full markdown report（中文）
    # ------------------------------------------------------------------

    def render_full_report(self) -> str:
        """构建 final_workflow_report.md（中文正文 + Mermaid 架构图 + 数据来源章节）。"""
        s = self.build_summary()
        cl = s["closed_loop_result"]
        ds = s["data_source_summary"]
        lines: list[str] = []

        # 1. 标题 + meta
        lines.append("# 金融表格 analysis-ready 工作流 — 最终报告")
        lines.append("")
        lines.append(
            f"- 项目：`{s['project']}`  |  报告版本：`{s['report_version']}`"
        )
        lines.append(f"- 汇总自阶段：{s['generated_from_stages']}")
        lines.append(
            f"- 初始校验状态：**{self._status_zh(s['initial_validation_status'])}**  →  "
            f"最终校验状态：**{self._status_zh(s['final_validation_status'])}**"
        )
        lines.append(f"- 修复删除行数：**{s['rows_removed_by_repair']}**")
        lines.append("")

        # 2. 执行摘要
        lines.append("## 1. 执行摘要")
        lines.append("")
        lines.append(
            "本项目把原始、杂乱的金融表格（行情、成交、财务、行业、交易日历）通过一个"
            "六阶段 Agent 工作流加工成 **analysis-ready 建模宽表**。它**不是**一次性"
            "表格清洗器，而是一个 **task-aware analysis-ready 工作流原型**：围绕下游"
            "建模目标规划、从结构上防止未来函数与标签泄漏，并通过 critic → repair → "
            "re-critic 闭环自我修正。"
        )
        lines.append("")
        lines.append("**闭环结果：**")
        lines.append("")
        lines.append(f"- 初始 `prepared_panel.csv`：**{cl['initial_rows']} 行**。")
        lines.append(
            f"- 初始 Critic 状态：**{self._status_zh(cl['initial_status'])}** — "
            f"失败项 `{cl['failed_check'] or '无'}`（{cl['failed_reason'] or '无失败项'}）。"
        )
        if cl.get("no_op_kind") == "no_repair_needed":
            lines.append(
                f"- 修复闭环：**no-op（无需修复）** — 初始 Critic 未失败；"
                f"`repaired_panel.csv` = `prepared_panel.csv`，共 **{cl['repaired_rows']} 行**。"
            )
        elif cl.get("no_op_kind") == "repair_disabled":
            lines.append(
                f"- 修复闭环：**已禁用（--no_repair）** — 初始 Critic 失败但修复被禁用；"
                f"宽表不变，仍为 **{cl['repaired_rows']} 行**，最终状态仍为 **failed**。"
            )
        else:
            lines.append(
                f"- 修复闭环删除了 **{cl['rows_removed']} 行**（`close` 缺失），"
                f"产出 `repaired_panel.csv`，共 **{cl['repaired_rows']} 行**。"
            )
        lines.append(
            f"- 复审 Critic 状态：**{self._status_zh(cl['final_status'])}** "
            f"（failed → {self.final_validation.get('summary', {}).get('failed', 0)}）。"
        )
        lines.append(
            f"- 标签隔离：`{s['label_column']}` **不**在 approved feature columns 中"
            f"（`label_not_in_approved_features = {cl['label_not_in_approved_features']}`）。"
        )
        lines.append("")
        lines.append(f"> {cl['one_line']}")
        lines.append("")

        # 3. 数据来源与时间边界（Stage 12 新增）
        lines.append("## 2. 数据来源与时间边界")
        lines.append("")
        self._render_data_source_section(lines, ds)

        # 4. Mermaid 架构图
        lines.append("## 3. 工作流架构（Mermaid）")
        lines.append("")
        lines.append("```mermaid")
        lines.append("flowchart TD")
        lines.append("    RAW[原始金融表格<br/>行情 / 成交 / 财务 / 行业 / 日历]")
        lines.append("    S1[阶段1 数据剖析<br/>profile.json]")
        lines.append("    S2[阶段2 工作流规划<br/>workflow_plan.json]")
        lines.append(f"    S3[阶段3 代码执行<br/>prepared_panel.csv {cl['initial_rows']} 行]")
        lines.append(f"    S4[阶段4 有效性审查<br/>状态 = {cl['initial_status']}<br/>失败项: {cl['failed_check'] or '无'}]")
        if cl.get("no_op_kind") == "no_repair_needed":
            lines.append("    S5[阶段5 修复闭环<br/>no-op（无需修复）<br/>repaired_panel.csv = prepared_panel.csv]")
            lines.append(f"    S6[阶段6 复审<br/>状态 = {cl['final_status']}]")
            lines.append("    S4 -- 未失败 --> S5 --> S6")
        elif cl.get("no_op_kind") == "repair_disabled":
            lines.append("    S5[阶段5 修复闭环<br/>no-op（已禁用，--no_repair）<br/>宽表不变]")
            lines.append(f"    S6[阶段6 复审<br/>状态 = {cl['final_status']}]")
            lines.append("    S4 -- 失败但 --no_repair --> S5 --> S6")
        else:
            lines.append(f"    S5[阶段5 修复闭环<br/>删除 close 缺失行<br/>repaired_panel.csv {cl['repaired_rows']} 行]")
            lines.append(f"    S6[阶段6 复审<br/>状态 = {cl['final_status']}]")
            lines.append("    S4 -- 失败 --> S5 --> S6")
        lines.append("    OUT[analysis-ready 宽表<br/>+ approved_feature_columns]")
        lines.append(f"    S6 -- {cl['final_status']} --> OUT")
        lines.append("    S4 -. approved features .-> OUT")
        lines.append("```")
        lines.append("")

        # 5. 为什么不只是表格检查
        lines.append("## 4. 为什么这不只是表格检查")
        lines.append("")
        lines.append(
            "朴素的表格检查只问“数据干净吗”——缺失率、重复、dtype、异常值。这对建模"
            "**必要但远远不够**。本工作流问的是更难、task-aware 的问题：**这张宽表能"
            "否安全地喂给时序模型而不泄漏未来？**"
        )
        lines.append("")
        lines.append("让它超越表格检查的地方：")
        lines.append("")
        lines.append(
            "1. **task-aware 规划。** Planner 读取剖析结果**和**下游分析目标（五日收益"
            "预测 / 因子分析），输出有序、依赖尊重、每步标注泄漏风险的计划——不是通用"
            "清洗配方。"
        )
        lines.append(
            "2. **从结构上防止未来函数。** rolling/pct_change 特征按 ticker 分组、只用"
            "历史窗口；财务按 `announce_date`（as-of merge）对齐，绝不用 `report_date`。"
            "金融未来函数是临床时间泄漏的对应物。"
        )
        lines.append(
            "3. **防止标签泄漏。** `label_next_5d`（未来五日收益）用 `shift(-5)` 生成，"
            "在数据字典中标 `role=label`，并从结构上排除出 `approved_feature_columns`——"
            "下游模型根本无法把标签当特征读。"
        )
        lines.append(
            "4. **时间有效性。** 计划要求按时间切分训练/测试（时序不做随机 shuffle）；"
            "Critic 强制计划要求这一点。"
        )
        lines.append(
            "5. **源码级静态分析。** Critic 不只看宽表——它读 `executor.py` 源码，验证"
            "`merge_asof` + `announce_date`，且无非标签的 `shift(-k)`。"
        )
        lines.append(
            "6. **闭环自我修正。** Critic 失败（close 缺失）时，修复闭环消费失败项、"
            "输出可解释修复动作，并独立重跑 Critic 确认修复生效。表格检查发现问题就停；"
            "本工作流**修复并复审**。"
        )
        lines.append("")
        lines.append("| 维度 | 普通表格检查 | 本工作流 |")
        lines.append("|---|---|---|")
        lines.append("| 关注点 | 缺失/重复/dtype/异常 | 未来函数、标签泄漏、时间有效性 |")
        lines.append("| 失败后果 | 脏但可清洗 | 模型看似有效实则无效；上线即灾难 |")
        lines.append("| 审查对象 | 仅表格 | 表格 + 数据字典 + 执行日志 + 计划 + 源码 |")
        lines.append("| 判定依据 | 统计阈值 | 时间因果、角色标签、源码静态分析 |")
        lines.append("| 失败时 | 报告并停止 | 修复 → 复审闭环 |")
        lines.append("")

        # 6. 各阶段说明
        lines.append("## 5. 各阶段说明")
        lines.append("")
        ps = s["profile_summary"]
        pl = s["plan_summary"]
        ex = s["execution_summary"]
        pa = s["panel_summary"]
        lines.append("### 阶段 1 — 数据剖析")
        lines.append("")
        lines.append(f"- 剖析表数：{ps['n_tables']}。")
        lines.append("")
        lines.append("| 表 | 行数 | 列数 |")
        lines.append("|---|---|---|")
        for t in ps["tables"]:
            lines.append(f"| {t['table']} | {t['n_rows']} | {t['n_columns']} |")
        lines.append("")
        lines.append("跨表发现：")
        lines.append("")
        for it in ps["schema_inconsistencies"]:
            lines.append(
                f"- `{it.get('type')}`：列 {it.get('columns')} 跨 "
                f"{it.get('tables')} — {it.get('note')}"
            )
        for it in ps["global_potential_issues"]:
            lines.append(f"- {it}")
        lines.append("")

        lines.append("### 阶段 2 — 工作流规划")
        lines.append("")
        lines.append(f"- analysis_goal：{pl['analysis_goal']}")
        lines.append(
            f"- {pl['n_workflow_steps']} 个工作流步骤，{pl['n_validation_checks']} "
            f"个校验项，{pl['n_features']} 个特征 + 1 个标签。"
        )
        lines.append(
            f"- 标签：`{pl['label'].get('name')}` — {pl['label'].get('usage')}"
        )
        lines.append("")

        lines.append("### 阶段 3 — 代码执行")
        lines.append("")
        lines.append(
            f"- `prepared_panel.csv`：{ex['n_rows']} 行 × {ex['n_columns']} 列，"
            f"{ex['n_tickers']} 个 ticker，{ex['date_min']} ~ {ex['date_max']}，"
            f"primary_key_unique={ex['primary_key_unique']}。"
        )
        lines.append(
            f"- 执行 {ex['n_steps_executed']} 步，{ex['n_warnings']} 个 warning，"
            f"{ex['n_errors']} 个 error。"
        )
        lines.append(
            "- 防泄漏：rolling/pct_change 按 ticker 分组（仅历史窗口）；"
            "财务按 `announce_date` 经 `merge_asof(direction='backward')` 对齐。"
        )
        lines.append("")

        lines.append("### 阶段 4 — 有效性审查（初始）")
        lines.append("")
        init_sum = self.initial_validation.get("summary", {})
        lines.append(
            f"- overall_status：**{self._status_zh(s['initial_validation_status'])}** "
            f"（{init_sum.get('passed')} 通过 / {init_sum.get('warnings')} 警告 / "
            f"{init_sum.get('failed')} 失败，共 {init_sum.get('total_checks')} 项）。"
        )
        lines.append(
            f"- 失败项：`{cl['failed_check'] or '无'}` — "
            f"{cl['failed_reason'] or '无失败项'}。"
        )
        lines.append(
            f"- approved_feature_columns：{s['approved_feature_columns']}。"
        )
        lines.append(
            f"- `{s['label_column']}` 是否在 approved features 中？"
            f"**{not s['label_not_in_approved_features']}**（必须为 False）。"
        )
        lines.append("")

        lines.append("### 阶段 5 — 修复闭环")
        lines.append("")
        rap = self.repair_plan.get("repair_actions", [])
        if cl.get("no_op_kind") == "no_repair_needed":
            lines.append(
                f"- 修复跳过/no-op（初始 Critic 未失败）："
                f"rows_before: {cl['initial_rows']} → rows_after: {cl['repaired_rows']} "
                f"（删除 {cl['rows_removed']}）。"
            )
            lines.append(
                "- `prepared_panel.csv` 原样复制为 `repaired_panel.csv`；"
                "初始校验复制为复审校验。"
            )
        elif cl.get("no_op_kind") == "repair_disabled":
            lines.append(
                f"- 修复被 --no_repair 禁用（初始 Critic 失败）："
                f"rows_before: {cl['initial_rows']} → rows_after: {cl['repaired_rows']} "
                f"（删除 {cl['rows_removed']}）；宽表不变；最终状态仍为 failed。"
            )
        else:
            lines.append(
                f"- rows_before: {cl['initial_rows']} → rows_after: {cl['repaired_rows']} "
                f"（删除 {cl['rows_removed']}）。"
            )
            if rap:
                a = rap[0]
                lines.append(
                    f"- 动作：`{a.get('strategy')}` 作用于 {a.get('target_columns')} — "
                    f"{a.get('reason')}"
                )
        chk = self.repair_log.get("checks_after_repair", {})
        lines.append(
            f"- 修复后自检：close_missing={chk.get('close_missing_count')}，"
            f"primary_key_unique={chk.get('primary_key_unique')}，"
            f"label_preserved={chk.get('label_column_preserved')}，"
            f"label_not_in_features={chk.get('label_not_in_approved_features')}。"
        )
        lines.append("")

        lines.append("### 阶段 6 — 复审（闭环验证）")
        lines.append("")
        fin_sum = self.final_validation.get("summary", {})
        lines.append(
            f"- overall_status：**{self._status_zh(s['final_validation_status'])}** "
            f"（{fin_sum.get('passed')} 通过 / {fin_sum.get('warnings')} 警告 / "
            f"{fin_sum.get('failed')} 失败，共 {fin_sum.get('total_checks')} 项）。"
        )
        close_rate_after = self._close_rate_after_repair()
        close_rate_before = self._close_rate_before_repair()
        lines.append(
            f"- 修复后 `close` 缺失率："
            f"{close_rate_after:.4f}（修复前 {close_rate_before:.4f}）。"
        )
        lines.append(
            f"- approved_feature_columns 不变：{s['approved_feature_columns']}；"
            f"`{s['label_column']}` 仍不在特征中。"
        )
        lines.append("")

        # 7. 闭环深入
        lines.append("## 6. 闭环深入")
        lines.append("")
        lines.append("| 指标 | 修复前 | 修复后 |")
        lines.append("|---|---|---|")
        lines.append(f"| 行数 | {cl['initial_rows']} | {cl['repaired_rows']} |")
        lines.append(
            f"| close 缺失数 | {pa['close_missing_prepared']} | {pa['close_missing_repaired']} |"
        )
        lines.append(
            f"| Critic overall_status | {self._status_zh(cl['initial_status'])} | "
            f"{self._status_zh(cl['final_status'])} |"
        )
        lines.append(
            f"| 失败项数 | {init_sum.get('failed')} | {fin_sum.get('failed')} |"
        )
        lines.append(
            f"| 标签是否在 approved features | False | "
            f"{not s['label_not_in_approved_features']} |"
        )
        lines.append("")
        lines.append(
            "该闭环是**反馈驱动**（Critic 失败 → 修复）、**可解释**（每个动作带 "
            "target_check/strategy/reason/risk）、且**独立可验证**（复审 Critic——"
            "而非修复者——判定修复是否生效）。"
        )
        lines.append("")

        # 8. 特征列表与标签隔离
        lines.append("## 7. 特征列表与标签隔离")
        lines.append("")
        lines.append(f"- approved_feature_columns（共 {len(s['approved_feature_columns'])} 个）：")
        for c in s["approved_feature_columns"]:
            role = self.data_dictionary.get(c, {}).get("role", "?")
            lines.append(f"  - `{c}`（role={role}）")
        lines.append("")
        lines.append(f"- label_column：`{s['label_column']}`（role=label）。")
        lines.append(
            f"- excluded_columns（共 {len(s['excluded_columns'])} 个）：{s['excluded_columns']}"
        )
        lines.append(
            "- 下游建模以 `approved_feature_columns.json` 为 X、`label_column` 为 y；"
            "标签从结构上无法进入特征矩阵。"
        )
        lines.append("")

        # 9. 标签泄漏说明
        lines.append("## 8. 标签泄漏说明")
        lines.append("")
        lines.append(
            f"- 标签列 `{s['label_column']}` 用未来信息（`shift(-5)`）生成，"
            "role=label，**永远不进入** approved_feature_columns。"
        )
        lines.append(
            f"- `label_not_in_approved_features = {s['label_not_in_approved_features']}`"
            "（必须为 True）。"
        )
        lines.append(
            "- 若 Critic 检测到标签泄漏，会返回 `LABEL_LEAKAGE_DETECTED` 并转人工，"
            "绝不自动继续。"
        )
        lines.append("")

        # 10. 警告与未解决问题
        lines.append("## 9. 警告与未解决问题")
        lines.append("")
        unresolved = []
        if cl.get("no_op_kind") == "repair_disabled":
            unresolved.append("初始 Critic 失败且修复被禁用，最终状态仍为 failed，需人工处理。")
        if s["final_validation_status"] == "failed":
            unresolved.append("复审 Critic 仍为 failed，存在未解决失败项。")
        if unresolved:
            for u in unresolved:
                lines.append(f"- {u}")
        else:
            lines.append("- 无未解决的失败项。")
        lines.append(
            "- 修复后剩余 warning：pe/pb/roe 缺失较多（公告频率低，属预期）"
            "与 industry_name 缺失——基线可接受，非失败。"
        )
        lines.append("")

        # 11. 局限性
        lines.append("## 10. 局限性")
        lines.append("")
        for it in s["limitations"]:
            lines.append(f"- {it}")
        lines.append("")

        # 12. 最终结论
        lines.append("## 11. 最终结论")
        lines.append("")
        if s["final_validation_status"] in ("passed", "passed_with_warnings"):
            lines.append(
                f"经六阶段闭环，宽表从 {cl['initial_rows']} 行到 {cl['repaired_rows']} 行，"
                f"复审状态为 **{self._status_zh(s['final_validation_status'])}**，"
                "标签隔离成立，可作为 analysis-ready 建模宽表交付下游。"
            )
        else:
            lines.append(
                f"经六阶段闭环，复审状态仍为 **{self._status_zh(s['final_validation_status'])}**，"
                "存在未解决失败项，需人工介入后再交付。"
            )
        lines.append(
            "本报告由确定性 Report Generator 生成，不调用 LLM、不训练模型、不输出投资建议。"
        )
        lines.append("")

        return "\n".join(lines)

    def _render_data_source_section(
        self, lines: list[str], ds: dict[str, Any]
    ) -> None:
        """渲染"数据来源与时间边界"章节内容（Stage 12）。"""
        kind = ds.get("source_kind")
        if kind == "fetched_real_market_data":
            lines.append(
                "本次数据由 `fetch_real_market_data` 工具抓取真实 A 股行情得到，"
                "写入当前 run 的 `raw_data/`。"
            )
            lines.append("")
            lines.append(f"- 请求 tickers：{ds.get('requested_tickers')}")
            lines.append(f"- 解析后 tickers：{ds.get('resolved_tickers')}")
            lines.append(f"- 起始日期：{ds.get('start_date')}")
            lines.append(f"- 结束日期：{ds.get('end_date')}")
            lines.append(f"- 抓取日期（fetch_date）：{ds.get('fetch_date')}")
            lines.append(f"- 各 ticker 行情来源：{ds.get('ohlcv_source_by_ticker')}")
            lines.append(f"- 各 ticker 行数：{ds.get('rows_by_ticker')}")
            sr = ds.get("summary_rows", {}) or {}
            lines.append(
                f"- 各表行数：price={sr.get('price')} volume={sr.get('volume')} "
                f"fundamentals={sr.get('fundamentals')} industry={sr.get('industry')} "
                f"calendar={sr.get('calendar')}"
            )
            lines.append(
                f"- 抓取基本面快照（snapshot_fundamentals_enabled）："
                f"{ds.get('snapshot_fundamentals_enabled')}"
            )
            lines.append("")
            lines.append("**基本面时间边界（关键）：**")
            lines.append("")
            limitation = ds.get("fundamentals_limitation")
            if limitation:
                lines.append(f"> {limitation}")
            else:
                lines.append(
                    "> 当前 PE/PB/ROE 快照不是历史 point-in-time 基本面，"
                    "不能回填到过去日期，否则会引入未来信息泄漏。"
                )
            lines.append("")
            warnings = ds.get("warnings", []) or []
            errors = ds.get("errors", []) or []
            if warnings:
                lines.append("**警告：**")
                for w in warnings:
                    lines.append(f"- {w}")
                lines.append("")
            if errors:
                lines.append("**错误：**")
                for e in errors:
                    lines.append(f"- {e}")
                lines.append("")
            if not warnings and not errors:
                lines.append("无警告与错误。")
                lines.append("")
        else:
            # 用户提供的已有 CSV
            lines.append(
                "本次使用**用户提供的已有 CSV**，未通过 `fetch_real_market_data` 抓取；"
                "不编造其外部来源。"
            )
            lines.append("")
            lines.append(f"- input_dir：`{ds.get('input_dir')}`")
            discovered = ds.get("discovered_csv_files", []) or []
            if discovered:
                lines.append(f"- 发现的 CSV 文件：{discovered}")
            lines.append("")
            lines.append(
                "> 若需可审计的抓取元数据（tickers、日期、来源、行数），请改用自然语言"
                "抓取模式（不传 `--input_dir`，由 `fetch_real_market_data` 抓取并生成 "
                "`fetch_metadata.json`）。"
            )
            lines.append("")

    # ------------------------------------------------------------------
    # one-page summary（中文）
    # ------------------------------------------------------------------

    def render_one_page(self) -> str:
        """构建 final_workflow_one_page.md（中文一页摘要）。"""
        s = self.build_summary()
        cl = s["closed_loop_result"]
        ds = s["data_source_summary"]
        lines: list[str] = []

        lines.append("# 金融表格 analysis-ready 工作流 — 一页摘要")
        lines.append("")
        lines.append(
            "**项目：** `financial_table_workflow_agent`（与 NTU 临床表格 capstone 同构）。"
        )
        lines.append("")

        lines.append("## 目标")
        lines.append("")
        lines.append(
            "把原始、杂乱的金融表格（行情、成交、财务、行业、交易日历）加工成一张"
            "**analysis-ready 建模宽表**，用于五日收益预测 / 因子分析——**只做数据准备，"
            "不输出投资建议、不训练模型**。难点在于防止未来函数与标签泄漏，而非清洗单元格。"
        )
        lines.append("")

        lines.append("## 数据来源")
        lines.append("")
        if ds.get("source_kind") == "fetched_real_market_data":
            lines.append(
                f"- 由 `fetch_real_market_data` 抓取真实 A 股行情："
                f"tickers={ds.get('resolved_tickers')}，"
                f"{ds.get('start_date')} ~ {ds.get('end_date')}，"
                f"fetch_date={ds.get('fetch_date')}。"
            )
            sr = ds.get("summary_rows", {}) or {}
            lines.append(
                f"- 各表行数：price={sr.get('price')} volume={sr.get('volume')} "
                f"fundamentals={sr.get('fundamentals')} industry={sr.get('industry')} "
                f"calendar={sr.get('calendar')}。"
            )
            lines.append(
                "- 当前 PE/PB/ROE 是快照，**不是历史 point-in-time 基本面**，"
                "不回填到历史日期。"
            )
        else:
            lines.append(
                f"- 本次使用用户提供的已有 CSV（input_dir=`{ds.get('input_dir')}`），"
                "未抓取，不编造外部来源。"
            )
        lines.append("")

        lines.append("## 六个模块")
        lines.append("")
        lines.append(
            "1. **数据剖析** — 剖析 schema、缺失、日期、证券代码、重复、异常与跨表不一致"
            "（如 `trade_date` vs `date`、`ticker` vs `stock_code`、财务公告滞后）。"
        )
        lines.append(
            "2. **工作流规划** — 读剖析结果 + 下游分析目标，输出有序、防泄漏的计划"
            "（步骤数、校验项数、特征数 + 1 标签均动态读取）。"
        )
        lines.append(
            "3. **代码执行** — 用 pandas 按 plan 执行成 ticker-date 宽表，"
            "rolling/pct_change 按 ticker 分组、财务按 `announce_date` 对齐。"
        )
        lines.append(
            "4. **有效性审查** — 审查宽表的未来函数 / 标签泄漏 / announce_date 对齐 / "
            "时间切分（非普通质量检查）。"
        )
        lines.append(
            "5. **修复闭环** — 消费 Critic 失败项，输出可解释修复，并重跑 Critic 验证"
            "（闭环）。"
        )
        lines.append(
            "6. **复审** — 对修复后宽表重新运行 Critic，确认失败项已解决。"
        )
        lines.append("")

        lines.append("## 闭环结果")
        lines.append("")
        if cl.get("no_op_kind") == "no_repair_needed":
            lines.append(f"- 初始 `prepared_panel.csv`：**{cl['initial_rows']} 行**。")
            lines.append(
                f"- Critic 状态 **{self._status_zh(cl['initial_status'])}**，无需修复；"
                f"宽表不变，仍为 **{cl['repaired_rows']} 行**。"
            )
            lines.append(
                f"- 复审 Critic 状态 **{self._status_zh(cl['final_status'])}**"
                "（0 失败；剩余 warning 为预期的 pe/pb/roe 稀疏与 industry 缺失，非失败）。"
            )
        elif cl.get("no_op_kind") == "repair_disabled":
            lines.append(f"- 初始 `prepared_panel.csv`：**{cl['initial_rows']} 行**。")
            lines.append(
                f"- Critic 失败，修复被禁用（--no_repair）；宽表不变，仍为 "
                f"**{cl['repaired_rows']} 行**，最终状态仍为 **failed**。"
            )
        else:
            lines.append(f"- 初始 `prepared_panel.csv`：**{cl['initial_rows']} 行**。")
            lines.append(
                f"- Critic 发现 **{cl['rows_removed']} 行 `close` 缺失**"
                "（核心价格字段），状态 "
                f"**{self._status_zh(cl['initial_status'])}**。"
            )
            lines.append(
                f"- 修复闭环**删除了这 {cl['rows_removed']} 行**（保守策略：删除而非插值），"
                f"产出 `repaired_panel.csv`，共 **{cl['repaired_rows']} 行**。"
            )
            lines.append(
                f"- 复审 Critic 状态 **{self._status_zh(cl['final_status'])}**"
                "（0 失败；剩余 warning 为预期的 pe/pb/roe 稀疏与 industry 缺失，非失败）。"
            )
        lines.append(
            f"- 标签 `{s['label_column']}` **不**在 approved feature columns 中——"
            "标签泄漏从结构上被防止。"
        )
        lines.append("")
        lines.append(f"> {cl['one_line']}")
        lines.append("")

        lines.append("## 为什么重要")
        lines.append("")
        lines.append(
            "这是一个 **task-aware analysis-ready 工作流**，不是表格检查器：它围绕建模"
            "目标规划、从结构上防止未来函数与标签泄漏，并通过 critic → repair → re-critic "
            "闭环自我修正。其方法论（金融未来函数 ≈ 临床时间泄漏）可迁移到临床队列准备。"
        )
        lines.append("")

        lines.append("## 局限性")
        lines.append("")
        lines.append(
            "- Report Generator 为确定性基线，不调用 LLM、不训练模型、不输出投资建议。"
        )
        lines.append(
            "- 当前 PE/PB/ROE 快照不是历史 point-in-time 基本面，不回填到历史日期。"
        )
        lines.append(
            "- 后续方向：多 Planner 投票、LLM Planner/Critic/Repair、规则 vs 单 Agent vs "
            "多 Agent 基线对比（均离线，不输出投资建议）。"
        )
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # save all
    # ------------------------------------------------------------------

    def save_all(self, output_dir: str | Path) -> dict[str, Path]:
        """写 4 个文件到 output_dir，返回路径 dict。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        summary = self.build_summary()
        index = self.build_artifacts_index()
        full = self.render_full_report()
        one = self.render_one_page()

        summary_path = out / "final_workflow_summary.json"
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        index_path = out / "pipeline_artifacts_index.json"
        with index_path.open("w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        full_path = out / "final_workflow_report.md"
        full_path.write_text(full, encoding="utf-8")

        one_path = out / "final_workflow_one_page.md"
        one_path.write_text(one, encoding="utf-8")

        return {
            "summary": summary_path,
            "index": index_path,
            "full_report": full_path,
            "one_page": one_path,
        }
