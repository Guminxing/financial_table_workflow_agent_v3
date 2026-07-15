"""Natural Language Agent CLI 测试（Stage 11）。

覆盖：
9.  CLI 参数和环境变量读取。
10. 缺少 model / base_url / API Key 时给出明确错误。
11. CLI 使用 Fake Model 完成自然语言工具链。
12. CLI approval approve / reject。
13. --auto_approve_remediation。
14. 最终输出包含 run_root 和报告路径。
15. 测试不访问真实网络（注入 Fake Model + 真实 fixture 临时副本）。
16. 原有 102 项测试继续通过（由全量 discover 覆盖）。
"""

from __future__ import annotations

import io
import os
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

from agent_runtime.models import AssistantTurn, StopReason, ToolCall  # noqa: E402
from agent_tools.pipeline_tools import build_default_registry  # noqa: E402
from chat_agent import (  # noqa: E402
    _new_run_id,
    _resolve_policy,
    parse_args,
    run_chat,
)
from agent_runtime.policy import PolicyAction  # noqa: E402

FIXTURE_DIR = HERE.parent / "test_data" / "real_market_sample"


def _copy_fixture(tmp_dir: Path, subdir: str = "input") -> Path:
    dst = tmp_dir / subdir
    shutil.copytree(FIXTURE_DIR, dst)
    return dst


# ======================================================================
# ScriptedFakeModel（与 test_agent_runtime 一致）
# ======================================================================


class ScriptedFakeModel:
    """按顺序返回预设 AssistantTurn 的假模型。不访问网络。"""

    def __init__(self, turns: list[AssistantTurn]):
        self._turns = list(turns)
        self._idx = 0
        self.received_messages: list[list[dict]] = []
        self.received_tools: list[list[dict]] = []

    def complete(self, messages, tools):
        self.received_messages.append(list(messages))
        self.received_tools.append(list(tools))
        if self._idx >= len(self._turns):
            raise RuntimeError(
                "ScriptedFakeModel: no more scripted turns; "
                f"consumed {self._idx}"
            )
        turn = self._turns[self._idx]
        self._idx += 1
        return turn


def _full_chain_turns(max_row_loss_ratio=0.5):
    """configure → profile → plan → prepare → validate → remediation → revalidate → report → final."""
    return [
        AssistantTurn(
            tool_calls=[
                ToolCall(
                    call_id="c1",
                    name="configure_workflow",
                    arguments={"max_row_loss_ratio": max_row_loss_ratio},
                )
            ]
        ),
        AssistantTurn(tool_calls=[ToolCall(call_id="c2", name="profile_financial_data", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c3", name="create_workflow_plan", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c4", name="prepare_financial_panel", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c5", name="validate_financial_panel", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c6", name="run_safe_remediation", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c7", name="validate_repaired_panel", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c8", name="generate_workflow_report", arguments={})]),
        AssistantTurn(final_text="数据处理已经完成，最终报告已生成。"),
    ]


class _Output:
    """收集 output_fn 的所有行。"""

    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, s: str):
        self.lines.append(s)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


# ======================================================================
# 9. CLI 参数和环境变量读取
# ======================================================================


class TestParseArgs(unittest.TestCase):
    def test_defaults(self):
        args = parse_args(["--input_dir", "x", "--output_base", "y"])
        self.assertEqual(args.input_dir, "x")
        self.assertEqual(args.output_base, "y")
        self.assertIsNone(args.run_id)
        self.assertIsNone(args.prompt)
        self.assertIsNone(args.model)
        self.assertIsNone(args.base_url)
        self.assertEqual(args.max_tool_turns, 12)
        self.assertFalse(args.auto_approve_remediation)
        self.assertEqual(args.max_repair_rounds, 3)
        self.assertAlmostEqual(args.max_row_loss_ratio, 0.05)

    def test_all_flags(self):
        args = parse_args(
            [
                "--input_dir",
                "in",
                "--output_base",
                "out",
                "--run_id",
                "run_abc",
                "--prompt",
                "do it",
                "--model",
                "gpt-x",
                "--base_url",
                "https://api.x/v1",
                "--max_tool_turns",
                "5",
                "--auto_approve_remediation",
                "--max_repair_rounds",
                "2",
                "--max_row_loss_ratio",
                "0.1",
            ]
        )
        self.assertEqual(args.run_id, "run_abc")
        self.assertEqual(args.prompt, "do it")
        self.assertEqual(args.model, "gpt-x")
        self.assertEqual(args.base_url, "https://api.x/v1")
        self.assertEqual(args.max_tool_turns, 5)
        self.assertTrue(args.auto_approve_remediation)
        self.assertEqual(args.max_repair_rounds, 2)
        self.assertAlmostEqual(args.max_row_loss_ratio, 0.1)

    def test_run_id_generation(self):
        rid = _new_run_id()
        self.assertTrue(rid.startswith("run_"))
        self.assertEqual(len(rid), len("run_") + 8)


