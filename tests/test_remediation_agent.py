"""Remediation Agent 单元测试（v2 升级，严格版）。

覆盖：
1. 初始 Critic 已通过时执行 0 轮（validation_passed）。
2. sample 数据经过一轮修复后收敛（validation_passed after 1 round）。
3. 没有可用策略时安全停止（no_actionable_strategy）。
4. failed checks + 内容指纹连续两轮不变 → 严格断言 no_progress。
5. 每轮有变化但始终 failed → 严格断言 max_rounds_reached。
6. 删除行数超过 5% 时转人工确认（manual_review_required）。
7. label_next_5d 始终不在 approved features。
8. blocked 或 failed 时仍然保存 repair_history.json。
9. TrimIndustryNameWhitespace 不把缺失值伪造为 "None"/"nan"/"<NA>"。
10. run_all 退出码：0 / 1 / 2 三态。
11. Agent Shell 从 repair_history.json 恢复历史状态。
12. CLI 参数边界：--max_repair_rounds 0 / --max_row_loss_ratio 越界尽早失败。

使用标准库 unittest + unittest.mock。所有测试用临时 output_root，不覆盖仓库内现有 outputs。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 让测试无论从哪里运行都能 import src 模块
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
for p in (str(SRC), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd  # noqa: E402

from generate_sample_data import generate_sample_data  # noqa: E402
from pipeline_runner import PipelineRunner  # noqa: E402
from repair import (  # noqa: E402
    DEFAULT_STRATEGIES,
    DropExactDuplicateRows,
    DropRowsWithMissingCorePrice,
    RepairLoop,
    TrimIndustryNameWhitespace,
)


def _run_to_initial_critic(runner: PipelineRunner) -> None:
    """跑 profile → planner → executor → initial_critic，停在修复前。"""
    runner.run_profile()
    runner.run_planner()
    runner.run_executor()
    runner.run_initial_critic()


# ======================================================================
# Fake Critic：可注入 PipelineRunner._run_critic，控制每轮复审结果
# ======================================================================


class FakeCriticReport:
    """构造一个固定的 validation_report dict。

    failed_names 中若包含 missing_rate_after_join，会自动带上
    close_missing_rate>0 的 evidence，使 DropRowsWithMissingCorePrice.can_handle
    返回 True（否则策略不匹配 → no_actionable_strategy，而非 max_rounds）。

    注意：failed_names 中不在 ALL_NAMES 里的 check 名（如 synthetic_check_n）
    会被追加为额外的 failed check 项，确保 failed 集合可变化。
    """

    def __init__(self, overall_status: str, failed_names: list[str]):
        self.overall_status = overall_status
        self.failed_names = failed_names

    def to_report(self) -> dict:
        checks = []
        all_names = [
            "primary_key_uniqueness",
            "required_columns_exist",
            "label_role_is_correct",
            "label_not_in_approved_features",
            "no_future_named_columns_in_features",
            "approved_features_have_valid_roles",
            "fundamentals_aligned_by_announce_date",
            "report_date_not_used_for_alignment",
            "rolling_features_past_only_static_check",
            "label_created_with_future_shift",
            "trading_calendar_alignment",
            "price_volume_sanity",
            "missing_rate_after_join",
            "source_flags_consistency",
            "time_based_split_required",
        ]
        emitted = set()
        for name in all_names:
            status = "failed" if name in self.failed_names else "passed"
            evidence = {}
            if name == "missing_rate_after_join" and status == "failed":
                # 策略 can_handle 依赖 close_missing_rate > 0
                evidence = {"close_missing_rate": 0.5}
            checks.append(
                {
                    "check_name": name,
                    "category": "data_quality",
                    "severity": "error",
                    "status": status,
                    "description": f"{name} check",
                    "evidence": evidence,
                    "recommendation": "",
                }
            )
            emitted.add(name)
        # 追加 failed_names 中不在 ALL_NAMES 的 check（如 synthetic_check_n）
        for name in self.failed_names:
            if name in emitted:
                continue
            checks.append(
                {
                    "check_name": name,
                    "category": "data_quality",
                    "severity": "error",
                    "status": "failed",
                    "description": f"{name} check",
                    "evidence": {},
                    "recommendation": "",
                }
            )
            emitted.add(name)
        n_failed = len(self.failed_names)
        return {
            "project": "financial_table_workflow_agent",
            "critic_version": "0.1",
            "input_files": {},
            "overall_status": self.overall_status,
            "summary": {
                "total_checks": len(checks),
                "passed": len(checks) - n_failed,
                "warnings": 0,
                "failed": n_failed,
            },
            "checks": checks,
            "approved_feature_columns": [
                "return_1d", "return_5d", "volatility_20d", "turnover_20d",
                "pe", "pb", "roe", "industry_name",
            ],
            "excluded_columns": ["label_next_5d"],
            "label_column": "label_next_5d",
            "limitations": [],
        }


class FakeCritic:
    """假 Critic：忽略 load_inputs，run_all_checks 返回预设 report。

    save_* 把 report 写到指定路径（与真实 Critic 接口一致）。
    """

    def __init__(self, report_factory):
        self._report_factory = report_factory
        self.report = None

    def load_inputs(self, **kwargs):
        pass

    def run_all_checks(self):
        self.report = self._report_factory()
        return self.report

    def save_json_report(self, report, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    def save_markdown_report(self, report, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("# fake critic report\n", encoding="utf-8")

    def save_approved_feature_columns(self, report, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "approved_feature_columns": report["approved_feature_columns"],
            "excluded_columns": report["excluded_columns"],
            "label_column": report["label_column"],
            "notes": [],
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


def _make_panel(n_rows: int = 300, with_close_missing: int = 0) -> pd.DataFrame:
    """构造一个最小合法 prepared_panel（含 Critic REQUIRED_COLUMNS）。"""
    dates = pd.bdate_range("2024-01-02", periods=max(1, n_rows // 5))
    tickers = ["000001", "600000", "AAPL", "510300", "000333"]
    rows = []
    for i in range(n_rows):
        d = dates[i % len(dates)]
        t = tickers[i % len(tickers)]
        close = 10.0 + (i % 7)
        rows.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "ticker": t,
                "open": close, "high": close, "low": close, "close": close,
                "volume": 1000, "turnover": 10000.0,
                "return_1d": 0.01, "return_5d": 0.02,
                "volatility_20d": 0.03, "turnover_20d": 10000.0,
                "pe": 15.0, "pb": 2.0, "roe": 10.0,
                "industry_name": "银行",
                "label_next_5d": 0.05,
                "source_price_available": True,
                "source_volume_available": True,
                "source_fundamental_available": True,
                "source_industry_available": True,
                "announce_date": "2024-01-02",
            }
        )
    df = pd.DataFrame(rows)
    if with_close_missing > 0:
        idx = df.sample(n=min(with_close_missing, len(df)), random_state=0).index
        df.loc[idx, "close"] = None
    return df


# ======================================================================
# 策略注册表测试
# ======================================================================


class TestStrategyRegistry(unittest.TestCase):
    def test_default_strategies_present(self):
        names = [s.name for s in DEFAULT_STRATEGIES]
        self.assertIn("drop_rows_with_missing_core_price", names)
        self.assertIn("drop_exact_duplicate_rows", names)
        self.assertIn("trim_industry_name_whitespace", names)

    def test_drop_missing_core_price_preserved(self):
        strat = DropRowsWithMissingCorePrice()
        self.assertEqual(strat.name, "drop_rows_with_missing_core_price")
        self.assertEqual(strat.target_check, "missing_rate_after_join")
        self.assertFalse(strat.requires_confirmation)
        df = pd.DataFrame(
            {"date": ["2024-01-02", "2024-01-03"], "ticker": ["A", "A"], "close": [10.0, None]}
        )
        fc = {"check_name": "missing_rate_after_join", "evidence": {"close_missing_rate": 0.5}}
        self.assertTrue(strat.can_handle(fc, df))
        new_df, detail = strat.apply(df, fc)
        self.assertEqual(len(new_df), 1)
        self.assertEqual(detail["rows_removed"], 1)
        self.assertEqual(detail["status"], "applied")

    def test_drop_exact_duplicates_only_full(self):
        strat = DropExactDuplicateRows()
        df = pd.DataFrame(
            {"date": ["d1", "d1", "d2"], "ticker": ["A", "A", "A"], "close": [1.0, 1.0, 2.0]}
        )
        fc = {"check_name": "primary_key_uniqueness", "evidence": {"duplicate_count": 1}}
        self.assertTrue(strat.can_handle(fc, df))
        new_df, detail = strat.apply(df, fc)
        self.assertEqual(len(new_df), 2)
        self.assertEqual(detail["rows_removed"], 1)

    def test_trim_whitespace(self):
        strat = TrimIndustryNameWhitespace()
        df = pd.DataFrame({"industry_name": [" 银行 ", "家电", " 信息技术 "]})
        fc = {"check_name": "source_flags_consistency", "evidence": {}}
        self.assertTrue(strat.can_handle(fc, df))
        new_df, detail = strat.apply(df, fc)
        self.assertEqual(list(new_df["industry_name"]), ["银行", "家电", "信息技术"])
        self.assertEqual(detail["rows_modified"], 2)

    # 9. Trim 不把缺失值伪造为 "None"/"nan"/"<NA>"
    def test_trim_preserves_missing_values(self):
        strat = TrimIndustryNameWhitespace()
        # 含 None / NaN / pd.NA / 空字符串 / 正常带空格值
        df = pd.DataFrame(
            {"industry_name": [None, float("nan"), pd.NA, "", " 银行 ", "家电"]}
        )
        fc = {"check_name": "source_flags_consistency", "evidence": {}}
        self.assertTrue(strat.can_handle(fc, df))
        new_df, detail = strat.apply(df, fc)
        vals = list(new_df["industry_name"])
        # 缺失值必须保持缺失
        self.assertTrue(pd.isna(vals[0]), f"None became {vals[0]!r}")
        self.assertTrue(pd.isna(vals[1]), f"nan became {vals[1]!r}")
        self.assertTrue(pd.isna(vals[2]), f"pd.NA became {vals[2]!r}")
        # 空字符串清理后转为缺失
        self.assertTrue(pd.isna(vals[3]), f"empty became {vals[3]!r}")
        # 带空格的值被 strip
        self.assertEqual(vals[4], "银行")
        self.assertEqual(vals[5], "家电")
        # 明确断言缺失值没有变成伪造字符串：用 pandas isna 判定，而非 str()
        for i in (0, 1, 2, 3):
            self.assertTrue(
                pd.isna(vals[i]),
                f"index {i} should be missing, got {vals[i]!r} (str repr: {str(vals[i])!r})",
            )

    def test_trim_no_fabrication_on_all_missing(self):
        """全缺失列：can_handle 返回 False（没有可 trim 的非空字符串）。"""
        strat = TrimIndustryNameWhitespace()
        df = pd.DataFrame({"industry_name": [None, float("nan"), pd.NA]})
        fc = {"check_name": "source_flags_consistency", "evidence": {}}
        self.assertFalse(strat.can_handle(fc, df))


# ======================================================================
# Remediation Agent 行为测试
# ======================================================================


class TestRemediationAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="remediation_"))
        self.input_dir = self.tmp / "data" / "sample"
        self.output_root = self.tmp / "outputs"
        generate_sample_data(self.input_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _new_runner(self, **kwargs):
        return PipelineRunner(
            input_dir=self.input_dir,
            output_root=self.output_root,
            verbose=False,
            **kwargs,
        )

    # 1. 初始 Critic 已通过时执行 0 轮
    def test_zero_rounds_when_initial_passed(self):
        # 清理 price.csv 的 OHLC 缺失行，使 initial critic 不 failed
        price_path = self.input_dir / "price.csv"
        df = pd.read_csv(price_path)
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        df.to_csv(price_path, index=False, encoding="utf-8-sig")

        runner = self._new_runner()
        runner.run_full_pipeline()
        initial_status = runner.stages["initial_critic"]["summary"].get("overall_status")
        self.assertNotEqual(initial_status, "failed")
        self.assertEqual(runner.repair_rounds_run, 0)
        self.assertEqual(runner.termination_reason, "validation_passed")
        self.assertTrue(runner.repair_history_json.exists())
        with runner.repair_history_json.open("r", encoding="utf-8") as f:
            rh = json.load(f)
        self.assertEqual(rh["repair_rounds"], 0)
        self.assertEqual(rh["termination_reason"], "validation_passed")

    # 2. sample 数据经过一轮修复后收敛
    def test_sample_converges_after_one_round(self):
        runner = self._new_runner()
        runner.run_full_pipeline()
        self.assertEqual(runner.repair_rounds_run, 1)
        self.assertEqual(runner.termination_reason, "validation_passed")
        self.assertFalse(runner.manual_review_required)
        self.assertEqual(runner.unresolved_checks, [])
        with runner.repair_history_json.open("r", encoding="utf-8") as f:
            rh = json.load(f)
        self.assertEqual(rh["rounds"][0]["rows_before"], 300)
        self.assertEqual(rh["rounds"][0]["rows_after"], 298)
        self.assertEqual(rh["rounds"][0]["cumulative_row_loss_ratio"], round(2 / 300, 6))

    # 3. 没有可用策略时安全停止（no_actionable_strategy）
    def test_no_actionable_strategy_safe_stop(self):
        runner = self._new_runner()
        _run_to_initial_critic(runner)
        # 篡改 initial validation report：注入一个未知 failed check
        with runner.initial_validation_json.open("r", encoding="utf-8") as f:
            report = json.load(f)
        for c in report["checks"]:
            if c["check_name"] == "missing_rate_after_join":
                c["status"] = "passed"
                c["evidence"] = {"close_missing_rate": 0.0}
        report["checks"].append(
            {
                "check_name": "unknown_check_xyz",
                "category": "data_quality",
                "severity": "error",
                "status": "failed",
                "description": "an unknown failed check no strategy can handle",
                "evidence": {},
                "recommendation": "manual review",
            }
        )
        report["overall_status"] = "failed"
        report["summary"]["failed"] = 1
        with runner.initial_validation_json.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        runner._run_remediation_agent()
        self.assertEqual(runner.termination_reason, "no_actionable_strategy")
        self.assertTrue(runner.manual_review_required)
        self.assertIn("unknown_check_xyz", runner.unresolved_checks)
        self.assertTrue(runner.repair_history_json.exists())

    # 4. 严格 no_progress：连续两轮 failed checks + 指纹不变
    def test_no_progress_stop_strict(self):
        """用 fake critic 注入：每轮复审都返回相同的 failed check + 相同 panel。

        策略 apply 删 0 行（panel 不变），fake critic 每轮返回相同 failed
        （含 missing_rate_after_join + close_missing_rate>0 evidence，使策略
        can_handle=True），→ 第二轮 failed check 集合 + 指纹与第一轮相同 → no_progress。
        """
        runner = self._new_runner()
        _run_to_initial_critic(runner)
        # 把 prepared_panel 改成无 close 缺失（策略 apply 删 0 行，指纹不变）
        df = pd.read_csv(runner.prepared_panel)
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        df.to_csv(runner.prepared_panel, index=False, encoding="utf-8-sig")

        # 篡改 initial report：声称 missing_rate_after_join failed（close_missing_rate=0.5）
        with runner.initial_validation_json.open("r", encoding="utf-8") as f:
            report = json.load(f)
        for c in report["checks"]:
            if c["check_name"] == "missing_rate_after_join":
                c["status"] = "failed"
                c["evidence"] = {"close_missing_rate": 0.5}
        report["overall_status"] = "failed"
        with runner.initial_validation_json.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # fake critic：每轮复审都返回 failed + missing_rate_after_join（带 evidence）
        fake_report = FakeCriticReport("failed", ["missing_rate_after_join"]).to_report()
        call_count = {"n": 0}

        def factory():
            call_count["n"] += 1
            return FakeCritic(lambda: fake_report)

        runner._critic_factory = factory
        runner._run_remediation_agent()

        # 严格断言 no_progress（不接受其他结果）
        self.assertEqual(
            runner.termination_reason,
            "no_progress",
            f"expected no_progress, got {runner.termination_reason}",
        )
        self.assertTrue(runner.manual_review_required)
        self.assertEqual(runner.unresolved_checks, ["missing_rate_after_join"])
        # fake critic 至少被调用 2 次（两轮 reflect）
        self.assertGreaterEqual(call_count["n"], 2)
        self.assertTrue(runner.repair_history_json.exists())

    # 5. 严格 max_rounds_reached：每轮 failed check 集合变化但始终 failed
    def test_max_rounds_reached_strict(self):
        """用 fake critic 注入：每轮复审返回不同 failed check 名，但始终 failed。

        failed check 集合每轮变化 → 不触发 no_progress；始终 failed → 不触发
        validation_passed；达到 max_repair_rounds → max_rounds_reached。

        关键设计：
        - 每轮 failed 必须含 missing_rate_after_join（带 close_missing_rate>0
          evidence），否则策略 can_handle=False → no_actionable_strategy。
        - 每轮 failed 还含 synthetic_check_n（n 递增），使 failed 集合变化。
        - panel 指纹必须每轮变化，否则即使 failed 集合变化也会因指纹不变
          触发 no_progress。我们注入足够多的 close 缺失行（>max_rounds），
          使策略每轮删 1 行、指纹变化，但累计删行仍 < 5% 安全门。
        - 注意：fake critic 每轮返回的 failed_after 必须包含 synthetic_check_n
          （n 递增），否则 failed_after 集合不变 → no_progress。但真实 Critic
          会重算 missing_rate_after_join（删完 close 缺失后 passed），所以这里
          用 fake critic 强制每轮 failed_after = [missing_rate_after_join,
          synthetic_check_n]，使 failed 集合变化 + panel 指纹变化 → 不触发
          no_progress → 达到 max_rounds → max_rounds_reached。
        """
        runner = self._new_runner(max_repair_rounds=2)
        _run_to_initial_critic(runner)
        # 注入 5 行 close 缺失（5/300≈1.7% < 5% 安全门；> max_rounds=2）
        df = pd.read_csv(runner.prepared_panel)
        cur_missing = int(df["close"].isna().sum())
        need = max(0, 5 - cur_missing)
        if need > 0:
            extra = df[df["close"].notna()].sample(n=need, random_state=0).index
            df.loc[extra, "close"] = None
        df.to_csv(runner.prepared_panel, index=False, encoding="utf-8-sig")

        with runner.initial_validation_json.open("r", encoding="utf-8") as f:
            report = json.load(f)
        for c in report["checks"]:
            if c["check_name"] == "missing_rate_after_join":
                c["status"] = "failed"
                c["evidence"] = {"close_missing_rate": 0.5}
        report["overall_status"] = "failed"
        with runner.initial_validation_json.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # fake critic：每轮返回 [missing_rate_after_join, synthetic_check_n]，
        # n 递增使 failed check 集合变化（避免 no_progress），但始终 failed。
        call_count = {"n": 0}

        def factory():
            call_count["n"] += 1
            n = call_count["n"]
            failed = ["missing_rate_after_join", f"synthetic_check_{n}"]
            return FakeCritic(lambda: FakeCriticReport("failed", failed).to_report())

        runner._critic_factory = factory
        runner._run_remediation_agent()

        # 严格断言 max_rounds_reached
        self.assertEqual(
            runner.termination_reason,
            "max_rounds_reached",
            f"expected max_rounds_reached, got {runner.termination_reason}",
        )
        self.assertEqual(runner.repair_rounds_run, 2)
        self.assertTrue(runner.manual_review_required)
        self.assertTrue(runner.repair_history_json.exists())

    # 6. 删除行数超过 5% 时转人工确认
    def test_row_loss_over_5_percent_manual_review(self):
        runner = self._new_runner(max_row_loss_ratio=0.05)
        _run_to_initial_critic(runner)
        panel_path = runner.prepared_panel
        df = pd.read_csv(panel_path)
        n = len(df)
        n_inject = int(n * 0.10)
        inject_idx = df.sample(n=n_inject, random_state=0).index
        df.loc[inject_idx, "close"] = None
        df.to_csv(panel_path, index=False, encoding="utf-8-sig")
        runner.run_initial_critic()
        initial_status = runner.stages["initial_critic"]["summary"].get("overall_status")
        self.assertEqual(initial_status, "failed")

        runner._run_remediation_agent()
        self.assertEqual(runner.termination_reason, "manual_review_required")
        self.assertTrue(runner.manual_review_required)
        self.assertTrue(runner.repair_history_json.exists())
        with runner.repair_history_json.open("r", encoding="utf-8") as f:
            rh = json.load(f)
        last = rh["rounds"][-1]
        self.assertIn("safety", last["decision_reason"].lower())
        # 超限修复结果没有被保存：repaired_panel 仍是原始 n 行 + 注入的缺失数。
        # 注意：repaired_panel 从磁盘读回时 close 缺失数可能因 dtype 略有出入，
        # 断言"行数不变 + 仍有大量缺失"即可证明超限结果未保存。
        rp = pd.read_csv(runner.repaired_panel)
        self.assertEqual(len(rp), n, "over-limit repaired panel must not be saved")
        self.assertGreaterEqual(int(rp["close"].isna().sum()), n_inject)

    # 7. label_next_5d 始终不在 approved features
    def test_label_never_in_approved_features(self):
        runner = self._new_runner()
        runner.run_full_pipeline()
        with runner.final_approved.open("r", encoding="utf-8") as f:
            approved = json.load(f)
        self.assertNotIn("label_next_5d", approved["approved_feature_columns"])
        df = pd.read_csv(runner.repaired_panel)
        self.assertIn("label_next_5d", df.columns)

    # 8. blocked 或 failed 时仍然保存 repair_history.json
    def test_repair_history_saved_even_when_manual_review(self):
        runner = self._new_runner(max_row_loss_ratio=0.05)
        _run_to_initial_critic(runner)
        panel_path = runner.prepared_panel
        df = pd.read_csv(panel_path)
        n_inject = int(len(df) * 0.10)
        inject_idx = df.sample(n=n_inject, random_state=0).index
        df.loc[inject_idx, "close"] = None
        df.to_csv(panel_path, index=False, encoding="utf-8-sig")
        runner.run_initial_critic()
        runner._run_remediation_agent()
        self.assertTrue(runner.repair_history_json.exists())
        with runner.repair_history_json.open("r", encoding="utf-8") as f:
            rh = json.load(f)
        self.assertEqual(rh["termination_reason"], "manual_review_required")
        self.assertTrue(rh["manual_review_required"])


# ======================================================================
# run_all 退出码测试（0 / 1 / 2）
# ======================================================================


class TestExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="exitcode_"))
        self.input_dir = self.tmp / "data" / "sample"
        self.output_root = self.tmp / "outputs"
        generate_sample_data(self.input_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_all(self, *extra_args) -> int:
        cmd = [
            sys.executable, "-B", "src/run_all.py",
            "--input_dir", str(self.input_dir),
            "--output_root", str(self.output_root),
        ] + list(extra_args)
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(HERE.parent))
        return r.returncode

    def test_exit_0_when_passed(self):
        # 清理 OHLC 缺失 → initial passed → 0 轮 → EXIT=0
        df = pd.read_csv(self.input_dir / "price.csv")
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        df.to_csv(self.input_dir / "price.csv", index=False, encoding="utf-8-sig")
        code = self._run_all()
        self.assertEqual(code, 0)

    def test_exit_0_when_one_round_converges(self):
        # sample 数据一轮收敛 → EXIT=0
        code = self._run_all()
        self.assertEqual(code, 0)

    def test_exit_2_when_manual_review(self):
        # 注入 10% close 缺失 → manual_review_required → EXIT=2
        df = pd.read_csv(self.input_dir / "price.csv")
        idx = df.sample(n=int(len(df) * 0.10), random_state=0).index
        df.loc[idx, "close"] = None
        df.to_csv(self.input_dir / "price.csv", index=False, encoding="utf-8-sig")
        code = self._run_all()
        self.assertEqual(code, 2)

    def test_exit_2_when_no_repair_and_initial_failed(self):
        # --no_repair + initial failed → EXIT=2
        code = self._run_all("--no_repair")
        self.assertEqual(code, 2)
        with open(self.output_root / "repaired" / "repair_history.json", encoding="utf-8") as f:
            rh = json.load(f)
        self.assertEqual(rh["termination_reason"], "repair_disabled")
        self.assertTrue(rh["manual_review_required"])
        self.assertEqual(rh["unresolved_checks"], ["missing_rate_after_join"])

    def test_exit_1_when_max_repair_rounds_zero(self):
        code = self._run_all("--max_repair_rounds", "0")
        self.assertEqual(code, 1)

    def test_exit_1_when_max_row_loss_ratio_out_of_range(self):
        code = self._run_all("--max_row_loss_ratio", "1.5")
        self.assertEqual(code, 1)
        code2 = self._run_all("--max_row_loss_ratio", "-0.1")
        self.assertEqual(code2, 1)


# ======================================================================
# Agent Shell 状态恢复测试
# ======================================================================


class TestShellStateRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shellrestore_"))
        self.input_dir = self.tmp / "data" / "sample"
        self.output_root = self.tmp / "outputs"
        generate_sample_data(self.input_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_restores_from_repair_history(self):
        # 先跑一次完整 pipeline，产生 repair_history.json
        r = subprocess.run(
            [sys.executable, "-B", "src/run_all.py",
             "--input_dir", str(self.input_dir),
             "--output_root", str(self.output_root)],
            capture_output=True, text=True, cwd=str(HERE.parent),
        )
        self.assertEqual(r.returncode, 0)
        # 新建 runner 指向已有 outputs，不跑 pipeline，直接 get_status
        runner = PipelineRunner(
            input_dir=self.input_dir, output_root=self.output_root, verbose=False
        )
        status = runner.get_status()
        # 内存未运行 → 从 repair_history.json 恢复
        self.assertEqual(status["repair_rounds"], 1)
        self.assertEqual(status["termination_reason"], "validation_passed")
        self.assertFalse(status["manual_review_required"])

    def test_shell_demo_shows_restored_state(self):
        # 先跑一次完整 pipeline
        subprocess.run(
            [sys.executable, "-B", "src/run_all.py",
             "--input_dir", str(self.input_dir),
             "--output_root", str(self.output_root)],
            capture_output=True, text=True, cwd=str(HERE.parent),
        )
        # 用 agent_shell --demo_commands 指向已有 outputs
        r = subprocess.run(
            [sys.executable, "-B", "src/agent_shell.py",
             "--input_dir", str(self.input_dir),
             "--output_root", str(self.output_root),
             "--demo_commands"],
            capture_output=True, text=True, cwd=str(HERE.parent),
        )
        out = r.stdout
        # status 与 show summary 都应显示 repair_rounds=1, termination=validation_passed
        self.assertIn("repair rounds: 1", out)
        self.assertIn("termination reason: validation_passed", out)
        self.assertIn("repair rounds:             1", out)
        self.assertIn("termination reason:        validation_passed", out)


# ======================================================================
# repair_history.json schema 测试
# ======================================================================


class TestRepairHistorySchema(unittest.TestCase):
    def test_history_has_required_fields(self):
        tmp = Path(tempfile.mkdtemp(prefix="schema_"))
        try:
            input_dir = tmp / "data" / "sample"
            output_root = tmp / "outputs"
            generate_sample_data(input_dir)
            runner = PipelineRunner(
                input_dir=input_dir, output_root=output_root, verbose=False
            )
            runner.run_full_pipeline()
            with runner.repair_history_json.open("r", encoding="utf-8") as f:
                rh = json.load(f)
            for k in [
                "project", "repair_version", "max_repair_rounds",
                "max_row_loss_ratio", "repair_rounds", "termination_reason",
                "manual_review_required", "unresolved_checks", "rounds",
            ]:
                self.assertIn(k, rh, f"missing top-level field {k}")
            if rh["rounds"]:
                r = rh["rounds"][0]
                for k in [
                    "round", "validation_status_before", "failed_checks_before",
                    "candidate_strategies", "selected_strategies", "decision_reason",
                    "rows_before", "rows_after", "cumulative_row_loss_ratio",
                    "validation_status_after", "failed_checks_after",
                    "panel_fingerprint", "termination_reason",
                ]:
                    self.assertIn(k, r, f"missing round field {k}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
