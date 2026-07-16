"""Pipeline 工具测试（Stage 9 MVP）。

覆盖：
C1. 输入目录不存在时明确失败。
C2. 缺少 CSV 时明确失败。
C3. 不创建 synthetic/sample 数据。
C4. configure 后 runner.output_root 等于当前 run_root。
C5. profile 工具可使用真实 fixture 临时副本。
C6. stage 失败转换为 ToolResult.ok=False。
C7. status 工具只读取当前 run。
C8. validation 失败项能结构化返回。
C9. remediation 的安全状态能够传递。
C10. label 不进入 approved features。

使用 tempfile + 真实 fixture 临时副本；不修改 test_data/real_market_sample；
不访问网络；不依赖真实 LLM。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
for p in (str(SRC), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent_runtime.context import AgentContext, InputDirError  # noqa: E402
from agent_runtime.models import ToolCall  # noqa: E402
from agent_tools.pipeline_tools import build_default_registry  # noqa: E402

FIXTURE_DIR = HERE.parent / "test_data" / "real_market_sample"


def _copy_fixture(tmp_dir: Path) -> Path:
    dst = tmp_dir / "input"
    shutil.copytree(FIXTURE_DIR, dst)
    return dst


def _make_ctx(tmp: Path, run_id: str = "run_pt_001") -> AgentContext:
    input_dir = _copy_fixture(tmp)
    return AgentContext.create(
        workspace_root=HERE.parent,
        input_dir=input_dir,
        output_base=tmp / "outputs",
        run_id=run_id,
    )


def _exec(registry, name: str, ctx: AgentContext, args: dict | None = None):
    """便捷执行工具调用。"""
    return registry.execute(
        ToolCall(call_id=f"c_{name}", name=name, arguments=args or {}), ctx
    )


class TestPipelineTools(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pt_"))
        self.ctx = _make_ctx(self.tmp, run_id="run_pt")
        self.registry = build_default_registry()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # C4. configure 后 runner.output_root 等于当前 run_root
    def test_configure_sets_runner_output_root(self):
        result = _exec(self.registry, "configure_workflow", self.ctx, {})
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "configured")
        self.assertEqual(
            result.metrics["runner_output_root"],
            str(self.ctx.run_root).replace("\\", "/"),
        )
        self.assertEqual(self.ctx.runner.output_root, self.ctx.run_root)

    # C5. profile 工具可使用真实 fixture 临时副本
    def test_profile_tool_runs(self):
        _exec(self.registry, "configure_workflow", self.ctx, {})
        result = _exec(self.registry, "profile_financial_data", self.ctx, {})
        self.assertTrue(result.ok, f"profile failed: {result.to_dict()}")
        self.assertEqual(result.status, "completed")
        self.assertGreater(result.metrics["n_tables"], 0)
        # artifact 在 run_root 下
        for art in result.artifacts:
            self.assertIn(self.ctx.run_id, art)

    # 完整 pipeline 通过工具链跑通（一轮收敛路径）
    def test_full_pipeline_via_tools(self):
        # 注入 2 行 close 缺失，使 initial critic failed → 一轮收敛
        price_path = self.ctx.input_dir / "price.csv"
        df = pd.read_csv(price_path)
        n = len(df)
        idx = df.sample(n=min(2, n), random_state=0).index
        df.loc[idx, "close"] = None
        df.to_csv(price_path, index=False, encoding="utf-8-sig")

        _exec(self.registry, "configure_workflow", self.ctx,
              {"max_row_loss_ratio": 0.5})
        _exec(self.registry, "profile_financial_data", self.ctx, {})
        _exec(self.registry, "create_workflow_plan", self.ctx, {})
        _exec(self.registry, "prepare_financial_panel", self.ctx, {})
        vc = _exec(self.registry, "validate_financial_panel", self.ctx, {})
        self.assertEqual(vc.metrics["overall_status"], "failed")
        rem = _exec(self.registry, "run_safe_remediation", self.ctx, {})
        # 一轮收敛 → ok=True, not requires_user_action
        self.assertTrue(rem.ok, f"remediation failed: {rem.to_dict()}")
        self.assertEqual(rem.metrics["termination_reason"], "validation_passed")
        self.assertFalse(rem.requires_user_action)
        rc = _exec(self.registry, "validate_repaired_panel", self.ctx, {})
        self.assertIn(rc.metrics["overall_status"], ("passed", "passed_with_warnings"))
        rep = _exec(self.registry, "generate_workflow_report", self.ctx, {})
        self.assertTrue(rep.ok)

    # C6. stage 失败转换为 ToolResult.ok=False
    def test_stage_failure_converts_to_ok_false(self):
        # 不 configure 直接 profile → runner 未创建 → 现在返回 PRECONDITION_NOT_MET
        # （Stage 12：无 input_dir / 未 configure 时不再抛 RuntimeError，而是结构化失败）
        result = _exec(self.registry, "profile_financial_data", self.ctx, {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "PRECONDITION_NOT_MET")

    # C7. status 工具只读取当前 run
    def test_status_reads_current_run_only(self):
        _exec(self.registry, "configure_workflow", self.ctx, {})
        result = _exec(self.registry, "inspect_pipeline_status", self.ctx, {})
        self.assertTrue(result.ok)
        # output_root 必须是当前 run_root
        self.assertEqual(
            result.metrics["output_root"],
            str(self.ctx.run_root).replace("\\", "/"),
        )
        # 未跑任何阶段 → 全 pending
        for s, st in result.metrics["stage_statuses"].items():
            self.assertEqual(st, "pending")

    # C8. validation 失败项能结构化返回
    def test_inspect_validation_failures_structured(self):
        _exec(self.registry, "configure_workflow", self.ctx, {})
        _exec(self.registry, "profile_financial_data", self.ctx, {})
        _exec(self.registry, "create_workflow_plan", self.ctx, {})
        _exec(self.registry, "prepare_financial_panel", self.ctx, {})
        _exec(self.registry, "validate_financial_panel", self.ctx, {})
        result = _exec(self.registry, "inspect_validation_failures", self.ctx, {})
        self.assertTrue(result.ok)
        self.assertIn("overall_status", result.metrics)
        self.assertIn("failed_checks", result.metrics)
        self.assertIn("warnings", result.metrics)
        self.assertIn("recommendations", result.metrics)
        # artifact 在 run_root 下
        self.assertTrue(result.artifacts)
        self.assertIn(self.ctx.run_id, result.artifacts[0])

    # C9. remediation 的安全状态能够传递（manual_review_required → requires_user_action）
    def test_remediation_safety_state_propagates(self):
        # 注入 1 行 close 缺失（1/7≈14% > 5% 安全门）→ manual_review_required
        price_path = self.ctx.input_dir / "price.csv"
        df = pd.read_csv(price_path)
        idx = df.sample(n=1, random_state=0).index
        df.loc[idx, "close"] = None
        df.to_csv(price_path, index=False, encoding="utf-8-sig")

        _exec(self.registry, "configure_workflow", self.ctx, {})  # 默认 5% 安全门
        _exec(self.registry, "profile_financial_data", self.ctx, {})
        _exec(self.registry, "create_workflow_plan", self.ctx, {})
        _exec(self.registry, "prepare_financial_panel", self.ctx, {})
        _exec(self.registry, "validate_financial_panel", self.ctx, {})
        rem = _exec(self.registry, "run_safe_remediation", self.ctx, {})
        self.assertFalse(rem.ok)
        self.assertEqual(rem.status, "manual_review_required")
        self.assertTrue(rem.requires_user_action)
        self.assertEqual(rem.metrics["termination_reason"], "manual_review_required")
        self.assertTrue(rem.metrics["manual_review_required"])

    # C10. label 不进入 approved features
    def test_label_not_in_approved_features(self):
        _exec(self.registry, "configure_workflow", self.ctx, {})
        _exec(self.registry, "profile_financial_data", self.ctx, {})
        _exec(self.registry, "create_workflow_plan", self.ctx, {})
        _exec(self.registry, "prepare_financial_panel", self.ctx, {})
        _exec(self.registry, "validate_financial_panel", self.ctx, {})
        # 读 approved_feature_columns.json
        approved_path = self.ctx.runner.initial_approved
        with approved_path.open("r", encoding="utf-8") as f:
            approved = json.load(f)
        self.assertNotIn("label_next_5d", approved["approved_feature_columns"])
        # status 工具也应报告 label_in_approved_features=False
        status = _exec(self.registry, "inspect_pipeline_status", self.ctx, {})
        self.assertFalse(status.metrics["label_in_approved_features"])

    # not_needed 路径：initial passed → run_safe_remediation 返回 not_needed
    def test_remediation_not_needed_when_passed(self):
        # 清理 OHLC 缺失 → initial passed
        price_path = self.ctx.input_dir / "price.csv"
        df = pd.read_csv(price_path)
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        df.to_csv(price_path, index=False, encoding="utf-8-sig")

        _exec(self.registry, "configure_workflow", self.ctx, {})
        _exec(self.registry, "profile_financial_data", self.ctx, {})
        _exec(self.registry, "create_workflow_plan", self.ctx, {})
        _exec(self.registry, "prepare_financial_panel", self.ctx, {})
        vc = _exec(self.registry, "validate_financial_panel", self.ctx, {})
        self.assertNotEqual(vc.metrics["overall_status"], "failed")
        rem = _exec(self.registry, "run_safe_remediation", self.ctx, {})
        self.assertTrue(rem.ok)
        self.assertEqual(rem.status, "not_needed")
        self.assertEqual(rem.metrics["termination_reason"], "validation_passed")
        self.assertFalse(rem.requires_user_action)


class TestPipelineToolsInputValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ptin_"))
        self.registry = build_default_registry()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # C1. 输入目录不存在时明确失败
    def test_missing_input_dir_fails(self):
        with self.assertRaises(InputDirError):
            AgentContext.create(
                workspace_root=HERE.parent,
                input_dir=self.tmp / "nope",
                output_base=self.tmp / "outputs",
                run_id="run_x",
            )

    # C2. 缺少 CSV 时明确失败
    def test_missing_csv_fails(self):
        d = self.tmp / "incomplete"
        d.mkdir()
        (d / "price.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        with self.assertRaises(InputDirError):
            AgentContext.create(
                workspace_root=HERE.parent,
                input_dir=d,
                output_base=self.tmp / "outputs",
                run_id="run_x",
            )

    # C3. 不创建 synthetic/sample 数据
    def test_no_synthetic_data_created(self):
        ctx = _make_ctx(self.tmp, run_id="run_nosynth")
        _exec(self.registry, "configure_workflow", ctx, {})
        _exec(self.registry, "profile_financial_data", ctx, {})
        # 不应出现 data/sample 或合成 CSV
        self.assertFalse((self.tmp / "sample").exists())
        self.assertFalse((self.tmp / "data" / "sample").exists())
        # input_dir 仍是真实 fixture 副本（未被覆盖/伪造）
        self.assertTrue((ctx.input_dir / "price.csv").exists())
        # run_root 下只有派生产物，没有合成输入
        for p in ctx.run_root.rglob("*.csv"):
            # 派生 panel 是 prepared/repaired，不是合成输入
            self.assertIn(self.ctx_run_subpath(p, ctx), ("prepared", "repaired"))

    @staticmethod
    def ctx_run_subpath(p: Path, ctx: AgentContext) -> str:
        rel = p.relative_to(ctx.run_root)
        return rel.parts[0] if rel.parts else ""


if __name__ == "__main__":
    unittest.main(verbosity=2)
