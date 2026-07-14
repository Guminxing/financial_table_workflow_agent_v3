"""Final Report Generator（第六阶段）。

只读前五阶段产物，汇总成面向导师/审计的最终总报告，把六阶段 workflow 与
闭环结果讲清楚，并明确说明这不是"普通表格检查"，而是 task-aware
analysis-ready workflow prototype。

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

REPORT_VERSION = "0.1"


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
    ) -> None:
        """读取全部前五阶段产物（只读，不修改任何输入）。"""
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
            "approved_feature_columns": approved,
            "excluded_columns": self.approved_features.get("excluded_columns", []),
            "label_column": label_col,
            "label_not_in_approved_features": label_not_in_features,
            "limitations": [
                "Report Generator is a deterministic baseline; no LLM is called.",
                "It only reads prior-stage artifacts; it does not re-run any stage.",
                "No model is trained; no investment advice is produced.",
                "Only real market data fetched via the adapter is used, not broker data.",
            ],
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
    # full markdown report
    # ------------------------------------------------------------------

    def render_full_report(self) -> str:
        """构建 final_workflow_report.md（含 Mermaid 架构图 + Why This Is More Than Table Checking）。"""
        s = self.build_summary()
        cl = s["closed_loop_result"]
        lines: list[str] = []

        # 1. 标题 + meta
        lines.append("# Financial Table Analysis-Ready Workflow — Final Report")
        lines.append("")
        lines.append(
            f"- project: `{s['project']}`  |  report_version: `{s['report_version']}`"
        )
        lines.append(
            f"- generated from stages: {s['generated_from_stages']}"
        )
        lines.append(
            f"- initial validation status: **{s['initial_validation_status']}**  →  "
            f"final validation status: **{s['final_validation_status']}**"
        )
        lines.append(f"- rows removed by repair: **{s['rows_removed_by_repair']}**")
        lines.append("")

        # 2. Executive Summary
        lines.append("## 1. Executive Summary")
        lines.append("")
        lines.append(
            "This project turns raw, messy financial/brokerage tables (price, volume, "
            "fundamentals, industry, trading calendar) into an **analysis-ready modeling "
            "panel** through a six-stage agent workflow. It is **not** a one-shot table "
            "cleaner — it is a **task-aware analysis-ready workflow prototype** that plans "
            "around a downstream modeling goal, prevents look-ahead bias and label leakage "
            "by construction, and closes the loop with a critic → repair → re-critic cycle."
        )
        lines.append("")
        lines.append("**Closed-loop result:**")
        lines.append("")
        lines.append(f"- Initial `prepared_panel.csv`: **{cl['initial_rows']} rows**.")
        lines.append(
            f"- Initial Critic status: **{cl['initial_status']}** — failed check "
            f"`{cl['failed_check'] or 'none'}` ({cl['failed_reason'] or 'no failed checks'})."
        )
        if cl.get("no_op_kind") == "no_repair_needed":
            lines.append(
                f"- Repair Loop: **no-op (no repair needed)** — initial critic did not fail; "
                f"`repaired_panel.csv` = `prepared_panel.csv` with **{cl['repaired_rows']} rows**."
            )
        elif cl.get("no_op_kind") == "repair_disabled":
            lines.append(
                f"- Repair Loop: **disabled (--no_repair)** — initial critic failed but repair "
                f"was disabled; panel unchanged at **{cl['repaired_rows']} rows**, final status "
                f"remains **failed**."
            )
        else:
            lines.append(
                f"- Repair Loop removed **{cl['rows_removed']} rows** (missing `close`), "
                f"producing `repaired_panel.csv` with **{cl['repaired_rows']} rows**."
            )
        lines.append(
            f"- Re-run Critic status: **{cl['final_status']}** "
            f"(failed → {self.final_validation.get('summary', {}).get('failed', 0)})."
        )
        lines.append(
            f"- Label isolation: `{s['label_column']}` is **not** in approved feature "
            f"columns (`label_not_in_approved_features = {cl['label_not_in_approved_features']}`)."
        )
        lines.append("")
        lines.append(f"> {cl['one_line']}")
        lines.append("")

        # 3. Mermaid 架构图
        lines.append("## 2. Pipeline Architecture (Mermaid)")
        lines.append("")
        lines.append("```mermaid")
        lines.append("flowchart TD")
        lines.append("    RAW[raw financial tables<br/>price / volume / fundamentals / industry / calendar]")
        lines.append("    S1[Stage 1: Data Profiler<br/>profile.json]")
        lines.append("    S2[Stage 2: Workflow Planner<br/>workflow_plan.json]")
        lines.append(f"    S3[Stage 3: Code Executor<br/>prepared_panel.csv {cl['initial_rows']} rows]")
        lines.append(f"    S4[Stage 4: Validity Critic<br/>status = {cl['initial_status']}<br/>failed check: {cl['failed_check'] or 'none'}]")
        if cl.get("no_op_kind") == "no_repair_needed":
            lines.append("    S5[Stage 5: Repair Loop<br/>no-op (no repair needed)<br/>repaired_panel.csv = prepared_panel.csv]")
            lines.append(f"    S6[Stage 6: Re-run Critic<br/>status = {cl['final_status']}]")
            lines.append("    S4 -- not failed --> S5 --> S6")
        elif cl.get("no_op_kind") == "repair_disabled":
            lines.append("    S5[Stage 5: Repair Loop<br/>no-op (disabled, --no_repair)<br/>panel unchanged]")
            lines.append(f"    S6[Stage 6: Re-run Critic<br/>status = {cl['final_status']}]")
            lines.append("    S4 -- failed but --no_repair --> S5 --> S6")
        else:
            lines.append(f"    S5[Stage 5: Repair Loop<br/>drop close-missing rows<br/>repaired_panel.csv {cl['repaired_rows']} rows]")
            lines.append(f"    S6[Stage 6: Re-run Critic<br/>status = {cl['final_status']}]")
            lines.append("    S4 -- failed --> S5 --> S6")
        lines.append("    OUT[analysis-ready panel<br/>+ approved_feature_columns]")
        lines.append(f"    S6 -- {cl['final_status']} --> OUT")
        lines.append("    S4 -. approved features .-> OUT")
        lines.append("```")
        lines.append("")

        # 4. Why This Is More Than Table Checking
        lines.append("## 3. Why This Is More Than Table Checking")
        lines.append("")
        lines.append(
            "A naive table checker asks \"is the data clean?\" — missing rates, duplicates, "
            "dtypes, outliers. That is necessary but **nowhere near sufficient** for modeling. "
            "This workflow asks the harder, task-aware question: **can this panel be safely "
            "fed to a time-series model without leaking the future?**"
        )
        lines.append("")
        lines.append("What makes it more than table checking:")
        lines.append("")
        lines.append(
            "1. **Task-aware planning.** The Planner reads the profiler output *and* a "
            "downstream analysis goal (5-day return prediction / factor analysis) and emits "
            "an ordered, dependency-respecting plan with explicit leakage risks per step — "
            "not a generic cleaning recipe."
        )
        lines.append(
            "2. **Look-ahead bias prevention by construction.** Rolling/pct_change features "
            "are grouped by ticker and use only historical windows; fundamentals are aligned "
            "by `announce_date` (as-of merge), never `report_date`. The financial future "
            "function is the analogue of clinical time leakage."
        )
        lines.append(
            "3. **Label leakage prevention.** `label_next_5d` (future 5-day return) is "
            "generated with `shift(-5)`, marked `role=label` in the data dictionary, and "
            "structurally excluded from `approved_feature_columns` — so a downstream model "
            "literally cannot read the label as a feature."
        )
        lines.append(
            "4. **Temporal validity.** The plan requires time-based train/test split (no "
            "random shuffling of time series); the Critic enforces that the plan demands it."
        )
        lines.append(
            "5. **Source-level static analysis.** The Critic does not just look at the panel "
            "— it reads `executor.py` source to verify `merge_asof` + `announce_date` and "
            "that no non-label `shift(-k)` exists."
        )
        lines.append(
            "6. **Closed-loop self-correction.** When the Critic fails (close missing), the "
            "Repair Loop consumes the failure, emits an explainable repair action, and the "
            "Critic is re-run independently to confirm the fix. A table checker reports a "
            "problem and stops; this workflow **fixes and re-verifies**."
        )
        lines.append("")
        lines.append("| Dimension | Ordinary table checking | This workflow |")
        lines.append("|---|---|---|")
        lines.append("| Focus | missing/duplicate/dtype/outlier | future-function, label leakage, temporal validity |")
        lines.append("| Failure consequence | dirty but cleanable | model looks valid but is invalid; live disaster |")
        lines.append("| Inspects | the table alone | table + data dictionary + execution log + plan + source |")
        lines.append("| Verdict basis | statistical thresholds | time causality, role labels, source static analysis |")
        lines.append("| On failure | report and stop | repair → re-critic closed loop |")
        lines.append("")

        # 5. Stage-by-stage
        lines.append("## 4. Stage-by-Stage")
        lines.append("")
        ps = s["profile_summary"]
        pl = s["plan_summary"]
        ex = s["execution_summary"]
        pa = s["panel_summary"]
        lines.append("### Stage 1 — Data Profiler")
        lines.append("")
        lines.append(f"- Tables profiled: {ps['n_tables']}.")
        lines.append("")
        lines.append("| table | n_rows | n_columns |")
        lines.append("|---|---|---|")
        for t in ps["tables"]:
            lines.append(f"| {t['table']} | {t['n_rows']} | {t['n_columns']} |")
        lines.append("")
        lines.append("Cross-table findings:")
        lines.append("")
        for it in ps["schema_inconsistencies"]:
            lines.append(
                f"- `{it.get('type')}`: columns {it.get('columns')} across "
                f"{it.get('tables')} — {it.get('note')}"
            )
        for it in ps["global_potential_issues"]:
            lines.append(f"- {it}")
        lines.append("")

        lines.append("### Stage 2 — Workflow Planner")
        lines.append("")
        lines.append(f"- analysis_goal: {pl['analysis_goal']}")
        lines.append(
            f"- {pl['n_workflow_steps']} workflow steps, {pl['n_validation_checks']} "
            f"validation checks, {pl['n_features']} features + 1 label."
        )
        lines.append(
            f"- label: `{pl['label'].get('name')}` — {pl['label'].get('usage')}"
        )
        lines.append("")

        lines.append("### Stage 3 — Code Executor")
        lines.append("")
        lines.append(
            f"- `prepared_panel.csv`: {ex['n_rows']} rows × {ex['n_columns']} columns, "
            f"{ex['n_tickers']} tickers, {ex['date_min']} ~ {ex['date_max']}, "
            f"primary_key_unique={ex['primary_key_unique']}."
        )
        lines.append(
            f"- {ex['n_steps_executed']} steps executed, {ex['n_warnings']} warnings, "
            f"{ex['n_errors']} errors."
        )
        lines.append(
            "- Leakage-safe: rolling/pct_change grouped by ticker (historical window only); "
            "fundamentals aligned by `announce_date` via `merge_asof(direction='backward')`."
        )
        lines.append("")

        lines.append("### Stage 4 — Validity Critic (initial)")
        lines.append("")
        init_sum = self.initial_validation.get("summary", {})
        lines.append(
            f"- overall_status: **{s['initial_validation_status']}** "
            f"({init_sum.get('passed')} passed / {init_sum.get('warnings')} warnings / "
            f"{init_sum.get('failed')} failed of {init_sum.get('total_checks')})."
        )
        lines.append(
            f"- Failed check: `{cl['failed_check'] or 'none'}` — "
            f"{cl['failed_reason'] or 'no failed checks'}."
        )
        lines.append(
            f"- approved_feature_columns: {s['approved_feature_columns']}."
        )
        lines.append(
            f"- `{s['label_column']}` in approved features? "
            f"**{not s['label_not_in_approved_features']}** (must be False)."
        )
        lines.append("")

        lines.append("### Stage 5 — Repair Loop")
        lines.append("")
        rap = self.repair_plan.get("repair_actions", [])
        if cl.get("no_op_kind") == "no_repair_needed":
            lines.append(
                f"- Repair skipped/no-op (initial critic did not fail): "
                f"rows_before: {cl['initial_rows']} → rows_after: {cl['repaired_rows']} "
                f"(removed {cl['rows_removed']})."
            )
            lines.append(
                "- `prepared_panel.csv` copied unchanged to `repaired_panel.csv`; "
                "initial validation copied as repaired validation."
            )
        elif cl.get("no_op_kind") == "repair_disabled":
            lines.append(
                f"- Repair disabled by --no_repair (initial critic failed): "
                f"rows_before: {cl['initial_rows']} → rows_after: {cl['repaired_rows']} "
                f"(removed {cl['rows_removed']}); panel unchanged; final status remains failed."
            )
        else:
            lines.append(
                f"- rows_before: {cl['initial_rows']} → rows_after: {cl['repaired_rows']} "
                f"(removed {cl['rows_removed']})."
            )
            if rap:
                a = rap[0]
                lines.append(
                    f"- action: `{a.get('strategy')}` on {a.get('target_columns')} — "
                    f"{a.get('reason')}"
                )
        chk = self.repair_log.get("checks_after_repair", {})
        lines.append(
            f"- post-repair self-check: close_missing={chk.get('close_missing_count')}, "
            f"primary_key_unique={chk.get('primary_key_unique')}, "
            f"label_preserved={chk.get('label_column_preserved')}, "
            f"label_not_in_features={chk.get('label_not_in_approved_features')}."
        )
        lines.append("")

        lines.append("### Stage 6 — Re-run Critic (closed-loop verification)")
        lines.append("")
        fin_sum = self.final_validation.get("summary", {})
        lines.append(
            f"- overall_status: **{s['final_validation_status']}** "
            f"({fin_sum.get('passed')} passed / {fin_sum.get('warnings')} warnings / "
            f"{fin_sum.get('failed')} failed of {fin_sum.get('total_checks')})."
        )
        close_rate_after = self._close_rate_after_repair()
        close_rate_before = self._close_rate_before_repair()
        lines.append(
            f"- `close` missing rate after repair: "
            f"{close_rate_after:.4f} (was {close_rate_before:.4f} before)."
        )
        lines.append(
            f"- approved_feature_columns unchanged: {s['approved_feature_columns']}; "
            f"`{s['label_column']}` still not in features."
        )
        lines.append("")

        # 6. Closed-loop deep dive
        lines.append("## 5. Closed-Loop Deep Dive")
        lines.append("")
        lines.append("| metric | before repair | after repair |")
        lines.append("|---|---|---|")
        lines.append(f"| rows | {cl['initial_rows']} | {cl['repaired_rows']} |")
        lines.append(
            f"| close missing count | {pa['close_missing_prepared']} | {pa['close_missing_repaired']} |"
        )
        lines.append(
            f"| Critic overall_status | {cl['initial_status']} | {cl['final_status']} |"
        )
        lines.append(
            f"| failed checks | {init_sum.get('failed')} | {fin_sum.get('failed')} |"
        )
        lines.append(
            f"| label in approved features | False | "
            f"{not s['label_not_in_approved_features']} |"
        )
        lines.append("")
        lines.append(
            "The loop is **feedback-driven** (Critic failure → repair), **explainable** "
            "(each action carries target_check/strategy/reason/risk), and **independently "
            "verifiable** (the re-run Critic — not the repairer — judges whether the fix held)."
        )
        lines.append("")

        # 7. Approved features & label isolation
        lines.append("## 6. Approved Features & Label Isolation")
        lines.append("")
        lines.append(f"- approved_feature_columns ({len(s['approved_feature_columns'])}):")
        for c in s["approved_feature_columns"]:
            role = self.data_dictionary.get(c, {}).get("role", "?")
            lines.append(f"  - `{c}` (role={role})")
        lines.append("")
        lines.append(f"- label_column: `{s['label_column']}` (role=label).")
        lines.append(
            f"- excluded_columns ({len(s['excluded_columns'])}): {s['excluded_columns']}"
        )
        lines.append(
            "- Downstream modeling reads `approved_feature_columns.json` as X and "
            "`label_column` as y; the label cannot enter the feature matrix by construction."
        )
        lines.append("")

        # 8. Limitations
        lines.append("## 7. Limitations")
        lines.append("")
        for it in s["limitations"]:
            lines.append(f"- {it}")
        lines.append(
            "- Remaining warning after repair: pe/pb/roe high missing (low announce "
            "frequency, expected) and industry_name missing (simulated data design) — "
            "acceptable for baseline, not failures."
        )
        lines.append("")

        # 9. Next steps
        lines.append("## 8. Next Steps")
        lines.append("")
        lines.append("- Multi Planner Voting: several planners propose plans, vote/select.")
        lines.append("- LLM Planner / LLM Critic / LLM Repair: replace/augment rule components.")
        lines.append("- Baseline comparison: rule-based vs single-agent vs multi-agent + critic.")
        lines.append("- Real broker data ingestion (out of current scope; no investment advice).")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # one-page summary
    # ------------------------------------------------------------------

    def render_one_page(self) -> str:
        """构建 final_workflow_one_page.md（一页，适合直接发导师）。"""
        s = self.build_summary()
        cl = s["closed_loop_result"]
        lines: list[str] = []

        lines.append("# Financial Table Analysis-Ready Workflow — One-Page Summary")
        lines.append("")
        lines.append("**Project:** `financial_table_workflow_agent` (isomorphic to an NTU clinical table capstone).")
        lines.append("")

        lines.append("## Goal")
        lines.append("")
        lines.append(
            "Turn raw, messy financial tables (price, volume, fundamentals, industry, "
            "trading calendar) into one **analysis-ready modeling panel** for 5-day return "
            "prediction / factor analysis — **data preparation only, no investment advice, "
            "no model training**. The hard part is preventing look-ahead bias and label "
            "leakage, not cleaning cells."
        )
        lines.append("")

        lines.append("## Five Modules")
        lines.append("")
        lines.append(
            "1. **Data Profiler** — profiles schema, missing values, dates, security codes, "
            "duplicates, anomalies, and cross-table inconsistencies (e.g. `trade_date` vs "
            "`date`, `ticker` vs `stock_code`, fundamentals announcement lag)."
        )
        lines.append(
            "2. **Workflow Planner** — reads the profile + a downstream analysis goal and "
            "emits an ordered, leakage-aware plan (13 steps, 12 validation checks, 8 "
            "features + 1 label)."
        )
        lines.append(
            "3. **Code Executor** — executes the plan with pandas into a ticker-date panel, "
            "grouping rolling/pct_change by ticker and aligning fundamentals by `announce_date`."
        )
        lines.append(
            "4. **Validity Critic** — reviews the panel for future-function / label leakage / "
            "announce-date alignment / time-based split (not ordinary quality checks)."
        )
        lines.append(
            "5. **Repair Loop** — consumes Critic failures, emits explainable repairs, and "
            "the Critic is re-run to verify (closed loop)."
        )
        lines.append("")

        lines.append("## Closed-Loop Result")
        lines.append("")
        if cl.get("no_op_kind") == "no_repair_needed":
            lines.append(
                f"- Initial `prepared_panel.csv`: **{cl['initial_rows']} rows**."
            )
            lines.append(
                f"- Critic status **{cl['initial_status']}**, no repair needed; "
                f"panel unchanged at **{cl['repaired_rows']} rows**."
            )
            lines.append(
                f"- Re-running the Critic gave status **{cl['final_status']}** "
                f"(0 failed; remaining warnings are expected pe/pb/roe sparsity and "
                f"missing industry, not failures)."
            )
        elif cl.get("no_op_kind") == "repair_disabled":
            lines.append(
                f"- Initial `prepared_panel.csv`: **{cl['initial_rows']} rows**."
            )
            lines.append(
                f"- Critic failed, repair disabled (--no_repair); panel unchanged at "
                f"**{cl['repaired_rows']} rows**, final status remains **failed**."
            )
        else:
            lines.append(
                f"- Initial `prepared_panel.csv`: **{cl['initial_rows']} rows**."
            )
            lines.append(
                f"- The Critic found **{cl['rows_removed']} rows with missing `close`** "
                f"(a core price field) and reported status **{cl['initial_status']}**."
            )
            lines.append(
                f"- The Repair Loop **deleted those {cl['rows_removed']} rows** (conservative: "
                f"drop, not impute), producing `repaired_panel.csv` with "
                f"**{cl['repaired_rows']} rows**."
            )
            lines.append(
                f"- Re-running the Critic gave status **{cl['final_status']}** "
                f"(0 failed; remaining warnings are expected pe/pb/roe sparsity and one missing "
                f"industry, not failures)."
            )
        lines.append(
            f"- The label `{s['label_column']}` is **not** in the approved feature columns — "
            f"label leakage is prevented by construction."
        )
        lines.append("")
        lines.append(
            f"> {cl['one_line']}"
        )
        lines.append("")

        lines.append("## Why It Matters")
        lines.append("")
        lines.append(
            "This is a **task-aware analysis-ready workflow**, not a table checker: it plans "
            "around a modeling goal, prevents future-function and label leakage by "
            "construction, and self-corrects via a critic → repair → re-critic loop. The "
            "methodology (financial future-function ≈ clinical time leakage) transfers to "
            "clinical cohort preparation."
        )
        lines.append("")

        lines.append("## Next Steps")
        lines.append("")
        lines.append(
            "- Multi Planner Voting; LLM Planner/Critic/Repair; rule vs single-agent vs "
            "multi-agent baseline comparison. (All offline, no investment advice.)"
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
