"""Validity Critic（第四阶段）。

读取第三阶段产出的 prepared_panel.csv、data_dictionary.json、execution_log.json、
workflow_plan.json 以及 executor.py 源码，对 analysis-ready table 进行有效性审查。

重点不是普通表格质量检查，而是检查：
- 金融建模有效性（label leakage、未来函数）
- 时间有效性（announce_date <= date、rolling 只用历史窗口、time-based split）
- 数据泄漏风险

设计原则：
- 确定性 baseline，不调用任何外部 LLM API，离线可运行。
- 不训练模型、不输出投资建议、不连接真实券商系统。
- 对 rolling 是否完全无未来函数的判断部分依赖源码静态检查，无法完全证明时给 warning。
- 路径用 pathlib，兼容 Windows，不写死绝对路径。
- 不删除/重写前三阶段代码，本模块独立。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

CRITIC_VERSION = "0.1"

# approved features 的候选白名单（必须同时满足 data_dictionary role=feature）
FEATURE_WHITELIST = [
    "return_1d",
    "return_5d",
    "volatility_20d",
    "turnover_20d",
    "pe",
    "pb",
    "roe",
    "industry_name",
]

# 必须存在的列
REQUIRED_COLUMNS = [
    "date", "ticker", "close", "volume", "turnover",
    "return_1d", "return_5d", "volatility_20d", "turnover_20d",
    "pe", "pb", "roe", "industry_name", "label_next_5d",
    "source_price_available", "source_volume_available",
    "source_fundamental_available", "source_industry_available",
]

# feature 列名中禁止出现的子串（防未来/标签泄漏）
FORBIDDEN_NAME_HINTS = ("future", "next", "label", "target")

# 不应进入 approved features 的 role
NON_FEATURE_ROLES = {"primary_key", "raw_input", "label", "source_flag", "auxiliary"}


class ValidityCritic:
    """金融表格 analysis-ready panel 有效性审查器。

    用法::

        critic = ValidityCritic()
        critic.load_inputs(
            panel_path="outputs_real/prepared/prepared_panel.csv",
            data_dictionary_path="outputs_real/prepared/data_dictionary.json",
            execution_log_path="outputs_real/prepared/execution_log.json",
            plan_path="outputs_real/plans/workflow_plan.json",
            executor_source_path="src/executor.py",
            calendar_path="data/real_market/calendar.csv",
        )
        report = critic.run_all_checks()
        critic.save_json_report(report, "outputs_real/validation/validation_report.json")
        critic.save_markdown_report(report, "outputs_real/validation/validation_report.md")
        critic.save_approved_feature_columns(report, "outputs_real/validation/approved_feature_columns.json")
    """

    def __init__(self) -> None:
        self.panel: pd.DataFrame | None = None
        self.data_dictionary: dict[str, Any] = {}
        self.execution_log: dict[str, Any] = {}
        self.plan: dict[str, Any] = {}
        self.executor_source: str = ""
        self.calendar: pd.DataFrame | None = None
        self.input_files: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def load_inputs(
        self,
        panel_path: str | Path,
        data_dictionary_path: str | Path,
        execution_log_path: str | Path,
        plan_path: str | Path,
        executor_source_path: str | Path,
        calendar_path: str | Path | None = None,
    ) -> None:
        """读取全部输入。"""
        self.panel = pd.read_csv(panel_path)
        if "date" in self.panel.columns:
            self.panel["date"] = pd.to_datetime(self.panel["date"], errors="coerce")
        if "announce_date" in self.panel.columns:
            self.panel["announce_date"] = pd.to_datetime(
                self.panel["announce_date"], errors="coerce"
            )

        with open(data_dictionary_path, "r", encoding="utf-8") as f:
            self.data_dictionary = json.load(f)
        with open(execution_log_path, "r", encoding="utf-8") as f:
            self.execution_log = json.load(f)
        with open(plan_path, "r", encoding="utf-8") as f:
            self.plan = json.load(f)
        self.executor_source = Path(executor_source_path).read_text(encoding="utf-8")

        if calendar_path is not None:
            cal = pd.read_csv(calendar_path)
            cal["date"] = pd.to_datetime(cal["date"], errors="coerce", format="mixed")
            cal["is_trading_day"] = pd.to_numeric(
                cal["is_trading_day"], errors="coerce"
            ).fillna(0).astype(int)
            self.calendar = cal

        self.input_files = {
            "prepared_panel": str(panel_path).replace("\\", "/"),
            "data_dictionary": str(data_dictionary_path).replace("\\", "/"),
            "execution_log": str(execution_log_path).replace("\\", "/"),
            "workflow_plan": str(plan_path).replace("\\", "/"),
            "executor_source": str(executor_source_path).replace("\\", "/"),
            "calendar": str(calendar_path).replace("\\", "/") if calendar_path else "",
        }

    def run_all_checks(self) -> dict[str, Any]:
        """运行全部检查，返回 validation_report dict。"""
        if self.panel is None:
            raise RuntimeError("call load_inputs() first")

        checks: list[dict[str, Any]] = []
        checks.append(self._check_primary_key_uniqueness())
        checks.append(self._check_required_columns_exist())
        checks.append(self._check_label_role_is_correct())
        checks.append(self._check_label_not_in_approved_features())
        checks.append(self._check_no_future_named_columns_in_features())
        checks.append(self._check_approved_features_have_valid_roles())
        checks.append(self._check_fundamentals_aligned_by_announce_date())
        checks.append(self._check_report_date_not_used_for_alignment())
        checks.append(self._check_rolling_features_past_only_static())
        checks.append(self._check_label_created_with_future_shift())
        checks.append(self._check_trading_calendar_alignment())
        checks.append(self._check_price_volume_sanity())
        checks.append(self._check_missing_rate_after_join())
        checks.append(self._check_source_flags_consistency())
        checks.append(self._check_time_based_split_required())

        # 汇总
        n_passed = sum(1 for c in checks if c["status"] == "passed")
        n_warn = sum(1 for c in checks if c["status"] == "warning")
        n_failed = sum(1 for c in checks if c["status"] == "failed")

        if n_failed > 0:
            overall = "failed"
        elif n_warn > 0:
            overall = "passed_with_warnings"
        else:
            overall = "passed"

        approved, excluded = self._derive_approved_feature_columns()

        return {
            "project": "financial_table_workflow_agent",
            "critic_version": CRITIC_VERSION,
            "input_files": self.input_files,
            "overall_status": overall,
            "summary": {
                "total_checks": len(checks),
                "passed": n_passed,
                "warnings": n_warn,
                "failed": n_failed,
            },
            "checks": checks,
            "approved_feature_columns": approved,
            "excluded_columns": excluded,
            "label_column": "label_next_5d",
            "limitations": self._limitations(),
        }

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------

    def save_json_report(self, report: dict[str, Any], output_path: str | Path) -> Path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return p

    def save_markdown_report(self, report: dict[str, Any], output_path: str | Path) -> Path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self._render_markdown(report), encoding="utf-8")
        return p

    def save_approved_feature_columns(
        self, report: dict[str, Any], output_path: str | Path
    ) -> Path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "approved_feature_columns": report["approved_feature_columns"],
            "excluded_columns": report["excluded_columns"],
            "label_column": report["label_column"],
            "notes": [
                "label_next_5d is a future return label and must not be used as a feature.",
                "approved features should be used with time-based train/test split only.",
            ],
        }
        with p.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return p

    # ==================================================================
    # 检查项实现
    # ==================================================================

    def _result(
        self,
        check_name: str,
        category: str,
        severity: str,
        status: str,
        description: str,
        evidence: dict[str, Any] | None = None,
        recommendation: str = "",
    ) -> dict[str, Any]:
        return {
            "check_name": check_name,
            "category": category,
            "severity": severity,
            "status": status,
            "description": description,
            "evidence": evidence or {},
            "recommendation": recommendation,
        }

    # ---- 1. primary_key_uniqueness ----
    def _check_primary_key_uniqueness(self) -> dict[str, Any]:
        dup = int(self.panel.duplicated(subset=["date", "ticker"]).sum())
        if dup == 0:
            return self._result(
                "primary_key_uniqueness", "data_quality", "error", "passed",
                "date + ticker must be unique",
                {"duplicate_count": dup},
                "No action needed.",
            )
        return self._result(
            "primary_key_uniqueness", "data_quality", "error", "failed",
            "date + ticker must be unique",
            {"duplicate_count": dup},
            "Deduplicate on (date, ticker) before modeling.",
        )

    # ---- 2. required_columns_exist ----
    def _check_required_columns_exist(self) -> dict[str, Any]:
        missing = [c for c in REQUIRED_COLUMNS if c not in self.panel.columns]
        if not missing:
            return self._result(
                "required_columns_exist", "schema", "error", "passed",
                "All required columns exist",
                {"required": REQUIRED_COLUMNS, "missing": []},
                "No action needed.",
            )
        return self._result(
            "required_columns_exist", "schema", "error", "failed",
            "All required columns must exist",
            {"missing": missing},
            f"Add missing columns: {missing}",
        )

    # ---- 3. label_role_is_correct ----
    def _check_label_role_is_correct(self) -> dict[str, Any]:
        lbl = self.data_dictionary.get("label_next_5d")
        if lbl is None:
            return self._result(
                "label_role_is_correct", "label_leakage", "error", "failed",
                "label_next_5d must exist in data_dictionary with role=label",
                {"found": False},
                "Add label_next_5d to data_dictionary with role=label.",
            )
        role = lbl.get("role")
        if role == "label":
            return self._result(
                "label_role_is_correct", "label_leakage", "error", "passed",
                "label_next_5d role must be label",
                {"role": role},
                "No action needed.",
            )
        return self._result(
            "label_role_is_correct", "label_leakage", "error", "failed",
            "label_next_5d role must be label",
            {"role": role},
            "Set label_next_5d role to 'label' in data_dictionary.",
        )

    # ---- 4. label_not_in_approved_features ----
    def _check_label_not_in_approved_features(self) -> dict[str, Any]:
        approved, _ = self._derive_approved_feature_columns()
        if "label_next_5d" not in approved:
            return self._result(
                "label_not_in_approved_features", "label_leakage", "error", "passed",
                "label_next_5d must not be in approved feature columns",
                {"approved": approved, "label_in_features": False},
                "No action needed.",
            )
        return self._result(
            "label_not_in_approved_features", "label_leakage", "error", "failed",
            "label_next_5d must not be in approved feature columns",
            {"label_in_features": True},
            "Remove label_next_5d from approved feature columns.",
        )

    # ---- 5. no_future_named_columns_in_features ----
    def _check_no_future_named_columns_in_features(self) -> dict[str, Any]:
        approved, _ = self._derive_approved_feature_columns()
        bad = [
            c for c in approved
            if any(h in c.lower() for h in FORBIDDEN_NAME_HINTS)
        ]
        if not bad:
            return self._result(
                "no_future_named_columns_in_features", "label_leakage", "error", "passed",
                "Approved features must not contain future/next/label/target names",
                {"approved": approved, "forbidden_hits": []},
                "No action needed.",
            )
        return self._result(
            "no_future_named_columns_in_features", "label_leakage", "error", "failed",
            "Approved features must not contain future/next/label/target names",
            {"forbidden_hits": bad},
            f"Remove columns with forbidden name hints: {bad}",
        )

    # ---- 6. approved_features_have_valid_roles ----
    def _check_approved_features_have_valid_roles(self) -> dict[str, Any]:
        approved, _ = self._derive_approved_feature_columns()
        invalid = []
        missing_in_dict = []
        for c in approved:
            entry = self.data_dictionary.get(c)
            if entry is None:
                missing_in_dict.append(c)
                continue
            role = entry.get("role")
            if role != "feature":
                invalid.append((c, role))
        if not invalid and not missing_in_dict:
            return self._result(
                "approved_features_have_valid_roles", "label_leakage", "error", "passed",
                "Approved features must have role=feature in data_dictionary",
                {"approved": approved, "invalid": [], "missing_in_dict": []},
                "No action needed.",
            )
        status = "failed" if invalid else "warning"
        return self._result(
            "approved_features_have_valid_roles", "label_leakage", "error", status,
            "Approved features must have role=feature in data_dictionary",
            {"invalid": invalid, "missing_in_dict": missing_in_dict},
            "Ensure every approved feature has role=feature in data_dictionary.",
        )

    # ---- 7. fundamentals_aligned_by_announce_date ----
    def _check_fundamentals_aligned_by_announce_date(self) -> dict[str, Any]:
        df = self.panel
        if (
            "source_fundamental_available" not in df.columns
            or "date" not in df.columns
        ):
            return self._result(
                "fundamentals_aligned_by_announce_date", "look_ahead_bias", "error", "failed",
                "Need source_fundamental_available and date columns",
                {},
                "Ensure executor aligns fundamentals by announce_date.",
            )
        # announce_date 列缺失：区分两种情况
        if "announce_date" not in df.columns:
            # 是否存在基本面值（pe/pb/roe 至少一个非空）
            has_fund_value = False
            if all(c in df.columns for c in ["pe", "pb", "roe"]):
                has_fund_value = bool(df[["pe", "pb", "roe"]].notna().any().any())
            if has_fund_value:
                # 存在基本面值却没有 announce_date → failed（防时间泄漏）
                return self._result(
                    "fundamentals_aligned_by_announce_date", "look_ahead_bias", "error", "failed",
                    "fundamentals present but announce_date column missing; cannot prove no look-ahead bias",
                    {"announce_date_column_present": False, "has_fundamental_values": True},
                    "Populate announce_date for all fundamental rows; never align by report_date.",
                )
            # 没有任何基本面值 → warning（空基本面是真实数据的正常情况，不阻塞）
            return self._result(
                "fundamentals_aligned_by_announce_date", "look_ahead_bias", "error", "warning",
                "no fundamentals available; announce_date absent is expected (empty fundamentals)",
                {"announce_date_column_present": False, "has_fundamental_values": False},
                "No action needed; fundamentals are empty by design or data source.",
            )
        fund_rows = df[df["source_fundamental_available"]].copy()
        # announce_date 缺失但 pe/pb/roe 存在 → warning
        pe_pb_roe_present = (
            fund_rows[["pe", "pb", "roe"]].notna().any(axis=1)
            if all(c in fund_rows.columns for c in ["pe", "pb", "roe"])
            else pd.Series([False] * len(fund_rows))
        )
        missing_announce_but_has_fund = int(
            (fund_rows["announce_date"].isna() & pe_pb_roe_present).sum()
        )
        # announce_date > date → fail
        both_present = fund_rows.dropna(subset=["announce_date", "date"])
        violations = int((both_present["announce_date"] > both_present["date"]).sum())

        if violations > 0:
            return self._result(
                "fundamentals_aligned_by_announce_date", "look_ahead_bias", "error", "failed",
                "For rows with fundamentals, announce_date must be <= date",
                {"violation_count": violations,
                 "missing_announce_but_has_fund": missing_announce_but_has_fund},
                "Re-align fundamentals using merge_asof on announce_date (backward).",
            )
        if missing_announce_but_has_fund > 0:
            return self._result(
                "fundamentals_aligned_by_announce_date", "look_ahead_bias", "error", "warning",
                "announce_date missing while pe/pb/roe present cannot prove no look-ahead",
                {"violation_count": violations,
                 "missing_announce_but_has_fund": missing_announce_but_has_fund},
                "Populate announce_date for all fundamental rows.",
            )
        return self._result(
            "fundamentals_aligned_by_announce_date", "look_ahead_bias", "error", "passed",
            "For rows with fundamentals, announce_date <= date",
            {"violation_count": violations,
             "missing_announce_but_has_fund": missing_announce_but_has_fund,
             "n_fund_rows": int(len(fund_rows))},
            "No action needed.",
        )

    # ---- 8. report_date_not_used_for_alignment ----
    def _check_report_date_not_used_for_alignment(self) -> dict[str, Any]:
        src = self.executor_source
        has_announce = "announce_date" in src
        has_asof = "merge_asof" in src
        # 检查是否用 report_date 直接 merge 到 date（粗略静态检查）
        # 匹配 report_date 出现在 merge/on/left_on/right_on 上下文
        report_merge_pattern = re.compile(
            r"merge\([^)]*report_date|on\s*=\s*['\"]report_date|left_on\s*=\s*['\"]report_date|right_on\s*=\s*['\"]report_date"
        )
        report_used_for_merge = bool(report_merge_pattern.search(src))
        # panel 不应含 report_date 列
        panel_has_report_date = "report_date" in self.panel.columns

        evidence = {
            "executor_has_announce_date": has_announce,
            "executor_has_merge_asof": has_asof,
            "report_date_used_in_merge": report_used_for_merge,
            "panel_has_report_date_column": panel_has_report_date,
        }
        if has_announce and has_asof and not report_used_for_merge and not panel_has_report_date:
            return self._result(
                "report_date_not_used_for_alignment", "look_ahead_bias", "error", "passed",
                "executor should use announce_date + merge_asof, not report_date",
                evidence,
                "No action needed.",
            )
        return self._result(
            "report_date_not_used_for_alignment", "look_ahead_bias", "error", "warning",
            "Cannot fully prove report_date is not used for alignment (static check)",
            evidence,
            "Verify executor aligns fundamentals by announce_date only.",
        )

    # ---- 9. rolling_features_past_only_static_check ----
    def _check_rolling_features_past_only_static(self) -> dict[str, Any]:
        src = self.executor_source
        has_groupby_ticker = bool(
            re.search(r'groupby\(\s*["\']ticker["\']', src)
        )
        has_rolling = "rolling(" in src
        # 检查是否存在用于 feature 的 shift(-k)（k>0）
        # label 也用 shift(-5)，所以只对"非 label 上下文"的 shift(-k) 报警。
        # 由于 shift(-5) 可能与 label_next_5d 赋值不在同一行（多行表达式），
        # 这里用窗口法：对每个 shift(-k) 匹配，检查其前后 ±5 行是否出现
        # label / label_next_5d，若出现则视为 label 上下文（允许）。
        lines = src.splitlines()
        shift_neg_lines = []
        non_label_shift_neg = []
        for i, line in enumerate(lines):
            if not re.search(r"shift\(\s*-\s*[1-9]", line):
                continue
            shift_neg_lines.append(line.strip())
            window = "\n".join(
                lines[max(0, i - 5): min(len(lines), i + 6)]
            )
            if "label" not in window.lower():
                non_label_shift_neg.append(line.strip())

        evidence = {
            "groupby_ticker_present": has_groupby_ticker,
            "rolling_present": has_rolling,
            "shift_negative_lines": shift_neg_lines,
            "non_label_shift_negative_lines": non_label_shift_neg,
        }
        if has_groupby_ticker and has_rolling and not non_label_shift_neg:
            return self._result(
                "rolling_features_past_only_static_check", "look_ahead_bias", "error", "passed",
                "rolling features grouped by ticker, no non-label shift(-k) found",
                evidence,
                "No action needed (static check).",
            )
        # 有非 label 的 shift(-k) → warning（静态检查无法完全证明是否进入 feature）
        return self._result(
            "rolling_features_past_only_static_check", "look_ahead_bias", "error", "warning",
            "Static check cannot fully prove rolling uses past-only; review shift(-k) usage",
            evidence,
            "Manually verify no shift(-k) feeds feature columns (only label may use future shift).",
        )

    # ---- 10. label_created_with_future_shift ----
    def _check_label_created_with_future_shift(self) -> dict[str, Any]:
        src = self.executor_source
        # label_next_5d 应由 shift(-5) 或等价逻辑生成
        label_block = re.search(
            r'label_next_5d["\']?\s*=\s*.*shift\(\s*-5', src
        )
        # label 只能被标记为 label（data_dictionary role 检查已在 check 3 覆盖）
        evidence = {
            "label_uses_shift_neg5": bool(label_block),
            "label_role_in_dict": self.data_dictionary.get("label_next_5d", {}).get("role"),
        }
        if label_block:
            return self._result(
                "label_created_with_future_shift", "label_leakage", "error", "passed",
                "label_next_5d should be created with shift(-5) and marked as label only",
                evidence,
                "No action needed.",
            )
        return self._result(
            "label_created_with_future_shift", "label_leakage", "error", "warning",
            "Cannot confirm label_next_5d is created with shift(-5) (static check)",
            evidence,
            "Verify label_next_5d = close.shift(-5)/close - 1 grouped by ticker.",
        )

    # ---- 11. trading_calendar_alignment ----
    def _check_trading_calendar_alignment(self) -> dict[str, Any]:
        if self.calendar is None:
            return self._result(
                "trading_calendar_alignment", "time_alignment", "warning", "warning",
                "calendar.csv not provided; cannot verify trading-day alignment",
                {},
                "Provide --calendar_path to enable this check.",
            )
        trading_days = set(
            self.calendar.loc[self.calendar["is_trading_day"] == 1, "date"]
            .dropna().dt.normalize().tolist()
        )
        panel_dates = set(
            self.panel["date"].dropna().dt.normalize().tolist()
        ) if "date" in self.panel.columns else set()
        non_trading = sorted(panel_dates - trading_days)
        if not non_trading:
            return self._result(
                "trading_calendar_alignment", "time_alignment", "warning", "passed",
                "All panel dates must be trading days",
                {"n_panel_dates": len(panel_dates),
                 "n_non_trading": 0},
                "No action needed.",
            )
        return self._result(
            "trading_calendar_alignment", "time_alignment", "warning", "failed",
            "All panel dates must be trading days",
            {"n_non_trading": len(non_trading),
             "sample_non_trading": [str(d)[:10] for d in non_trading[:5]]},
            "Filter panel to trading days only.",
        )

    # ---- 12. price_volume_sanity ----
    def _check_price_volume_sanity(self) -> dict[str, Any]:
        df = self.panel
        price_cols = [c for c in ["open", "high", "low", "close"] if c in df.columns]
        nonpos_price = 0
        for c in price_cols:
            s = pd.to_numeric(df[c], errors="coerce")
            nonpos_price += int((s <= 0).sum())
        vol_neg = int((pd.to_numeric(df["volume"], errors="coerce") < 0).sum()) if "volume" in df.columns else 0
        turn_neg = int((pd.to_numeric(df["turnover"], errors="coerce") < 0).sum()) if "turnover" in df.columns else 0
        evidence = {
            "non_positive_price_count": nonpos_price,
            "negative_volume_count": vol_neg,
            "negative_turnover_count": turn_neg,
        }
        if nonpos_price == 0 and vol_neg == 0 and turn_neg == 0:
            return self._result(
                "price_volume_sanity", "data_quality", "error", "passed",
                "open/high/low/close > 0; volume >= 0; turnover >= 0",
                evidence,
                "No action needed.",
            )
        return self._result(
            "price_volume_sanity", "data_quality", "error", "failed",
            "open/high/low/close > 0; volume >= 0; turnover >= 0",
            evidence,
            "Investigate and fix non-positive prices or negative volume/turnover.",
        )

    # ---- 13. missing_rate_after_join ----
    def _check_missing_rate_after_join(self) -> dict[str, Any]:
        df = self.panel
        rates = {c: round(float(df[c].isna().mean()), 4) for c in df.columns}
        close_rate = rates.get("close", 0.0)
        fund_rates = {c: rates.get(c, 0.0) for c in ["pe", "pb", "roe"]}
        industry_rate = rates.get("industry_name", 0.0)

        if close_rate > 0:
            return self._result(
                "missing_rate_after_join", "data_quality", "error", "failed",
                "close missing rate must be 0; pe/pb/roe high missing is warning (low announce freq)",
                {"close_missing_rate": close_rate,
                 "fundamental_missing_rates": fund_rates,
                 "industry_missing_rate": industry_rate},
                "Fix close missing values before modeling.",
            )
        warnings_bits = []
        if any(r > 0.2 for r in fund_rates.values()):
            warnings_bits.append("pe/pb/roe high missing (>20%)")
        if industry_rate > 0:
            warnings_bits.append("industry_name missing")
        if warnings_bits:
            return self._result(
                "missing_rate_after_join", "data_quality", "warning", "warning",
                "close missing rate must be 0; pe/pb/roe high missing is warning (low announce freq)",
                {"close_missing_rate": close_rate,
                 "fundamental_missing_rates": fund_rates,
                 "industry_missing_rate": industry_rate,
                 "warnings": warnings_bits},
                "Acceptable for baseline; consider imputation or wider announce coverage.",
            )
        return self._result(
            "missing_rate_after_join", "data_quality", "warning", "passed",
            "close missing rate must be 0; pe/pb/roe high missing is warning (low announce freq)",
            {"close_missing_rate": close_rate,
             "fundamental_missing_rates": fund_rates,
             "industry_missing_rate": industry_rate},
            "No action needed.",
        )

    # ---- 14. source_flags_consistency ----
    def _check_source_flags_consistency(self) -> dict[str, Any]:
        df = self.panel
        issues = []
        if "source_price_available" in df.columns:
            if not df["source_price_available"].all():
                issues.append("source_price_available has False values (expected all True)")
        if "source_volume_available" in df.columns and all(c in df.columns for c in ["volume", "turnover"]):
            vol_present = df["volume"].notna() | df["turnover"].notna()
            mismatch = int((df["source_volume_available"] != vol_present).sum())
            if mismatch > 0:
                issues.append(f"source_volume_available mismatches volume/turnover non-null in {mismatch} rows")
        if "source_fundamental_available" in df.columns and all(c in df.columns for c in ["pe", "pb", "roe"]):
            fund_present = df[["pe", "pb", "roe"]].notna().any(axis=1)
            mismatch = int((df["source_fundamental_available"] != fund_present).sum())
            if mismatch > 0:
                issues.append(f"source_fundamental_available mismatches pe/pb/roe in {mismatch} rows")
        if "source_industry_available" in df.columns and "industry_name" in df.columns:
            ind_present = df["industry_name"].notna()
            mismatch = int((df["source_industry_available"] != ind_present).sum())
            if mismatch > 0:
                issues.append(f"source_industry_available mismatches industry_name in {mismatch} rows")
        if not issues:
            return self._result(
                "source_flags_consistency", "data_quality", "warning", "passed",
                "source_* flags consistent with underlying columns",
                {"issues": []},
                "No action needed.",
            )
        return self._result(
            "source_flags_consistency", "data_quality", "warning", "warning",
            "source_* flags consistent with underlying columns",
            {"issues": issues},
            "Recompute source flags in executor.",
        )

    # ---- 15. time_based_split_required ----
    def _check_time_based_split_required(self) -> dict[str, Any]:
        # 在 plan 的 validation_plan.checks 中查找 time_based_train_test_split_required
        checks = self.plan.get("validation_plan", {}).get("checks", [])
        found = any(
            c.get("check_name") == "time_based_train_test_split_required"
            for c in checks
        )
        # 也看 assumptions
        assumptions = self.plan.get("planning_assumptions", [])
        assumption_mentions = [a for a in assumptions if "train/test" in a or "时间" in a]
        evidence = {
            "plan_has_time_based_split_check": found,
            "assumption_mentions": assumption_mentions,
        }
        if found:
            return self._result(
                "time_based_split_required", "temporal_validity", "error", "passed",
                "workflow_plan must require time-based train/test split",
                evidence,
                "Enforce time-based split in downstream modeling.",
            )
        return self._result(
            "time_based_split_required", "temporal_validity", "error", "warning",
            "workflow_plan must require time-based train/test split",
            evidence,
            "Add time_based_train_test_split_required to validation_plan.",
        )

    # ==================================================================
    # approved feature columns 推导
    # ==================================================================

    def _derive_approved_feature_columns(self) -> tuple[list[str], list[str]]:
        """从 data_dictionary 推导 approved features 与 excluded columns。

        approved = 白名单 ∩ (data_dictionary role=feature)
        excluded = 其余所有列
        """
        feature_cols = [
            c for c, v in self.data_dictionary.items()
            if v.get("role") == "feature"
        ]
        # 白名单 ∩ feature role，保持白名单顺序
        approved = [c for c in FEATURE_WHITELIST if c in feature_cols]
        excluded = [
            c for c in self.data_dictionary.keys() if c not in approved
        ]
        return approved, excluded

    # ==================================================================
    # limitations
    # ==================================================================

    def _limitations(self) -> list[str]:
        return [
            "Current Critic is a deterministic baseline; no LLM is called.",
            "Judgment on whether rolling fully avoids future data partly relies on static source checks.",
            "No model is trained in this stage.",
            "No real business data is used; only real market data fetched via the adapter.",
            "No investment advice is produced.",
        ]

    # ==================================================================
    # Markdown 渲染
    # ==================================================================

    def _render_markdown(self, report: dict[str, Any]) -> str:
        lines: list[str] = []
        lines.append("# Validity Critic Report")
        lines.append("")
        lines.append(
            f"- project: `{report['project']}`  |  critic_version: `{report['critic_version']}`"
        )
        lines.append("")

        # 1. Overall Status
        s = report["summary"]
        lines.append("## 1. Overall Status")
        lines.append("")
        lines.append(f"- **overall_status**: `{report['overall_status']}`")
        lines.append(f"- total checks: {s['total_checks']}")
        lines.append(f"- passed: {s['passed']}")
        lines.append(f"- warnings: {s['warnings']}")
        lines.append(f"- failed: {s['failed']}")
        lines.append("")

        # 2. What This Critic Checks
        lines.append("## 2. What This Critic Checks")
        lines.append("")
        lines.append(
            "This stage is **not** ordinary table-quality checking. It checks whether the prepared panel "
            "satisfies downstream modeling validity requirements — especially **future-function (look-ahead bias)** "
            "and **label leakage** — plus temporal validity and data-leakage risk."
        )
        lines.append("")

        # 3. Input Files
        lines.append("## 3. Input Files")
        lines.append("")
        for k, v in report["input_files"].items():
            lines.append(f"- {k}: `{v}`")
        lines.append("")

        # 4. Key Validation Results
        lines.append("## 4. Key Validation Results")
        lines.append("")
        lines.append("| check_name | category | severity | status | recommendation |")
        lines.append("|---|---|---|---|---|")
        for c in report["checks"]:
            rec = c["recommendation"]
            if len(rec) > 60:
                rec = rec[:57] + "..."
            lines.append(
                f"| {c['check_name']} | {c['category']} | {c['severity']} | {c['status']} | {rec} |"
            )
        lines.append("")

        # 5. Leakage and Temporal Validity
        lines.append("## 5. Leakage and Temporal Validity")
        lines.append("")
        by_name = {c["check_name"]: c for c in report["checks"]}
        for name in [
            "label_not_in_approved_features",
            "no_future_named_columns_in_features",
            "fundamentals_aligned_by_announce_date",
            "report_date_not_used_for_alignment",
            "rolling_features_past_only_static_check",
            "label_created_with_future_shift",
            "time_based_split_required",
        ]:
            c = by_name.get(name)
            if c:
                lines.append(f"- **{name}**: `{c['status']}` — {c['description']}")
                if c["evidence"]:
                    ev = ", ".join(f"{k}={v}" for k, v in c["evidence"].items())
                    lines.append(f"  - evidence: {ev}")
                lines.append(f"  - recommendation: {c['recommendation']}")
        lines.append("")

        # 6. Data Quality Findings
        lines.append("## 6. Data Quality Findings")
        lines.append("")
        for name in [
            "primary_key_uniqueness",
            "missing_rate_after_join",
            "price_volume_sanity",
            "source_flags_consistency",
            "trading_calendar_alignment",
        ]:
            c = by_name.get(name)
            if c:
                lines.append(f"- **{name}**: `{c['status']}` — {c['description']}")
                if c["evidence"]:
                    ev = ", ".join(f"{k}={v}" for k, v in c["evidence"].items())
                    lines.append(f"  - evidence: {ev}")
        lines.append("")
        lines.append("Notes:")
        lines.append("- pe/pb/roe sparsity is expected (low announce frequency); flagged as warning, not failure.")
        lines.append("- industry_name missing reflects the simulated data design (one ticker missing industry).")
        lines.append("")

        # 7. Approved Feature Columns
        lines.append("## 7. Approved Feature Columns")
        lines.append("")
        lines.append(f"- label_column: `{report['label_column']}`")
        lines.append("- approved_feature_columns:")
        for c in report["approved_feature_columns"]:
            lines.append(f"  - `{c}`")
        lines.append("- excluded_columns:")
        for c in report["excluded_columns"]:
            lines.append(f"  - `{c}`")
        lines.append("")

        # 8. Limitations
        lines.append("## 8. Limitations")
        lines.append("")
        for l in report["limitations"]:
            lines.append(f"- {l}")
        lines.append("")

        # 9. Next Stage
        lines.append("## 9. Next Stage")
        lines.append("")
        lines.append("- **Multi Planner Voting**: multiple planners produce plans, vote/pick best.")
        lines.append("- **LLM Planner / LLM Critic**: replace rule-based components with LLM-driven ones.")
        lines.append("- **Baseline comparison**: rule-based vs single-agent vs multi-agent + critic.")
        lines.append("")

        return "\n".join(lines)
