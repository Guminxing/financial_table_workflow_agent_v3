"""AgentRuntime + AgentContext/run_id 测试（Stage 9 MVP）。

覆盖：
B. AgentContext/run_id
  B1. 合法 run_id。
  B2. 拒绝 ../x。
  B3. 拒绝包含 / 或反斜杠。
  B4. run_root 位于 output_base/runs/run_id。
  B5. 两个 run_id 产物隔离。
  B6. 不从另一个 run 恢复 repair_history。

D. AgentRuntime
  D1. FakeModel 发出工具调用，Runtime 正确执行。
  D2. ToolResult 被加入下一轮 model messages。
  D3. 模型返回 final_text 后 completed。
  D4. 达到 max_tool_turns 后停止。
  D5. 连续重复相同工具调用后停止。
  D6. 未知工具不会导致 Runtime 崩溃。
  D7. 工具失败会反馈给模型。
  D8. requires_user_action 时停止。
  D9. 每一步生成正确 AgentEvent。
  D10. Runtime 不直接依赖 PipelineRunner。

使用 ScriptedFakeModel 作为测试替身：按顺序返回预设 AssistantTurn，
记录收到的 messages 和 tools，预设响应耗尽时明确失败。
不访问网络，不依赖任何 LLM SDK。
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

from agent_runtime.context import (  # noqa: E402
    AgentContext,
    InputDirError,
    RunIdError,
    normalize_run_id,
    validate_input_dir,
)
from agent_runtime.models import (  # noqa: E402
    AgentEvent,
    AssistantTurn,
    EventType,
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
    RiskLevel,
)
from agent_runtime.registry import ToolRegistry, build_registry  # noqa: E402
from agent_runtime.runtime import AgentRuntime  # noqa: E402

FIXTURE_DIR = HERE.parent / "test_data" / "real_market_sample"


def _copy_fixture(tmp_dir: Path, subdir: str = "input") -> Path:
    dst = tmp_dir / subdir
    shutil.copytree(FIXTURE_DIR, dst)
    return dst


# ======================================================================
# ScriptedFakeModel：测试替身
# ======================================================================


class ScriptedFakeModel:
    """按顺序返回预设 AssistantTurn 的假模型。

    - 按顺序返回预设 AssistantTurn。
    - 记录收到的 messages 和 tools。
    - 当预设响应耗尽时明确失败（抛 RuntimeError）。
    - 不访问网络，不依赖任何 LLM SDK。
    """

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


def _make_ctx(tmp: Path, run_id: str = "run_test_001") -> AgentContext:
    """构造一个指向临时 fixture 副本的 AgentContext。

    每个 run_id 用独立的 input 子目录，避免同一测试中多次调用时 copytree 冲突。
    """
    safe_subdir = f"input_{run_id}"
    input_dir = _copy_fixture(tmp, safe_subdir)
    return AgentContext.create(
        workspace_root=HERE.parent,
        input_dir=input_dir,
        output_base=tmp / "outputs",
        run_id=run_id,
    )


# ======================================================================
# B. AgentContext / run_id
# ======================================================================


class TestRunId(unittest.TestCase):
    # B1. 合法 run_id
    def test_valid_run_id(self):
        self.assertEqual(normalize_run_id("run_20260715_120000_ab12"), "run_20260715_120000_ab12")
        self.assertEqual(normalize_run_id("  run_x  "), "run_x")
        self.assertEqual(normalize_run_id("run-1"), "run-1")

    # B2. 拒绝 `../x`
    def test_reject_path_traversal(self):
        with self.assertRaises(RunIdError):
            normalize_run_id("../x")
        with self.assertRaises(RunIdError):
            normalize_run_id("run_.._x")

    # B3. 拒绝包含 `/` 或 `\`
    def test_reject_separators(self):
        for bad in ["run/a", "run\\a", "run a", "_lead", "-lead", "", "run.x"]:
            with self.assertRaises(RunIdError, msg=f"should reject {bad!r}"):
                normalize_run_id(bad)


class TestAgentContext(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ctx_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # B4. run_root 位于 output_base/runs/run_id
    def test_run_root_location(self):
        ctx = _make_ctx(self.tmp, run_id="run_abc")
        expected = (self.tmp / "outputs" / "runs" / "run_abc").resolve()
        self.assertEqual(ctx.run_root, expected)
        # run_root 在 output_base 之下
        self.assertEqual(ctx.run_root.relative_to(ctx.output_base), Path("runs/run_abc"))

    # 输入目录不存在时明确失败
    def test_input_dir_missing_fails(self):
        with self.assertRaises(InputDirError):
            AgentContext.create(
                workspace_root=HERE.parent,
                input_dir=self.tmp / "does_not_exist",
                output_base=self.tmp / "outputs",
                run_id="run_x",
            )

    # 缺少 CSV 时明确失败
    def test_input_dir_missing_csv_fails(self):
        empty = self.tmp / "empty_input"
        empty.mkdir()
        (empty / "only_one.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        with self.assertRaises(InputDirError):
            AgentContext.create(
                workspace_root=HERE.parent,
                input_dir=empty,
                output_base=self.tmp / "outputs",
                run_id="run_x",
            )

    # B5. 两个 run_id 产物隔离
    def test_two_runs_isolated(self):
        ctx_a = _make_ctx(self.tmp, run_id="run_aaa")
        ctx_b = _make_ctx(self.tmp, run_id="run_bbb")
        self.assertNotEqual(ctx_a.run_root, ctx_b.run_root)
        # 各自 configure 后 runner.output_root 互不相同
        ra = ctx_a.configure_runner()
        rb = ctx_b.configure_runner()
        self.assertEqual(ra.output_root, ctx_a.run_root)
        self.assertEqual(rb.output_root, ctx_b.run_root)
        self.assertNotEqual(ra.output_root, rb.output_root)

    # B6. 不从另一个 run 恢复 repair_history
    def test_no_cross_run_repair_history_restore(self):
        # run_a 跑完整 pipeline（产生 repair_history.json）
        ctx_a = _make_ctx(self.tmp, run_id="run_aaa")
        ra = ctx_a.configure_runner(max_row_loss_ratio=0.5)
        ra.run_full_pipeline()
        self.assertTrue((ctx_a.run_root / "repaired" / "repair_history.json").exists())

        # run_b 指向不同 run_root，新建 runner 不跑 pipeline，get_status 不应读到 run_a 的历史
        ctx_b = _make_ctx(self.tmp, run_id="run_bbb")
        rb = ctx_b.configure_runner()
        status = rb.get_status()
        # run_b 没跑过 repair，repair_rounds 应为 0（不从 run_a 恢复）
        self.assertEqual(status["repair_rounds"], 0)
        self.assertIsNone(status["termination_reason"])
        # run_b 的 repair_history.json 不存在
        self.assertFalse((ctx_b.run_root / "repaired" / "repair_history.json").exists())

    # configure 后 runner.output_root 等于当前 run_root
    def test_configure_sets_runner_output_root(self):
        ctx = _make_ctx(self.tmp, run_id="run_cfg")
        runner = ctx.configure_runner()
        self.assertEqual(runner.output_root, ctx.run_root)

    # artifact 越权检测
    def test_artifact_outside_run_root_rejected(self):
        ctx = _make_ctx(self.tmp, run_id="run_art")
        with self.assertRaises(ValueError):
            ctx.ensure_artifact_in_run_root(self.tmp / "outside.csv")


# ======================================================================
# D. AgentRuntime
# ======================================================================


def _echo_tool(name: str = "echo") -> ToolSpec:
    """一个简单的 echo 工具，把 arguments 回显为 metrics。"""
    return ToolSpec(
        name=name,
        description="echo arguments back",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": [],
        },
        risk_level=RiskLevel.READ,
        handler=lambda args, ctx: ToolResult.success(
            f"echo: {args.get('msg', '')}",
            metrics={"echoed": args.get("msg", "")},
        ),
    )


def _failing_tool(name: str = "boom") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="always fails",
        input_schema={"type": "object", "properties": {}, "required": []},
        risk_level=RiskLevel.READ,
        handler=lambda args, ctx: (_ for _ in ()).throw(RuntimeError("handler boom")),
    )


def _user_action_tool(name: str = "ask_user") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="requires user action",
        input_schema={"type": "object", "properties": {}, "required": []},
        # READ 风险：默认策略 ALLOW → 执行 → handler 返回 requires_user_action=True
        # → Runtime 以 requires_user_action 停止（D8 意图）。
        # 若用 GUARDED，默认策略会先 ASK 暂停（awaiting_approval），那是 Stage 10
        # 审批测试覆盖的场景，不是 D8 的意图。
        risk_level=RiskLevel.READ,
        handler=lambda args, ctx: ToolResult(
            ok=False,
            status="manual_review_required",
            summary="needs human review",
            requires_user_action=True,
        ),
    )


class TestAgentRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rt_"))
        self.ctx = _make_ctx(self.tmp, run_id="run_rt")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _registry(self, specs: list[ToolSpec]) -> ToolRegistry:
        return build_registry(specs)

    # D1. FakeModel 发出工具调用，Runtime 正确执行
    def test_executes_tool_call(self):
        reg = self._registry([_echo_tool()])
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="echo", arguments={"msg": "hi"})]),
            AssistantTurn(final_text="done"),
        ])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=5)
        result = rt.run("echo hi")
        self.assertEqual(result.stop_reason, StopReason.COMPLETED)
        self.assertEqual(result.final_text, "done")
        self.assertEqual(result.tool_turns, 1)
        # 模型第二轮收到了 tool 结果
        second_messages = model.received_messages[1]
        tool_msgs = [m for m in second_messages if m.get("role") == "tool"]
        self.assertTrue(any("echo" in m.get("content", "") for m in tool_msgs))

    # D2. ToolResult 被加入下一轮 model messages
    def test_tool_result_in_next_messages(self):
        reg = self._registry([_echo_tool()])
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="echo", arguments={"msg": "hello"})]),
            AssistantTurn(final_text="ok"),
        ])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=5)
        rt.run("go")
        # 第二轮 messages 应包含 role=tool 的回填
        second = model.received_messages[1]
        tool_contents = [m["content"] for m in second if m.get("role") == "tool"]
        self.assertTrue(tool_contents)
        # content 是 ToolResult.to_dict() 的 JSON
        parsed = json.loads(tool_contents[0])
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["metrics"]["echoed"], "hello")

    # D3. 模型返回 final_text 后 completed
    def test_completed_on_final_text(self):
        reg = self._registry([_echo_tool()])
        model = ScriptedFakeModel([AssistantTurn(final_text="all done")])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=5)
        result = rt.run("hi")
        self.assertEqual(result.stop_reason, StopReason.COMPLETED)
        self.assertEqual(result.final_text, "all done")
        self.assertEqual(result.tool_turns, 0)

    # D4. 达到 max_tool_turns 后停止
    def test_max_tool_turns_stop(self):
        reg = self._registry([_echo_tool()])
        # 模型一直发 tool_call，永不给 final_text。
        # 每轮 arguments 不同，避免触发 repeated_tool_call（指纹只看 name+arguments）。
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id=f"c{i}", name="echo", arguments={"msg": f"m{i}"})])
            for i in range(20)
        ])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=3)
        result = rt.run("loop")
        self.assertEqual(result.stop_reason, StopReason.MAX_TOOL_TURNS)
        self.assertEqual(result.tool_turns, 3)
        self.assertIsNone(result.final_text)

    # D5. 连续重复相同工具调用后停止
    def test_repeated_tool_call_stop(self):
        reg = self._registry([_echo_tool()])
        # 连续两轮完全相同的 tool_call
        same_call = ToolCall(call_id="c1", name="echo", arguments={"msg": "same"})
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[same_call]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c2", name="echo", arguments={"msg": "same"})]),
            AssistantTurn(final_text="never"),
        ])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=10)
        result = rt.run("repeat")
        self.assertEqual(result.stop_reason, StopReason.REPEATED_TOOL_CALL)
        # 只执行了 1 轮（第 2 轮检测到重复即停）
        self.assertEqual(result.tool_turns, 1)

    # D6. 未知工具不会导致 Runtime 崩溃
    def test_unknown_tool_no_crash(self):
        reg = self._registry([_echo_tool()])
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="nope", arguments={})]),
            AssistantTurn(final_text="recovered"),
        ])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=5)
        result = rt.run("call unknown")
        # 模型收到失败结果后给了 final_text → completed
        self.assertEqual(result.stop_reason, StopReason.COMPLETED)
        self.assertEqual(result.final_text, "recovered")
        # 失败结果已回填模型
        second = model.received_messages[1]
        tool_msg = [m for m in second if m.get("role") == "tool"][0]
        parsed = json.loads(tool_msg["content"])
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "UNKNOWN_TOOL")

    # D7. 工具失败会反馈给模型
    def test_tool_failure_feedback(self):
        reg = self._registry([_failing_tool()])
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="boom", arguments={})]),
            AssistantTurn(final_text="saw failure"),
        ])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=5)
        result = rt.run("call boom")
        self.assertEqual(result.stop_reason, StopReason.COMPLETED)
        second = model.received_messages[1]
        parsed = json.loads([m for m in second if m.get("role") == "tool"][0]["content"])
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "TOOL_EXECUTION_ERROR")

    # D8. requires_user_action 时停止
    def test_requires_user_action_stop(self):
        reg = self._registry([_user_action_tool()])
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="ask_user", arguments={})]),
            AssistantTurn(final_text="should not reach"),
        ])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=5)
        result = rt.run("ask")
        self.assertEqual(result.stop_reason, StopReason.REQUIRES_USER_ACTION)
        self.assertIsNone(result.final_text)

    # D9. 每一步生成正确 AgentEvent
    def test_events_recorded(self):
        reg = self._registry([_echo_tool()])
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="echo", arguments={"msg": "x"})]),
            AssistantTurn(final_text="fin"),
        ])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=5)
        result = rt.run("go")
        types = [e.event_type for e in result.events]
        # 期望顺序：user_message, assistant_turn, tool_call, tool_result, assistant_turn, runtime_stop
        self.assertEqual(types[0], EventType.USER_MESSAGE.value)
        self.assertIn(EventType.ASSISTANT_TURN.value, types)
        self.assertIn(EventType.TOOL_CALL.value, types)
        self.assertIn(EventType.TOOL_RESULT.value, types)
        self.assertEqual(types[-1], EventType.RUNTIME_STOP.value)
        # tool_result 事件 payload 含 result.to_dict()
        tr_events = [e for e in result.events if e.event_type == EventType.TOOL_RESULT.value]
        self.assertTrue(tr_events)
        self.assertTrue(tr_events[0].payload["result"]["ok"])

    # D10. Runtime 不直接依赖 PipelineRunner
    def test_runtime_does_not_touch_runner(self):
        # 用一个完全不涉及 PipelineRunner 的 registry + context stub
        class StubCtx:
            run_id = "stub"
            run_root = Path(self.tmp / "stubroot")
        reg = self._registry([_echo_tool()])
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="echo", arguments={})]),
            AssistantTurn(final_text="ok"),
        ])
        rt = AgentRuntime(model, reg, StubCtx(), max_tool_turns=5)  # type: ignore[arg-type]
        result = rt.run("stub")
        self.assertEqual(result.stop_reason, StopReason.COMPLETED)
        # StubCtx 没有 runner 属性，Runtime 仍能工作 → 证明不直接依赖 PipelineRunner
        self.assertFalse(hasattr(StubCtx, "runner"))

    # 模型协议错误（final_text 与 tool_calls 同时为空）
    def test_model_protocol_error_empty_turn(self):
        reg = self._registry([_echo_tool()])
        model = ScriptedFakeModel([AssistantTurn()])  # 全空
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=5)
        result = rt.run("bad")
        self.assertEqual(result.stop_reason, StopReason.MODEL_PROTOCOL_ERROR)

    # 模型协议错误（final_text 与 tool_calls 同时非空）
    def test_model_protocol_error_both_nonempty(self):
        reg = self._registry([_echo_tool()])
        model = ScriptedFakeModel([
            AssistantTurn(
                final_text="text",
                tool_calls=[ToolCall(call_id="c1", name="echo", arguments={})],
            )
        ])
        rt = AgentRuntime(model, reg, self.ctx, max_tool_turns=5)
        result = rt.run("bad")
        self.assertEqual(result.stop_reason, StopReason.MODEL_PROTOCOL_ERROR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
