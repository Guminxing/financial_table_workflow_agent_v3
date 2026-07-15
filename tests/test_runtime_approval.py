"""AgentRuntime 审批恢复测试（Stage 10）。

覆盖：
3. ASK/DENY 时 handler 未执行。
4. 正确批准后只执行一次。
5. 拒绝后不执行，并反馈模型。
6. 错误 request_id、参数篡改、跨 run、重复审批被拒绝。
7. resume 后轮数和重复检测不重置。
8. 多 ToolCall 暂停后正确继续。
9. guarded remediation 默认 ASK。
10. 批准后仍受金融安全门约束。
11. awaiting_approval 与 requires_user_action 不混淆。
12. no-op repair 只使用公开 API。
13. 实际修复路径端到端通过。
14. 原有测试和 CLI 保持兼容（由全量 discover 覆盖）。

使用 ScriptedFakeModel + 真实 fixture 临时副本；不访问网络；不依赖真实 LLM。
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

from agent_runtime.context import AgentContext  # noqa: E402
from agent_runtime.models import (  # noqa: E402
    AssistantTurn,
    EventType,
    RiskLevel,
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from agent_runtime.policy import (  # noqa: E402
    ApprovalResponse,
    PolicyAction,
    PolicyConfig,
    PolicyEngine,
    PolicyRule,
)
from agent_runtime.registry import build_registry  # noqa: E402
from agent_runtime.runtime import AgentRuntime  # noqa: E402
from agent_tools.pipeline_tools import build_default_registry  # noqa: E402

FIXTURE_DIR = HERE.parent / "test_data" / "real_market_sample"


def _copy_fixture(tmp_dir: Path, subdir: str = "input") -> Path:
    dst = tmp_dir / subdir
    shutil.copytree(FIXTURE_DIR, dst)
    return dst


def _make_ctx(tmp: Path, run_id: str = "run_ap_001") -> AgentContext:
    safe_subdir = f"input_{run_id}"
    input_dir = _copy_fixture(tmp, safe_subdir)
    return AgentContext.create(
        workspace_root=HERE.parent,
        input_dir=input_dir,
        output_base=tmp / "outputs",
        run_id=run_id,
    )


# ======================================================================
# ScriptedFakeModel（与 test_agent_runtime 一致）
# ======================================================================


class ScriptedFakeModel:
    """按顺序返回预设 AssistantTurn 的假模型。"""

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


# ======================================================================
# 辅助工具：带副作用计数器的工具
# ======================================================================


def _counting_guarded_tool(name: str = "guarded_op") -> tuple[ToolSpec, list[int]]:
    """一个 guarded 工具，handler 执行时计数 +1。返回 (spec, counter_box)。"""
    counter = [0]

    def handler(args, ctx):
        counter[0] += 1
        return ToolResult.success(f"executed {name} #{counter[0]}")

    spec = ToolSpec(
        name=name,
        description="guarded counting tool",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": []},
        risk_level=RiskLevel.GUARDED,
        handler=handler,
    )
    return spec, counter


def _counting_read_tool(name: str = "read_op") -> tuple[ToolSpec, list[int]]:
    counter = [0]

    def handler(args, ctx):
        counter[0] += 1
        return ToolResult.success(f"executed {name} #{counter[0]}")

    spec = ToolSpec(
        name=name,
        description="read counting tool",
        input_schema={"type": "object", "properties": {}, "required": []},
        risk_level=RiskLevel.READ,
        handler=handler,
    )
    return spec, counter


# ======================================================================
# 3. ASK/DENY 时 handler 未执行
# ======================================================================


class TestHandlerNotExecutedOnAskDeny(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ask_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 3a. ASK 时 handler 未执行
    def test_ask_handler_not_executed(self):
        spec, counter = _counting_guarded_tool()
        reg = build_registry([spec])
        ctx = _make_ctx(self.tmp, "run_ask")
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="guarded_op", arguments={"msg": "x"})]),
            AssistantTurn(final_text="done"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=5)
        result = rt.run("go")
        # 默认策略 guarded→ASK → 暂停，handler 未执行
        self.assertEqual(result.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(counter[0], 0)
        self.assertIsNotNone(result.pending_approval)
        self.assertEqual(result.pending_approval.tool_name, "guarded_op")

    # 3b. DENY 时 handler 未执行
    def test_deny_handler_not_executed(self):
        spec, counter = _counting_guarded_tool()
        # 工具级 DENY
        cfg = PolicyConfig.with_overrides({"guarded_op": PolicyAction.DENY})
        reg = build_registry([spec])
        ctx = _make_ctx(self.tmp, "run_deny")
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="guarded_op", arguments={})]),
            AssistantTurn(final_text="recovered"),
        ])
        rt = AgentRuntime(model, reg, ctx, policy=PolicyEngine(cfg), max_tool_turns=5)
        result = rt.run("go")
        # DENY → 不执行，回填拒绝，模型继续 → completed
        self.assertEqual(result.stop_reason, StopReason.COMPLETED)
        self.assertEqual(result.final_text, "recovered")
        self.assertEqual(counter[0], 0)
        # 模型第二轮收到 denied 结果
        second = model.received_messages[1]
        tool_msg = [m for m in second if m.get("role") == "tool"][0]
        parsed = json.loads(tool_msg["content"])
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "TOOL_DENIED_BY_POLICY")
        self.assertFalse(parsed["error"]["retryable"])
        self.assertEqual(parsed["status"], "denied")
        # 有 tool_denied 事件
        denied_events = [e for e in result.events if e.event_type == EventType.TOOL_DENIED.value]
        self.assertTrue(denied_events)


# ======================================================================
# 4. 正确批准后只执行一次
# ======================================================================


class TestApproveExecutesOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appr_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_approve_executes_once(self):
        spec, counter = _counting_guarded_tool()
        reg = build_registry([spec])
        ctx = _make_ctx(self.tmp, "run_appr")
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="guarded_op", arguments={"msg": "x"})]),
            AssistantTurn(final_text="done"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=5)
        r1 = rt.run("go")
        self.assertEqual(r1.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(counter[0], 0)
        # 批准
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        self.assertEqual(r2.stop_reason, StopReason.COMPLETED)
        self.assertEqual(r2.final_text, "done")
        # 只执行了一次
        self.assertEqual(counter[0], 1)
        # 有 approval_resolved(approved) 事件
        resolved = [e for e in r2.events if e.event_type == EventType.APPROVAL_RESOLVED.value]
        self.assertTrue(resolved)
        self.assertEqual(resolved[-1].payload["outcome"], "approved")


# ======================================================================
# 5. 拒绝后不执行，并反馈模型
# ======================================================================


class TestRejectFeedback(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rej_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reject_does_not_execute_and_feeds_back(self):
        spec, counter = _counting_guarded_tool()
        reg = build_registry([spec])
        ctx = _make_ctx(self.tmp, "run_rej")
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="guarded_op", arguments={"msg": "x"})]),
            AssistantTurn(final_text="chose alternative"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=5)
        r1 = rt.run("go")
        self.assertEqual(r1.stop_reason, StopReason.AWAITING_APPROVAL)
        # 拒绝
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=False))
        self.assertEqual(r2.stop_reason, StopReason.COMPLETED)
        self.assertEqual(r2.final_text, "chose alternative")
        self.assertEqual(counter[0], 0)
        # 模型第二轮收到 rejected 结果
        second = model.received_messages[1]
        tool_msg = [m for m in second if m.get("role") == "tool"][0]
        parsed = json.loads(tool_msg["content"])
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "TOOL_REJECTED_BY_USER")
        self.assertFalse(parsed["error"]["retryable"])
        self.assertEqual(parsed["status"], "rejected")
        # 有 approval_resolved(rejected) 事件
        resolved = [e for e in r2.events if e.event_type == EventType.APPROVAL_RESOLVED.value]
        self.assertTrue(resolved)
        self.assertEqual(resolved[-1].payload["outcome"], "rejected")


# ======================================================================
# 6. 错误 request_id / 参数篡改 / 跨 run / 重复审批被拒绝
# ======================================================================


class TestResumeRejection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rej2_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_paused(self, run_id="run_rej"):
        spec, counter = _counting_guarded_tool()
        reg = build_registry([spec])
        ctx = _make_ctx(self.tmp, run_id)
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="guarded_op", arguments={"msg": "x"})]),
            AssistantTurn(final_text="done"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=5)
        r1 = rt.run("go")
        self.assertEqual(r1.stop_reason, StopReason.AWAITING_APPROVAL)
        return rt, r1, counter

    # 6a. 错误 request_id
    def test_wrong_request_id_rejected(self):
        rt, r1, counter = self._setup_paused()
        r2 = rt.resume(ApprovalResponse(request_id="wrong-id", approved=True))
        # 保留 pending，仍 awaiting_approval
        self.assertEqual(r2.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(counter[0], 0)
        # pending 仍在
        self.assertIsNotNone(rt._pending)
        resolved = [e for e in r2.events if e.event_type == EventType.APPROVAL_RESOLVED.value]
        self.assertEqual(resolved[-1].payload["outcome"], "rejected_wrong_id")

    # 6b. 参数篡改
    def test_tampered_arguments_rejected(self):
        rt, r1, counter = self._setup_paused()
        # 白盒篡改 pending 的 arguments
        rt._pending.arguments = {"msg": "TAMPERED"}
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        self.assertEqual(r2.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(counter[0], 0)
        self.assertIsNotNone(rt._pending)
        resolved = [e for e in r2.events if e.event_type == EventType.APPROVAL_RESOLVED.value]
        self.assertEqual(resolved[-1].payload["outcome"], "rejected_tampered")

    # 6c. 跨 run（pending.run_id != context.run_id）
    def test_cross_run_rejected(self):
        rt, r1, counter = self._setup_paused(run_id="run_a")
        # 白盒篡改 pending.run_id，模拟跨 run 审批
        rt._pending.run_id = "run_b"
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        self.assertEqual(r2.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(counter[0], 0)
        resolved = [e for e in r2.events if e.event_type == EventType.APPROVAL_RESOLVED.value]
        self.assertEqual(resolved[-1].payload["outcome"], "rejected_cross_run")

    # 6d. 重复审批（无 pending 时 resume → runtime_error）
    def test_double_resume_rejected(self):
        rt, r1, counter = self._setup_paused()
        # 第一次批准，消费 pending
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        self.assertEqual(r2.stop_reason, StopReason.COMPLETED)
        self.assertEqual(counter[0], 1)
        # 第二次 resume：无 pending → runtime_error
        r3 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        self.assertEqual(r3.stop_reason, StopReason.RUNTIME_ERROR)
        # 计数不增加
        self.assertEqual(counter[0], 1)


# ======================================================================
# 7. resume 后轮数和重复检测不重置
# ======================================================================


class TestResumeDoesNotResetCounters(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cnt_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_max_tool_turns_not_reset_after_resume(self):
        # max_tool_turns=1：第一轮 guarded ASK 暂停；resume 批准后执行（tool_turns 已=1）；
        # 模型再发一轮 tool_call → 应触发 max_tool_turns（不重置）
        spec, counter = _counting_guarded_tool()
        reg = build_registry([spec])
        ctx = _make_ctx(self.tmp, "run_cnt")
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="guarded_op", arguments={"msg": "a"})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c2", name="guarded_op", arguments={"msg": "b"})]),
            AssistantTurn(final_text="never"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=1)
        r1 = rt.run("go")
        self.assertEqual(r1.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(r1.tool_turns, 1)
        # resume 批准第一轮
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        # 第一轮执行后，模型第二轮发 tool_call → max_tool_turns=1 已达 → MAX_TOOL_TURNS
        self.assertEqual(r2.stop_reason, StopReason.MAX_TOOL_TURNS)
        self.assertEqual(r2.tool_turns, 1)  # 未重置，仍 1
        # 第一轮的 guarded 执行了 1 次；第二轮未执行（max 拦截）
        self.assertEqual(counter[0], 1)

    def test_repeated_detection_not_reset_after_resume(self):
        # resume 后，下一轮与"暂停前那一轮"指纹相同 → repeated_tool_call
        spec, counter = _counting_guarded_tool()
        reg = build_registry([spec])
        ctx = _make_ctx(self.tmp, "run_rep")
        same_args = {"msg": "same"}
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="guarded_op", arguments=same_args)]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c2", name="guarded_op", arguments=same_args)]),
            AssistantTurn(final_text="never"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=10)
        r1 = rt.run("go")
        self.assertEqual(r1.stop_reason, StopReason.AWAITING_APPROVAL)
        # resume 批准第一轮（执行）
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        # 第二轮与第一轮指纹相同 → repeated_tool_call（重复检测未重置）
        self.assertEqual(r2.stop_reason, StopReason.REPEATED_TOOL_CALL)


# ======================================================================
# 8. 多 ToolCall 暂停后正确继续
# ======================================================================


class TestMultiToolCallResume(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="multi_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_multi_toolcall_pause_and_continue(self):
        # 一轮三个调用：c1(READ) → c2(GUARDED, ASK 暂停) → c3(READ)
        # c1 执行 → c2 ASK 暂停（c3 未处理）
        # resume 批准 → c2 执行 → c3 执行 → 模型 final_text
        spec_r, counter_r = _counting_read_tool("read_op")
        spec_g, counter_g = _counting_guarded_tool("guarded_op")
        spec_r2, counter_r2 = _counting_read_tool("read_op2")
        reg = build_registry([spec_r, spec_g, spec_r2])
        ctx = _make_ctx(self.tmp, "run_multi")
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[
                ToolCall(call_id="c1", name="read_op", arguments={}),
                ToolCall(call_id="c2", name="guarded_op", arguments={}),
                ToolCall(call_id="c3", name="read_op2", arguments={}),
            ]),
            AssistantTurn(final_text="all done"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=5)
        r1 = rt.run("go")
        # c1 执行了，c2 ASK 暂停，c3 未执行
        self.assertEqual(r1.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(counter_r[0], 1)   # c1 执行
        self.assertEqual(counter_g[0], 0)   # c2 未执行（ASK）
        self.assertEqual(counter_r2[0], 0)  # c3 未处理
        self.assertEqual(r1.pending_approval.call_id, "c2")
        # resume 批准 c2
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        self.assertEqual(r2.stop_reason, StopReason.COMPLETED)
        self.assertEqual(r2.final_text, "all done")
        # c2 执行 1 次，c3 执行 1 次，c1 仍 1 次（无丢失无重复）
        self.assertEqual(counter_r[0], 1)
        self.assertEqual(counter_g[0], 1)
        self.assertEqual(counter_r2[0], 1)
        # tool_turns=1（整轮算 1 轮）
        self.assertEqual(r2.tool_turns, 1)

    def test_multi_toolcall_deny_continues_remaining(self):
        # 一轮：c1(READ) → c2(GUARDED, DENY) → c3(READ)
        # c1 执行 → c2 DENY 回填 → c3 执行（不因 DENY 中断）
        spec_r, counter_r = _counting_read_tool("read_op")
        spec_g, counter_g = _counting_guarded_tool("guarded_op")
        spec_r2, counter_r2 = _counting_read_tool("read_op2")
        cfg = PolicyConfig.with_overrides({"guarded_op": PolicyAction.DENY})
        reg = build_registry([spec_r, spec_g, spec_r2])
        ctx = _make_ctx(self.tmp, "run_mdeny")
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[
                ToolCall(call_id="c1", name="read_op", arguments={}),
                ToolCall(call_id="c2", name="guarded_op", arguments={}),
                ToolCall(call_id="c3", name="read_op2", arguments={}),
            ]),
            AssistantTurn(final_text="done"),
        ])
        rt = AgentRuntime(model, reg, ctx, policy=PolicyEngine(cfg), max_tool_turns=5)
        result = rt.run("go")
        self.assertEqual(result.stop_reason, StopReason.COMPLETED)
        self.assertEqual(counter_r[0], 1)   # c1 执行
        self.assertEqual(counter_g[0], 0)   # c2 DENY 未执行
        self.assertEqual(counter_r2[0], 1)  # c3 执行（DENY 不中断后续）


# ======================================================================
# 9. guarded remediation 默认 ASK
# ======================================================================


class TestGuardedRemediationDefaultAsk(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="grd_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_guarded_remediation_default_ask(self):
        # 用真实 pipeline 工具：configure → profile → plan → prepare → validate(failed)
        # → run_safe_remediation（guarded）默认 ASK
        reg = build_default_registry()
        ctx = _make_ctx(self.tmp, "run_grd")
        # 注入 2 行 close 缺失 → initial failed
        price_path = ctx.input_dir / "price.csv"
        df = pd.read_csv(price_path)
        idx = df.sample(n=min(2, len(df)), random_state=0).index
        df.loc[idx, "close"] = None
        df.to_csv(price_path, index=False, encoding="utf-8-sig")

        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="configure_workflow", arguments={"max_row_loss_ratio": 0.5})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c2", name="profile_financial_data", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c3", name="create_workflow_plan", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c4", name="prepare_financial_panel", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c5", name="validate_financial_panel", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c6", name="run_safe_remediation", arguments={})]),
            AssistantTurn(final_text="never"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=10)
        result = rt.run("go")
        # run_safe_remediation 是 guarded → 默认 ASK → 暂停
        self.assertEqual(result.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(result.pending_approval.tool_name, "run_safe_remediation")
        # 有 policy_decision(ask) 事件
        decisions = [e for e in result.events if e.event_type == EventType.POLICY_DECISION.value]
        remediation_decisions = [d for d in decisions if d.payload["name"] == "run_safe_remediation"]
        self.assertTrue(remediation_decisions)
        self.assertEqual(remediation_decisions[-1].payload["action"], "ask")


# ======================================================================
# 10. 批准后仍受金融安全门约束
# ======================================================================


class TestApprovalDoesNotBypassSafetyGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_approval_does_not_bypass_safety_gate(self):
        # 注入 1 行 close 缺失（1/7≈14% > 5% 安全门）→ manual_review_required
        # 批准 run_safe_remediation 后，执行仍触发安全门 → requires_user_action
        reg = build_default_registry()
        ctx = _make_ctx(self.tmp, "run_gate")
        price_path = ctx.input_dir / "price.csv"
        df = pd.read_csv(price_path)
        idx = df.sample(n=1, random_state=0).index
        df.loc[idx, "close"] = None
        df.to_csv(price_path, index=False, encoding="utf-8-sig")

        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="configure_workflow", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c2", name="profile_financial_data", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c3", name="create_workflow_plan", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c4", name="prepare_financial_panel", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c5", name="validate_financial_panel", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c6", name="run_safe_remediation", arguments={})]),
            AssistantTurn(final_text="never"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=10)
        r1 = rt.run("go")
        # run_safe_remediation guarded → ASK 暂停
        self.assertEqual(r1.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(r1.pending_approval.tool_name, "run_safe_remediation")
        # 批准
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        # 执行后安全门触发 → manual_review_required → requires_user_action
        self.assertEqual(r2.stop_reason, StopReason.REQUIRES_USER_ACTION)
        # 找到 run_safe_remediation 的 tool_result
        tr_events = [e for e in r2.events if e.event_type == EventType.TOOL_RESULT.value
                     and e.payload["name"] == "run_safe_remediation"]
        self.assertTrue(tr_events)
        res = tr_events[-1].payload["result"]
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "manual_review_required")
        self.assertTrue(res["requires_user_action"])
        self.assertEqual(res["metrics"]["termination_reason"], "manual_review_required")
        self.assertTrue(res["metrics"]["manual_review_required"])


# ======================================================================
# 11. awaiting_approval 与 requires_user_action 不混淆
# ======================================================================


class TestAwaitingVsRequiresUserAction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vs_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_awaiting_vs_requires_user_action_distinct(self):
        # awaiting_approval：执行前、未执行、有 pending
        spec_g, _ = _counting_guarded_tool()
        reg = build_registry([spec_g])
        ctx = _make_ctx(self.tmp, "run_vs1")
        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="guarded_op", arguments={})]),
            AssistantTurn(final_text="done"),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=5)
        r = rt.run("go")
        self.assertEqual(r.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertIsNotNone(r.pending_approval)
        # 工具未执行 → 无 tool_result 事件
        tr = [e for e in r.events if e.event_type == EventType.TOOL_RESULT.value]
        self.assertFalse(tr)

        # requires_user_action：执行后、已执行、无 pending
        def handler(args, ctx):
            return ToolResult(
                ok=False, status="manual_review_required",
                summary="needs review", requires_user_action=True,
            )
        spec_r = ToolSpec(
            name="read_ua", description="",
            input_schema={"type": "object", "properties": {}, "required": []},
            risk_level=RiskLevel.READ, handler=handler,
        )
        reg2 = build_registry([spec_r])
        ctx2 = _make_ctx(self.tmp, "run_vs2")
        model2 = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="read_ua", arguments={})]),
            AssistantTurn(final_text="never"),
        ])
        rt2 = AgentRuntime(model2, reg2, ctx2, max_tool_turns=5)
        r2 = rt2.run("go")
        self.assertEqual(r2.stop_reason, StopReason.REQUIRES_USER_ACTION)
        self.assertIsNone(r2.pending_approval)
        # 工具已执行 → 有 tool_result 事件
        tr2 = [e for e in r2.events if e.event_type == EventType.TOOL_RESULT.value]
        self.assertTrue(tr2)


# ======================================================================
# 12. no-op repair 只使用公开 API
# ======================================================================


class TestNoopRepairPublicAPIOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="noop_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_noop_repair_uses_public_api(self):
        # 行为：initial passed → run_safe_remediation 返回 not_needed 且产物齐全
        reg = build_default_registry()
        ctx = _make_ctx(self.tmp, "run_noop")
        # 清理 OHLC 缺失 → initial passed
        price_path = ctx.input_dir / "price.csv"
        df = pd.read_csv(price_path)
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        df.to_csv(price_path, index=False, encoding="utf-8-sig")

        from agent_runtime.models import ToolCall
        reg.execute(ToolCall(call_id="c1", name="configure_workflow", arguments={}), ctx)
        reg.execute(ToolCall(call_id="c2", name="profile_financial_data", arguments={}), ctx)
        reg.execute(ToolCall(call_id="c3", name="create_workflow_plan", arguments={}), ctx)
        reg.execute(ToolCall(call_id="c4", name="prepare_financial_panel", arguments={}), ctx)
        vc = reg.execute(ToolCall(call_id="c5", name="validate_financial_panel", arguments={}), ctx)
        self.assertNotEqual(vc.metrics["overall_status"], "failed")
        rem = reg.execute(ToolCall(call_id="c6", name="run_safe_remediation", arguments={}), ctx)
        self.assertTrue(rem.ok)
        self.assertEqual(rem.status, "not_needed")
        # 产物齐全
        runner = ctx.get_runner()
        self.assertTrue(runner.repaired_panel.exists())
        self.assertTrue(runner.repair_plan.exists())
        self.assertTrue(runner.repair_history_json.exists())

    def test_pipeline_tools_no_private_method_calls(self):
        # 源码断言：pipeline_tools.py 不再直接调用私有方法
        src_path = SRC / "agent_tools" / "pipeline_tools.py"
        text = src_path.read_text(encoding="utf-8")
        # 只检查"调用"形式 runner._xxx( 或 ctx._xxx(，不匹配注释/文档字符串中的提及。
        import re
        for forbidden in [
            "_write_noop_repair_artifacts",
            "_write_repair_history",
            "_mark_skipped",
        ]:
            # 匹配 <ident>._forbidden( 形式的调用（前面是 . 表示方法调用）
            pattern = r"\." + re.escape(forbidden) + r"\s*\("
            self.assertFalse(
                re.search(pattern, text),
                f"pipeline_tools.py must not call private method {forbidden}()",
            )
        # 应调用公开 run_noop_repair
        self.assertIn("run_noop_repair", text)


# ======================================================================
# 13. 实际修复路径端到端通过
# ======================================================================


class TestEndToEndRepairPath(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="e2e_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_end_to_end_repair_path_with_approval(self):
        # configure → profile → plan → prepare → validate(failed)
        # → run_safe_remediation[ASK] → [resume 批准] → validate_repaired → report → final_text
        reg = build_default_registry()
        ctx = _make_ctx(self.tmp, "run_e2e")
        # 注入 2 行 close 缺失 → initial failed → 一轮收敛
        price_path = ctx.input_dir / "price.csv"
        df = pd.read_csv(price_path)
        idx = df.sample(n=min(2, len(df)), random_state=0).index
        df.loc[idx, "close"] = None
        df.to_csv(price_path, index=False, encoding="utf-8-sig")

        model = ScriptedFakeModel([
            AssistantTurn(tool_calls=[ToolCall(call_id="c1", name="configure_workflow", arguments={"max_row_loss_ratio": 0.5})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c2", name="profile_financial_data", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c3", name="create_workflow_plan", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c4", name="prepare_financial_panel", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c5", name="validate_financial_panel", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c6", name="run_safe_remediation", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c7", name="validate_repaired_panel", arguments={})]),
            AssistantTurn(tool_calls=[ToolCall(call_id="c8", name="generate_workflow_report", arguments={})]),
            AssistantTurn(final_text="All stages complete; final report generated."),
        ])
        rt = AgentRuntime(model, reg, ctx, max_tool_turns=20)
        r1 = rt.run("run full pipeline")
        # run_safe_remediation guarded → ASK 暂停
        self.assertEqual(r1.stop_reason, StopReason.AWAITING_APPROVAL)
        self.assertEqual(r1.pending_approval.tool_name, "run_safe_remediation")
        # 批准
        r2 = rt.resume(ApprovalResponse(request_id=r1.pending_approval.request_id, approved=True))
        self.assertEqual(r2.stop_reason, StopReason.COMPLETED)
        self.assertEqual(r2.final_text, "All stages complete; final report generated.")
        # final_report 存在
        runner = ctx.get_runner()
        self.assertTrue(runner.full_report_md.exists())
        # 修复后 validation 应 passed/passed_with_warnings
        tr_repaired = [e for e in r2.events if e.event_type == EventType.TOOL_RESULT.value
                      and e.payload["name"] == "validate_repaired_panel"]
        self.assertTrue(tr_repaired)
        self.assertIn(
            tr_repaired[-1].payload["result"]["metrics"]["overall_status"],
            ("passed", "passed_with_warnings"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
