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
        self.assertFalse(args.auto_approve_data_fetch)
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
                "--auto_approve_data_fetch",
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
        self.assertTrue(args.auto_approve_data_fetch)
        self.assertEqual(args.max_repair_rounds, 2)
        self.assertAlmostEqual(args.max_row_loss_ratio, 0.1)

    def test_input_dir_optional_mode_b(self):
        # Stage 12: --input_dir 现在可选（模式 B 不传）
        args = parse_args(["--output_base", "out", "--prompt", "fetch 600519"])
        self.assertIsNone(args.input_dir)
        self.assertEqual(args.output_base, "out")

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

    def test_fetch_guarded_default_ask(self):
        # Stage 12: fetch_real_market_data 是 guarded，默认 ASK
        engine = _resolve_policy()
        from agent_runtime.models import RiskLevel

        d = engine.decide("fetch_real_market_data", RiskLevel.GUARDED, run_id="r")
        self.assertEqual(d.action, PolicyAction.ASK)


class TestShouldAutoApprove(unittest.TestCase):
    """Stage 12: --auto_approve_data_fetch 只批准 fetch；--auto_approve_remediation 只批准 remediation。"""

    def test_data_fetch_flag_only_approves_fetch(self):
        from chat_agent import _should_auto_approve

        self.assertTrue(
            _should_auto_approve(
                "fetch_real_market_data",
                auto_approve_data_fetch=True,
                auto_approve_remediation=False,
            )
        )
        # --auto_approve_remediation 不应自动批准 fetch
        self.assertFalse(
            _should_auto_approve(
                "fetch_real_market_data",
                auto_approve_data_fetch=False,
                auto_approve_remediation=True,
            )
        )

    def test_remediation_flag_only_approves_remediation(self):
        from chat_agent import _should_auto_approve

        self.assertTrue(
            _should_auto_approve(
                "run_safe_remediation",
                auto_approve_data_fetch=False,
                auto_approve_remediation=True,
            )
        )
        # --auto_approve_data_fetch 不应自动批准 remediation
        self.assertFalse(
            _should_auto_approve(
                "run_safe_remediation",
                auto_approve_data_fetch=True,
                auto_approve_remediation=False,
            )
        )

    def test_other_guarded_not_auto_approved(self):
        from chat_agent import _should_auto_approve

        # 未知 guarded 工具不被任一 flag 自动批准
        self.assertFalse(
            _should_auto_approve(
                "some_other_guarded",
                auto_approve_data_fetch=True,
                auto_approve_remediation=True,
            )
        )


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
            # Stage 12：fetch_real_market_data 现在是第一个注册的工具
            self.assertEqual(model.received_tools[0][0]["name"], "fetch_real_market_data")
            # configure_workflow 仍在 schema 列表中
            names = [t["name"] for t in model.received_tools[0]]
            self.assertIn("configure_workflow", names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# 16. Stage 12：自然语言抓取完整链路（Fake Model + mock fetch，不访问网络）
# ======================================================================


def _fetch_chain_turns():
    """模式 B 完整链路：fetch → configure → profile → plan → prepare → validate
    → inspect failures → remediation → revalidate → report → final。"""
    return [
        AssistantTurn(
            tool_calls=[
                ToolCall(
                    call_id="f1",
                    name="fetch_real_market_data",
                    arguments={
                        "tickers": ["600519"],
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-10",
                        "snapshot_fundamentals": False,
                    },
                )
            ]
        ),
        AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="configure_workflow", arguments={"max_row_loss_ratio": 0.5})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c2", name="profile_financial_data", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c3", name="create_workflow_plan", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c4", name="prepare_financial_panel", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c5", name="validate_financial_panel", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c6", name="inspect_validation_failures", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c7", name="run_safe_remediation", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c8", name="validate_repaired_panel", arguments={})]),
        AssistantTurn(tool_calls=[ToolCall(call_id="c9", name="generate_workflow_report", arguments={})]),
        AssistantTurn(final_text="已抓取真实数据并完成全流程，最终中文报告已生成。"),
    ]


