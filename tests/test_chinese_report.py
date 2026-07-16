"""中文最终报告测试（Stage 12）。

覆盖：
23. 中文最终报告包含主要中文标题。
24. 中文一页摘要包含主要中文标题。
25. 报告所有数值仍来自真实产物（不硬编码）。
26. final_workflow_summary.json 结构保持兼容。
27. label_next_5d 永远不进入 approved_feature_columns。
- 数据来源章节：有 fetch_metadata 时显示抓取来源；无时显示"用户提供的已有 CSV"。
- passed_with_warnings 显示为"passed_with_warnings（通过但有警告）"，不覆盖原始值。
- 基本面时间边界说明存在。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
for p in (str(SRC), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from pipeline_runner import PipelineRunner  # noqa: E402
from report_generator import ReportGenerator  # noqa: E402

FIXTURE_DIR = HERE.parent / "test_data" / "real_market_sample"


def _copy_fixture(tmp_dir: Path, subdir: str = "input") -> Path:
    dst = tmp_dir / subdir
    shutil.copytree(FIXTURE_DIR, dst)
    return dst


class TestChineseReport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zhrep_"))
        self.input_dir = _copy_fixture(self.tmp, "input")
        self.output_root = self.tmp / "outputs"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_pipeline(self) -> PipelineRunner:
        runner = PipelineRunner(
            input_dir=self.input_dir,
            output_root=self.output_root,
            auto_repair=True,
        )
        runner.run_full_pipeline()
        return runner

    def _load_report(self, runner: PipelineRunner):
        gen = ReportGenerator()
        gen.load_inputs(
            profile_json=runner.profile_json,
            workflow_plan_json=runner.plan_json,
            prepared_panel=runner.prepared_panel,
            execution_log=runner.execution_log,
            initial_validation_report=runner.initial_validation_json,
            repair_plan=runner.repair_plan,
            repair_log=runner.repair_log,
            repaired_panel=runner.repaired_panel,
            final_validation_report=runner.final_validation_json,
            approved_features=runner.final_approved,
            data_dictionary=runner.data_dictionary,
            fetch_metadata=self.input_dir / "fetch_metadata.json",
            input_dir=self.input_dir,
        )
        return gen

    # 23. 中文最终报告包含主要中文标题
    def test_full_report_has_chinese_headings(self):
        runner = self._run_pipeline()
        gen = self._load_report(runner)
        full = gen.render_full_report()
        for heading in [
            "# 金融表格 analysis-ready 工作流 — 最终报告",
            "## 1. 执行摘要",
            "## 2. 数据来源与时间边界",
            "## 3. 工作流架构（Mermaid）",
            "## 4. 为什么这不只是表格检查",
            "## 5. 各阶段说明",
            "## 6. 闭环深入",
            "## 7. 特征列表与标签隔离",
            "## 8. 标签泄漏说明",
            "## 9. 警告与未解决问题",
            "## 10. 局限性",
            "## 11. 最终结论",
        ]:
            self.assertIn(heading, full, f"missing heading: {heading}")

    # 24. 中文一页摘要包含主要中文标题
    def test_one_page_has_chinese_headings(self):
        runner = self._run_pipeline()
        gen = self._load_report(runner)
        one = gen.render_one_page()
        for heading in [
            "# 金融表格 analysis-ready 工作流 — 一页摘要",
            "## 目标",
            "## 数据来源",
            "## 六个模块",
            "## 闭环结果",
            "## 为什么重要",
            "## 局限性",
        ]:
            self.assertIn(heading, one, f"missing heading: {heading}")

    # 25. 报告所有数值仍来自真实产物（不硬编码 7 行 22 列等）
    def test_report_numbers_from_real_artifacts(self):
        runner = self._run_pipeline()
        gen = self._load_report(runner)
        s = gen.build_summary()
        # 行数来自 prepared_panel 实算
        import pandas as pd
        prepared = pd.read_csv(runner.prepared_panel)
        self.assertEqual(s["panel_summary"]["prepared_panel_rows"], int(len(prepared)))
        self.assertEqual(
            s["panel_summary"]["prepared_panel_cols"], int(prepared.shape[1])
        )
        # closed_loop 行数来自 repair_log
        self.assertEqual(
            s["closed_loop_result"]["initial_rows"],
            int(json.loads(runner.repair_log.read_text(encoding="utf-8"))["rows_before"]),
        )
        # approved features 来自 approved_feature_columns.json
        approved = json.loads(runner.final_approved.read_text(encoding="utf-8"))
        self.assertEqual(s["approved_feature_columns"], approved["approved_feature_columns"])

    # 26. final_workflow_summary.json 结构保持兼容
    def test_summary_json_structure_compatible(self):
        runner = self._run_pipeline()
        gen = self._load_report(runner)
        s = gen.build_summary()
        # 顶层关键字段保持兼容
        for key in [
            "project", "report_version", "generated_from_stages",
            "initial_validation_status", "final_validation_status",
            "rows_removed_by_repair", "closed_loop_result", "pipeline_stages",
            "profile_summary", "plan_summary", "execution_summary", "panel_summary",
            "approved_feature_columns", "excluded_columns", "label_column",
            "label_not_in_approved_features", "limitations",
        ]:
            self.assertIn(key, s, f"missing key: {key}")
        # closed_loop_result 子字段
        cl = s["closed_loop_result"]
        for key in [
            "initial_rows", "initial_status", "failed_check", "failed_reason",
            "rows_removed", "repaired_rows", "final_status",
            "label_not_in_approved_features", "repair_skipped", "no_op_kind",
            "one_line",
        ]:
            self.assertIn(key, cl, f"missing closed_loop key: {key}")
        # Stage 12 新增 data_source_summary
        self.assertIn("data_source_summary", s)

    # 27. label_next_5d 永远不进入 approved_feature_columns
    def test_label_not_in_approved_features(self):
        runner = self._run_pipeline()
        gen = self._load_report(runner)
        s = gen.build_summary()
        self.assertNotIn("label_next_5d", s["approved_feature_columns"])
        self.assertTrue(s["label_not_in_approved_features"])
        # 报告正文也声明标签不在特征中
        full = gen.render_full_report()
        self.assertIn("label_not_in_approved_features", full)

    # passed_with_warnings 显示为"passed_with_warnings（通过但有警告）"，不覆盖原始值
    def test_passed_with_warnings_display(self):
        runner = self._run_pipeline()
        gen = self._load_report(runner)
        s = gen.build_summary()
        full = gen.render_full_report()
        # fixture 初始状态为 passed_with_warnings
        self.assertEqual(s["initial_validation_status"], "passed_with_warnings")
        # 报告正文显示中文括注，但 JSON 值保留原文
        self.assertIn("passed_with_warnings（通过但有警告）", full)
        # summary.json 中原始值未被覆盖
        self.assertEqual(s["initial_validation_status"], "passed_with_warnings")

    # 数据来源章节：有 fetch_metadata 时显示抓取来源
    def test_data_source_section_with_fetch_metadata(self):
        runner = self._run_pipeline()
        gen = self._load_report(runner)
        full = gen.render_full_report()
        self.assertIn("数据来源与时间边界", full)
        self.assertIn("fetch_real_market_data", full)
        self.assertIn("600519", full)
        self.assertIn("2024-01-01", full)
        # 基本面时间边界说明存在
        self.assertIn("基本面时间边界", full)
        self.assertIn("point-in-time", full.lower() + " " + full)

    # 数据来源章节：无 fetch_metadata 时显示"用户提供的已有 CSV"
    def test_data_source_section_without_fetch_metadata(self):
        runner = self._run_pipeline()
        gen = ReportGenerator()
        gen.load_inputs(
            profile_json=runner.profile_json,
            workflow_plan_json=runner.plan_json,
            prepared_panel=runner.prepared_panel,
            execution_log=runner.execution_log,
            initial_validation_report=runner.initial_validation_json,
            repair_plan=runner.repair_plan,
            repair_log=runner.repair_log,
            repaired_panel=runner.repaired_panel,
            final_validation_report=runner.final_validation_json,
            approved_features=runner.final_approved,
            data_dictionary=runner.data_dictionary,
            fetch_metadata=None,  # 无 fetch_metadata
            input_dir=self.input_dir,
        )
        full = gen.render_full_report()
        self.assertIn("用户提供的已有 CSV", full)
        self.assertIn("不编造", full)
        s = gen.build_summary()
        self.assertEqual(s["data_source_summary"]["source_kind"], "user_provided_existing_csv")

    # 一页摘要数据来源：有 fetch_metadata
    def test_one_page_data_source_with_fetch(self):
        runner = self._run_pipeline()
        gen = self._load_report(runner)
        one = gen.render_one_page()
        self.assertIn("fetch_real_market_data", one)
        self.assertIn("600519", one)
        self.assertIn("point-in-time", one.lower() + " " + one)


if __name__ == "__main__":
    unittest.main(verbosity=2)