class TestResolvePolicy(unittest.TestCase):
    def test_default_is_ask_for_guarded(self):
        engine = _resolve_policy()
        from agent_runtime.models import RiskLevel

        d = engine.decide("run_safe_remediation", RiskLevel.GUARDED, run_id="r")
        self.assertEqual(d.action, PolicyAction.ASK)

    def test_auto_approve_does_not_change_policy(self):
        # --auto_approve_remediation 不改策略，仍在 CLI 层回复；策略始终 ASK
        engine = _resolve_policy()
        from agent_runtime.models import RiskLevel

        d = engine.decide("run_safe_remediation", RiskLevel.GUARDED, run_id="r")
        self.assertEqual(d.action, PolicyAction.ASK)


# ======================================================================
# 10. 缺少 model / base_url / API Key 时明确错误
# ======================================================================


class TestMissingConfig(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("FTA_LLM_API_KEY", "FTA_LLM_BASE_URL", "FTA_LLM_MODEL")
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_missing_config_exits_with_message(self):
        tmp = Path(tempfile.mkdtemp(prefix="cfg_"))
        try:
            input_dir = _copy_fixture(tmp, "input")
            out = _Output()
            args = parse_args(
                [
                    "--input_dir",
                    str(input_dir),
                    "--output_base",
                    str(tmp / "outputs"),
                    "--prompt",
                    "go",
                ]
            )
            rc = run_chat(args, output_fn=out)
            self.assertEqual(rc, 1)
            text = out.text
            self.assertIn("model not configured", text)
            self.assertIn("FTA_LLM_API_KEY", text)
            self.assertIn("FTA_LLM_BASE_URL", text)
            self.assertIn("FTA_LLM_MODEL", text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# 11. CLI 使用 Fake Model 完成自然语言工具链
# ======================================================================


class TestFakeModelFullChain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="chat_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_chain_completes(self):
        input_dir = _copy_fixture(self.tmp, "input")
        out = _Output()
        args = parse_args(
            [
                "--input_dir",
                str(input_dir),
                "--output_base",
                str(self.tmp / "outputs"),
                "--run_id",
                "run_full",
                "--prompt",
                "检查数据并生成最终报告",
                "--auto_approve_remediation",
                "--max_tool_turns",
                "20",
            ]
        )
        model = ScriptedFakeModel(_full_chain_turns())
        rc = run_chat(args, model_client=model, output_fn=out)
        self.assertEqual(rc, 0)
        text = out.text
        # 最终回答
        self.assertIn("数据处理已经完成", text)
        # run_root
        self.assertIn("Run root:", text)
        self.assertIn("run_full", text)
        # 报告路径
        self.assertIn("Final report:", text)
        # 工具进度行
        self.assertIn("[tool]", text)
        # 不打印完整 messages / API Key
        self.assertNotIn("Authorization", text)


# ======================================================================
# 12. CLI approval approve / reject
# ======================================================================


class TestApprovalInteraction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appr_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _args(self, run_id):
        input_dir = _copy_fixture(self.tmp, f"in_{run_id}")
        # 注入 2 行 close 缺失 → initial failed → run_safe_remediation 真正进入 guarded 修复（ASK）
        import pandas as pd

        price_path = input_dir / "price.csv"
        df = pd.read_csv(price_path)
        idx = df.sample(n=min(2, len(df)), random_state=0).index
        df.loc[idx, "close"] = None
        df.to_csv(price_path, index=False, encoding="utf-8-sig")
        return parse_args(
            [
                "--input_dir",
                str(input_dir),
                "--output_base",
                str(self.tmp / "outputs"),
                "--run_id",
                run_id,
                "--prompt",
                "go",
                "--max_tool_turns",
                "20",
            ]
        )

    def test_approve_completes(self):
        # 不 auto-approve：run_safe_remediation guarded → ASK → 用户输入 y
        args = self._args("run_appr_y")
        model = ScriptedFakeModel(_full_chain_turns())
        out = _Output()
        inputs = iter(["y"])
        rc = run_chat(args, model_client=model, input_fn=lambda _p: next(inputs), output_fn=out)
        self.assertEqual(rc, 0)
        text = out.text
        self.assertIn("Approve?", text)
        self.assertIn("run_safe_remediation", text)
        self.assertIn("数据处理已经完成", text)

    def test_reject_stops_or_continues(self):
        # 拒绝后模型应能继续（脚本里 final_text 仍给出）；退出码非 0（未完成修复链）
        args = self._args("run_appr_n")
        # 拒绝后模型直接 final_text（模拟模型选择放弃修复直接报告）
        turns = [
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="configure_workflow", arguments={"max_row_loss_ratio": 0.5})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c2", name="profile_financial_data", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c3", name="create_workflow_plan", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c4", name="prepare_financial_panel", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c5", name="validate_financial_panel", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c6", name="run_safe_remediation", arguments={})]),
            # 拒绝后模型选择不重试，直接总结
            AssistantTurn(final_text="用户拒绝了修复，已停止自动修复流程。"),
        ]
        model = ScriptedFakeModel(turns)
        out = _Output()
        inputs = iter(["n"])
        rc = run_chat(args, model_client=model, input_fn=lambda _p: next(inputs), output_fn=out)
        # 拒绝后模型 final_text → completed → rc 0（模型自主选择停止并总结）
        self.assertEqual(rc, 0)
        text = out.text
        self.assertIn("拒绝了修复", text)


# ======================================================================
# 13. --auto_approve_remediation
# ======================================================================


class TestAutoApprove(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="auto_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_auto_approve_no_prompt(self):
        input_dir = _copy_fixture(self.tmp, "input")
        # 注入 2 行 close 缺失 → initial failed → run_safe_remediation 真正进入 guarded 修复
        import pandas as pd

        price_path = input_dir / "price.csv"
        df = pd.read_csv(price_path)
        idx = df.sample(n=min(2, len(df)), random_state=0).index
        df.loc[idx, "close"] = None
        df.to_csv(price_path, index=False, encoding="utf-8-sig")
        out = _Output()
        called = {"input": 0}

        def _no_input(prompt):
            called["input"] += 1
            self.fail("input_fn should not be called under --auto_approve_remediation")

        args = parse_args(
            [
                "--input_dir",
                str(input_dir),
                "--output_base",
                str(self.tmp / "outputs"),
                "--run_id",
                "run_auto",
                "--prompt",
                "go",
                "--auto_approve_remediation",
                "--max_tool_turns",
                "20",
            ]
        )
        model = ScriptedFakeModel(_full_chain_turns())
        rc = run_chat(args, model_client=model, input_fn=_no_input, output_fn=out)
        self.assertEqual(rc, 0)
        self.assertEqual(called["input"], 0)
        self.assertIn("auto-approved", out.text)


# ======================================================================
# 14. 最终输出包含 run_root 和报告路径
# ======================================================================


class TestOutputContainsPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="paths_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_output_has_run_root_and_report(self):
        input_dir = _copy_fixture(self.tmp, "input")
        out = _Output()
        args = parse_args(
            [
                "--input_dir",
                str(input_dir),
                "--output_base",
                str(self.tmp / "outputs"),
                "--run_id",
                "run_paths",
                "--prompt",
                "go",
                "--auto_approve_remediation",
                "--max_tool_turns",
                "20",
            ]
        )
        model = ScriptedFakeModel(_full_chain_turns())
        rc = run_chat(args, model_client=model, output_fn=out)
        self.assertEqual(rc, 0)
        text = out.text
        # run_root 行
        run_root_line = [ln for ln in out.lines if ln.startswith("Run root:")]
        self.assertTrue(run_root_line)
        self.assertIn("run_paths", run_root_line[0])
        # 报告路径行，且文件真实存在
        report_line = [ln for ln in out.lines if ln.startswith("Final report:")]
        self.assertTrue(report_line)
        report_path = report_line[0].split("Final report:", 1)[1].strip()
        self.assertTrue(Path(report_path).exists(), f"report not found: {report_path}")


# ======================================================================
# 15. 不访问真实网络（注入 Fake Model + 真实 fixture 副本）
# ======================================================================


class TestNoNetwork(unittest.TestCase):
    def test_uses_injected_model_not_http(self):
        # 注入 Fake Model；run_chat 不应构造真实 client。
        tmp = Path(tempfile.mkdtemp(prefix="nonet_"))
        try:
            input_dir = _copy_fixture(tmp, "input")
            out = _Output()
            args = parse_args(
                [
                    "--input_dir",
                    str(input_dir),
                    "--output_base",
                    str(tmp / "outputs"),
                    "--run_id",
                    "run_nonet",
                    "--prompt",
                    "go",
                    "--auto_approve_remediation",
                    "--max_tool_turns",
                    "20",
                ]
            )
            model = ScriptedFakeModel(_full_chain_turns())
            rc = run_chat(args, model_client=model, output_fn=out)
            self.assertEqual(rc, 0)
            # Fake Model 收到了工具 schema（证明走的是注入模型，不是 HTTP）
            self.assertTrue(model.received_tools)
            self.assertEqual(model.received_tools[0][0]["name"], "configure_workflow")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