class _FetchFakeAdapter:
    """mock real_data_adapter.fetch_real_data：把 fixture 的五张 CSV 复制到
    config.output_dir，并返回与真实 adapter 同构的 metadata dict。不访问网络。"""

    def __init__(self, fixture_dir: Path):
        self.fixture_dir = fixture_dir
        self.calls: list = []

    def __call__(self, config):
        import json as _json
        import shutil as _shutil

        self.calls.append(config)
        out_dir = Path(config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # 复制五张 CSV + fetch_metadata.json
        for name in ("price.csv", "volume.csv", "fundamentals.csv",
                     "industry.csv", "calendar.csv", "fetch_metadata.json"):
            src = self.fixture_dir / name
            if src.exists():
                _shutil.copyfile(src, out_dir / name)
        # 读回 fixture 的 metadata，更新 output_files 路径与 requested_tickers
        meta_path = self.fixture_dir / "fetch_metadata.json"
        with meta_path.open("r", encoding="utf-8") as f:
            metadata = _json.load(f)
        metadata["requested_tickers"] = list(config.tickers)
        metadata["resolved_tickers"] = list(config.tickers)
        metadata["start_date"] = config.start_date
        metadata["end_date"] = config.end_date
        metadata["snapshot_fundamentals_enabled"] = bool(config.snapshot_fundamentals)
        metadata["output_files"] = {
            k: str(out_dir / v).replace("\\", "/")
            for k, v in {
                "price": "price.csv",
                "volume": "volume.csv",
                "fundamentals": "fundamentals.csv",
                "industry": "industry.csv",
                "calendar": "calendar.csv",
            }.items()
        }
        with (out_dir / "fetch_metadata.json").open("w", encoding="utf-8") as f:
            _json.dump(metadata, f, ensure_ascii=False, indent=2)
        return metadata


class TestNaturalLanguageFetchChain(unittest.TestCase):
    """Stage 12：模式 B 自然语言抓取完整链路（mock fetch，不访问网络）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nlf_"))
        # mock real_data_adapter.fetch_real_data
        import real_data_adapter
        self._orig_fetch = real_data_adapter.fetch_real_data
        self._fake = _FetchFakeAdapter(FIXTURE_DIR)
        real_data_adapter.fetch_real_data = self._fake

    def tearDown(self):
        import real_data_adapter
        real_data_adapter.fetch_real_data = self._orig_fetch
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_full_chain_completes(self):
        out = _Output()
        args = parse_args(
            [
                "--output_base",
                str(self.tmp / "outputs"),
                "--run_id",
                "run_nlf",
                "--prompt",
                "获取600519从2024-01-01至2024-01-10的真实数据并生成报告",
                "--auto_approve_data_fetch",
                "--auto_approve_remediation",
                "--max_tool_turns",
                "20",
            ]
        )
        model = ScriptedFakeModel(_fetch_chain_turns())
        rc = run_chat(args, model_client=model, output_fn=out)
        self.assertEqual(rc, 0, out.text)
        text = out.text
        # fetch 被调用一次（mock）
        self.assertEqual(len(self._fake.calls), 1)
        # fetch 被 auto-approve（--auto_approve_data_fetch）
        self.assertIn("fetch_real_market_data", text)
        self.assertIn("auto-approved", text)
        # 最终中文回答
        self.assertIn("已抓取真实数据", text)
        # run_root 与报告路径
        self.assertIn("Run root:", text)
        self.assertIn("Final report:", text)
        # raw_data 在 run_root 下
        run_root_line = [ln for ln in out.lines if ln.startswith("Run root:")][0]
        run_root = run_root_line.split("Run root:", 1)[1].strip()
        # 报告文件真实存在
        report_line = [ln for ln in out.lines if ln.startswith("Final report:")][0]
        report_path = report_line.split("Final report:", 1)[1].strip()
        self.assertTrue(Path(report_path).exists(), f"report not found: {report_path}")

    def test_fetch_default_triggers_approval(self):
        # 不传 --auto_approve_data_fetch：fetch guarded → ASK → 需用户输入
        out = _Output()
        args = parse_args(
            [
                "--output_base",
                str(self.tmp / "outputs"),
                "--run_id",
                "run_nlf_ask",
                "--prompt",
                "获取600519从2024-01-01至2024-01-10的真实数据并生成报告",
                "--auto_approve_remediation",
                "--max_tool_turns",
                "20",
            ]
        )
        model = ScriptedFakeModel(_fetch_chain_turns())
        # 第一次输入 y 批准 fetch；后续 remediation 由 --auto_approve_remediation 自动批准
        inputs = iter(["y"])
        rc = run_chat(args, model_client=model, input_fn=lambda _p: next(inputs), output_fn=out)
        self.assertEqual(rc, 0, out.text)
        text = out.text
        self.assertIn("Approve?", text)
        self.assertIn("fetch_real_market_data", text)
        # fetch 仍只执行一次
        self.assertEqual(len(self._fake.calls), 1)

    def test_fetch_rejected_does_not_execute(self):
        # 拒绝 fetch：mock 不应被调用；模型应给出最终文本
        out = _Output()
        args = parse_args(
            [
                "--output_base",
                str(self.tmp / "outputs"),
                "--run_id",
                "run_nlf_rej",
                "--prompt",
                "获取600519从2024-01-01至2024-01-10的真实数据并生成报告",
                "--max_tool_turns",
                "20",
            ]
        )
        turns = [
            AssistantTurn(
                tool_calls=[
                    ToolCall(
                        call_id="f1",
                        name="fetch_real_market_data",
                        arguments={
                            "tickers": ["600519"],
                            "start_date": "2024-01-01",
                            "end_date": "2024-01-10",
                        },
                    )
                ]
            ),
            AssistantTurn(final_text="用户拒绝了抓取，已停止。"),
        ]
        model = ScriptedFakeModel(turns)
        inputs = iter(["n"])
        rc = run_chat(args, model_client=model, input_fn=lambda _p: next(inputs), output_fn=out)
        self.assertEqual(rc, 0, out.text)
        # mock 未被调用（拒绝 → 不执行 fetch）
        self.assertEqual(len(self._fake.calls), 0)
        self.assertIn("拒绝了抓取", out.text)

    def test_no_input_dir_profile_returns_precondition(self):
        # 模式 B 启动后未 fetch 直接 profile → PRECONDITION_NOT_MET
        from agent_runtime.context import AgentContext
        from agent_tools.pipeline_tools import build_default_registry
        from agent_runtime.models import ToolCall

        ctx = AgentContext.create_without_input_dir(
            workspace_root=HERE.parent,
            output_base=self.tmp / "outputs",
            run_id="run_precond",
        )
        reg = build_default_registry()
        result = reg.execute(
            ToolCall(call_id="p1", name="profile_financial_data", arguments={}), ctx
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "PRECONDITION_NOT_MET")
        # configure 也应失败并建议 fetch
        cfg = reg.execute(
            ToolCall(call_id="cf1", name="configure_workflow", arguments={}), ctx
        )
        self.assertFalse(cfg.ok)
        self.assertEqual(cfg.error.code, "PRECONDITION_NOT_MET")
        self.assertIn("fetch_real_market_data", cfg.next_actions)


# ======================================================================
# runtime_error 的可诊断性
# ======================================================================


class _RaisingFakeModel:
    """complete() 抛异常，用于触发 Runtime 的兜底 runtime_error。不访问网络。"""

    def __init__(self, exc: Exception):
        self._exc = exc

    def complete(self, messages, tools):
        raise self._exc


class TestRuntimeErrorIsDiagnosable(unittest.TestCase):
    """Runtime 把兜底异常记在 runtime_stop 事件的 payload["error"]。

    事件流不落盘，若 CLI 不打印该字段，runtime_error 就只剩一个无从排查的名字。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="chat_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, exc: Exception) -> tuple[int, str]:
        input_dir = _copy_fixture(self.tmp, "input")
        out = _Output()
        args = parse_args(
            [
                "--input_dir",
                str(input_dir),
                "--output_base",
                str(self.tmp / "outputs"),
                "--run_id",
                "run_err",
                "--prompt",
                "检查数据并生成最终报告",
            ]
        )
        rc = run_chat(args, model_client=_RaisingFakeModel(exc), output_fn=out)
        return rc, out.text

    def test_runtime_error_prints_underlying_exception(self):
        rc, text = self._run(RuntimeError("upstream gateway exploded"))
        self.assertEqual(rc, 2)
        self.assertIn("[stop] runtime_error", text)
        # 关键：真实异常类型与消息必须可见
        self.assertIn("[stop] error:", text)
        self.assertIn("RuntimeError", text)
        self.assertIn("upstream gateway exploded", text)

    def test_normal_stop_has_no_error_line(self):
        """completed 等正常停止不带 error，不应打印空的 error 行。"""
        input_dir = _copy_fixture(self.tmp, "input")
        out = _Output()
        args = parse_args(
            [
                "--input_dir",
                str(input_dir),
                "--output_base",
                str(self.tmp / "outputs"),
                "--run_id",
                "run_ok",
                "--prompt",
                "只回答，不调用工具",
            ]
        )
        model = ScriptedFakeModel([AssistantTurn(final_text="好的，已完成。")])
        rc = run_chat(args, model_client=model, output_fn=out)
        self.assertEqual(rc, 0)
        self.assertIn("[stop] completed", out.text)
        self.assertNotIn("[stop] error:", out.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
