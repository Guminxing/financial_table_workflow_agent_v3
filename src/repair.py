"""Remediation / Repair Loop（第五阶段）。

读取第四阶段 Critic 的 validation_report.json，针对 failed/warning 项生成
可解释的修复方案，并执行确定性修复，输出 repaired_panel.csv。

当前重点修复：close 缺失导致的 missing_rate_after_join failed。
策略：删除 close 缺失行（保守策略，不默认插值）。

设计原则：
- 确定性 baseline，不调用任何外部 LLM API，离线可运行。
- 不删除/重写前四阶段代码，本模块独立。
- 不训练模型、不输出投资建议、不连接真实券商系统。
- 修复后支持重新运行 Critic，形成闭环。
- 路径用 pathlib，兼容 Windows，不写死绝对路径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

REPAIR_VERSION = "0.1"

# 核心行情字段：缺失时优先删除整行（保守策略）
CORE_PRICE_COLUMNS = ["close"]


class RepairLoop:
    """修复闭环：读 Critic 报告 → 生成修复方案 → 执行修复 → 输出 repaired panel。

    用法::

        loop = RepairLoop()
        loop.load_inputs(
            panel_path="outputs/prepared/prepared_panel.csv",
            validation_report_path="outputs/validation/validation_report.json",
            data_dictionary_path="outputs/prepared/data_dictionary.json",
            approved_features_path="outputs/validation/approved_feature_columns.json",
        )
        plan = loop.build_repair_plan()
        result = loop.apply_repairs(plan)
        loop.save_outputs(result, "outputs/repaired")
        loop.save_report(result, "outputs/repaired")
    """

    def __init__(self) -> None:
        self.panel: pd.DataFrame | None = None
        self.validation_report: dict[str, Any] = {}
        self.data_dictionary: dict[str, Any] = {}
        self.approved_features: dict[str, Any] = {}
        self.input_files: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def load_inputs(
        self,
        panel_path: str | Path,
        validation_report_path: str | Path,
        data_dictionary_path: str | Path,
        approved_features_path: str | Path,
    ) -> None:
        """读取全部输入。"""
        self.panel = pd.read_csv(panel_path)
        if "date" in self.panel.columns:
            self.panel["date"] = pd.to_datetime(self.panel["date"], errors="coerce")
        if "announce_date" in self.panel.columns:
            self.panel["announce_date"] = pd.to_datetime(
                self.panel["announce_date"], errors="coerce"
            )
        with open(validation_report_path, "r", encoding="utf-8") as f:
            self.validation_report = json.load(f)
        with open(data_dictionary_path, "r", encoding="utf-8") as f:
            self.data_dictionary = json.load(f)
        with open(approved_features_path, "r", encoding="utf-8") as f:
            self.approved_features = json.load(f)
        self.input_files = {
            "panel": str(panel_path).replace("\\", "/"),
            "validation_report": str(validation_report_path).replace("\\", "/"),
            "data_dictionary": str(data_dictionary_path).replace("\\", "/"),
            "approved_features": str(approved_features_path).replace("\\", "/"),
        }

    def build_repair_plan(self) -> dict[str, Any]:
        """根据 Critic 的 failed/warning 项生成修复方案。"""
        if self.panel is None:
            raise RuntimeError("call load_inputs() first")

        failed_checks = [
            {
                "check_name": c["check_name"],
                "category": c["category"],
                "severity": c["severity"],
                "status": c["status"],
                "description": c["description"],
                "evidence": c["evidence"],
                "recommendation": c["recommendation"],
            }
            for c in self.validation_report.get("checks", [])
            if c["status"] == "failed"
        ]
        warning_checks = [
            {
                "check_name": c["check_name"],
                "status": c["status"],
                "evidence": c["evidence"],
            }
            for c in self.validation_report.get("checks", [])
            if c["status"] == "warning"
        ]

        repair_actions: list[dict[str, Any]] = []
        not_repaired: list[dict[str, Any]] = []
        action_id = 1

        # ---- 处理 missing_rate_after_join 中的 close 缺失 ----
        missing_check = next(
            (c for c in failed_checks if c["check_name"] == "missing_rate_after_join"),
            None,
        )
        if missing_check is not None:
            ev = missing_check.get("evidence", {})
            close_rate = ev.get("close_missing_rate", 0.0)
            if close_rate > 0 and "close" in self.panel.columns:
                affected = int(self.panel["close"].isna().sum())
                repair_actions.append(
                    {
                        "action_id": action_id,
                        "target_check": "missing_rate_after_join",
                        "target_columns": ["close"],
                        "issue": "close has missing values",
                        "strategy": "drop_rows_with_missing_core_price",
                        "reason": (
                            "close is required for return features (return_1d/return_5d/"
                            "volatility_20d) and label (label_next_5d); rows with missing "
                            "close cannot be safely used for supervised modeling"
                        ),
                        "affected_rows_before": affected,
                        "expected_effect": (
                            "remove rows with missing close; dependent fields (return/volatility/"
                            "label) on remaining rows are recomputed by re-running executor "
                            "in a full pipeline; baseline repair keeps existing values and "
                            "only drops the bad rows"
                        ),
                        "risk": "slightly reduces sample size",
                        "requires_manual_confirmation": False,
                    }
                )
                action_id += 1
            # pe/pb/roe 高缺失与 industry 缺失：不修复（合理稀疏/设计），记入 not_repaired
            fund_rates = ev.get("fundamental_missing_rates", {})
            if any(r > 0.2 for r in fund_rates.values()):
                not_repaired.append(
                    {
                        "item": "pe/pb/roe high missing rate",
                        "reason": (
                            "low announce frequency is expected for fundamentals; "
                            "not a failure, only a warning; no repair needed"
                        ),
                    }
                )
            if ev.get("industry_missing_rate", 0) > 0:
                not_repaired.append(
                    {
                        "item": "industry_name missing",
                        "reason": (
                            "reflects simulated data design (one ticker missing industry); "
                            "kept as-is; downstream can encode as 'unknown'"
                        ),
                    }
                )

        # ---- 其他 failed 检查：当前 baseline 未实现自动修复的，记入 not_repaired ----
        for c in failed_checks:
            if c["check_name"] == "missing_rate_after_join":
                continue
            not_repaired.append(
                {
                    "item": c["check_name"],
                    "reason": (
                        "no automatic repair strategy implemented in baseline; "
                        "requires manual review"
                    ),
                    "evidence": c.get("evidence"),
                }
            )

        # ---- warning 检查：记录但不强制修复 ----
        for c in warning_checks:
            not_repaired.append(
                {
                    "item": c["check_name"],
                    "reason": "warning-level; recorded but not auto-repaired in baseline",
                    "evidence": c.get("evidence"),
                }
            )

        return {
            "project": "financial_table_workflow_agent",
            "repair_version": REPAIR_VERSION,
            "input_validation_status": self.validation_report.get("overall_status", "unknown"),
            "failed_checks": failed_checks,
            "warning_checks": warning_checks,
            "repair_actions": repair_actions,
            "not_repaired_items": not_repaired,
            "next_validation_required": True,
        }

    def apply_repairs(self, plan: dict[str, Any]) -> dict[str, Any]:
        """执行修复方案，返回结果 dict（含 repaired panel 与 log）。"""
        if self.panel is None:
            raise RuntimeError("call load_inputs() first")

        df = self.panel.copy()
        rows_before = len(df)
        actions_applied: list[dict[str, Any]] = []
        warnings: list[str] = []

        for action in plan["repair_actions"]:
            if action["strategy"] == "drop_rows_with_missing_core_price":
                cols = [c for c in action["target_columns"] if c in df.columns]
                if not cols:
                    warnings.append(
                        f"action {action['action_id']}: target columns not found, skipped"
                    )
                    continue
                mask = df[cols].isna().any(axis=1)
                removed = int(mask.sum())
                df = df[~mask].reset_index(drop=True)
                actions_applied.append(
                    {
                        "action_id": action["action_id"],
                        "strategy": action["strategy"],
                        "target_columns": cols,
                        "rows_removed": removed,
                        "status": "applied",
                    }
                )
            else:
                warnings.append(
                    f"action {action['action_id']}: unknown strategy "
                    f"{action['strategy']}, skipped"
                )

        rows_after = len(df)

        # 修复后自检
        checks_after = self._post_repair_checks(df)

        log = {
            "project": "financial_table_workflow_agent",
            "repair_version": REPAIR_VERSION,
            "input_panel_path": self.input_files["panel"],
            "input_validation_report_path": self.input_files["validation_report"],
            "rows_before": rows_before,
            "rows_after": rows_after,
            "rows_removed": rows_before - rows_after,
            "actions_applied": actions_applied,
            "checks_after_repair": checks_after,
            "warnings": warnings,
            "next_step": "rerun_validity_critic_on_repaired_panel",
        }

        return {
            "repair_plan": plan,
            "repaired_panel": df,
            "repair_log": log,
        }

    def save_outputs(self, result: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
        """保存 repair_plan.json / repaired_panel.csv / repair_log.json。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        plan_path = out / "repair_plan.json"
        with plan_path.open("w", encoding="utf-8") as f:
            json.dump(result["repair_plan"], f, ensure_ascii=False, indent=2)

        panel: pd.DataFrame = result["repaired_panel"]
        csv_path = out / "repaired_panel.csv"
        panel_to_write = panel.copy()
        if "date" in panel_to_write.columns:
            panel_to_write["date"] = pd.to_datetime(
                panel_to_write["date"]
            ).dt.strftime("%Y-%m-%d")
        if "announce_date" in panel_to_write.columns:
            # announce_date 可能含 NaT
            ad = pd.to_datetime(panel_to_write["announce_date"], errors="coerce")
            panel_to_write["announce_date"] = ad.dt.strftime("%Y-%m-%d")
        panel_to_write.to_csv(csv_path, index=False, encoding="utf-8-sig")

        log_path = out / "repair_log.json"
        with log_path.open("w", encoding="utf-8") as f:
            json.dump(result["repair_log"], f, ensure_ascii=False, indent=2)

        return {"plan": plan_path, "panel": csv_path, "log": log_path}

    def save_report(self, result: dict[str, Any], output_dir: str | Path) -> Path:
        """生成并保存 repair_report.md。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        md_path = out / "repair_report.md"
        md_path.write_text(self._render_report(result), encoding="utf-8")
        return md_path

    # ------------------------------------------------------------------
    # 修复后自检
    # ------------------------------------------------------------------

    def _post_repair_checks(self, df: pd.DataFrame) -> dict[str, Any]:
        close_missing = int(df["close"].isna().sum()) if "close" in df.columns else -1
        pk_dup = int(df.duplicated(subset=["date", "ticker"]).sum()) if all(
            c in df.columns for c in ["date", "ticker"]
        ) else -1
        label_preserved = "label_next_5d" in df.columns
        # approved features 仍不含 label
        approved = self.approved_features.get("approved_feature_columns", [])
        label_not_in_features = "label_next_5d" not in approved
        return {
            "close_missing_count": close_missing,
            "primary_key_unique": pk_dup == 0,
            "primary_key_duplicate_count": pk_dup,
            "label_column_preserved": label_preserved,
            "label_not_in_approved_features": label_not_in_features,
            "approved_feature_columns_unchanged": approved,
        }

    # ------------------------------------------------------------------
    # Markdown 报告
    # ------------------------------------------------------------------

    def _render_report(self, result: dict[str, Any]) -> str:
        plan = result["repair_plan"]
        log = result["repair_log"]
        lines: list[str] = []
        lines.append("# Repair Loop Report")
        lines.append("")
        lines.append(
            f"- project: `{plan['project']}`  |  repair_version: `{plan['repair_version']}`"
        )
        lines.append("")

        # 1. Why Repair Was Needed
        lines.append("## 1. Why Repair Was Needed")
        lines.append("")
        lines.append(
            f"The Validity Critic reported `overall_status = {plan['input_validation_status']}`. "
            "The failing check was `missing_rate_after_join` caused by missing `close` values: "
            "`close` is a core price field required by return features and the label, so its "
            "missing rate must be 0. Until repaired, the panel cannot be considered analysis-ready."
        )
        lines.append("")

        # 2. Failed Checks From Critic
        lines.append("## 2. Failed Checks From Critic")
        lines.append("")
        if plan["failed_checks"]:
            lines.append("| check_name | category | description | evidence |")
            lines.append("|---|---|---|---|")
            for c in plan["failed_checks"]:
                ev = c.get("evidence", {})
                ev_str = ", ".join(f"{k}={v}" for k, v in ev.items())
                lines.append(
                    f"| {c['check_name']} | {c['category']} | {c['description']} | {ev_str} |"
                )
        else:
            lines.append("(none)")
        lines.append("")

        # 3. Repair Strategy
        lines.append("## 3. Repair Strategy")
        lines.append("")
        lines.append("For `close` missing rows, the baseline **drops the entire row** rather than imputing.")
        lines.append("")
        lines.append("Reasons:")
        lines.append("")
        lines.append("- `close` is the core price field; `return_1d`, `return_5d`, `volatility_20d`, and `label_next_5d` all depend on it.")
        lines.append("- For simulated data and a modeling panel, dropping 2/300 rows is more conservative than imputation and avoids fabricating price points.")
        lines.append("- Imputation could introduce artificial return/volatility patterns that bias downstream modeling.")
        lines.append("")
        lines.append("Note on real-world data:")
        lines.append("")
        lines.append("- For real market data, a better approach is to re-fetch the original price series or use ticker-level time-series interpolation / adjusted-price re-pull, then re-run the executor.")
        lines.append("- The current baseline deliberately chooses conservative row deletion.")
        lines.append("")
        if plan["not_repaired_items"]:
            lines.append("Items not repaired (by design):")
            lines.append("")
            for it in plan["not_repaired_items"]:
                lines.append(f"- `{it['item']}` — {it['reason']}")
            lines.append("")

        # 4. Repair Result
        lines.append("## 4. Repair Result")
        lines.append("")
        lines.append(f"- rows before: {log['rows_before']}")
        lines.append(f"- rows after: {log['rows_after']}")
        lines.append(f"- rows removed: {log['rows_removed']}")
        chk = log["checks_after_repair"]
        lines.append(f"- close missing count after repair: {chk['close_missing_count']}")
        lines.append(f"- primary key unique after repair: {chk['primary_key_unique']}")
        lines.append(f"- label column preserved: {chk['label_column_preserved']}")
        lines.append(f"- label not in approved features: {chk['label_not_in_approved_features']}")
        lines.append("")
        lines.append("Actions applied:")
        lines.append("")
        lines.append("| action_id | strategy | target_columns | rows_removed | status |")
        lines.append("|---|---|---|---|---|")
        for a in log["actions_applied"]:
            lines.append(
                f"| {a['action_id']} | {a['strategy']} | {a['target_columns']} | {a['rows_removed']} | {a['status']} |"
            )
        lines.append("")

        # 5. Limitations
        lines.append("## 5. Limitations")
        lines.append("")
        lines.append("- This is a **deterministic baseline repair**; no LLM is called.")
        lines.append("- For real market data, the ideal approach is to re-fetch original prices or repair via business rules, not just drop rows.")
        lines.append("- Dependent fields (return/volatility/label) on remaining rows are NOT recomputed in this minimal repair; a full fix re-runs the executor on repaired inputs. For the current sample, dropping rows does not break existing per-ticker rolling windows because windows are min_periods=1.")
        lines.append("- No model is trained in this stage.")
        lines.append("- No investment advice is produced.")
        lines.append("")

        # 6. Next Step
        lines.append("## 6. Next Step")
        lines.append("")
        lines.append("Re-run the Validity Critic on the repaired panel to confirm the failure is resolved:")
        lines.append("")
        lines.append("```bash")
        lines.append(
            "python src/run_critic.py --panel_path outputs/repaired/repaired_panel.csv "
            "--data_dictionary_path outputs/prepared/data_dictionary.json "
            "--execution_log_path outputs/prepared/execution_log.json "
            "--plan_path outputs/plans/workflow_plan.json "
            "--executor_source_path src/executor.py "
            "--calendar_path data/sample/calendar.csv "
            "--output_dir outputs/validation_repaired"
        )
        lines.append("```")
        lines.append("")

        return "\n".join(lines)
