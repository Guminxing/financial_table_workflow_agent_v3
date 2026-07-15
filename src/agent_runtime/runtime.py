"""AgentRuntime：有界 tool-calling 循环 + 进程内审批恢复（Stage 9–10）。

输入：ModelClient + ToolRegistry + AgentContext + PolicyEngine + max_tool_turns。

行为：
1. 追加用户消息。
2. 调用 ModelClient.complete()。
3. 若返回 final_text，正常结束（completed）。
4. 若返回 tool_calls：
   - **执行前先过 PolicyEngine**（Stage 10）：
     - ALLOW：记录决策并执行。
     - ASK：不执行 handler；创建 PendingApproval，记录事件并暂停
       （stop_reason=awaiting_approval）。
     - DENY：不执行 handler；回填 status=denied / code=TOOL_DENIED_BY_POLICY /
       retryable=False，让模型选择安全替代方案。
   - 顺序执行，不并行；
   - 记录 tool_call / tool_result / policy_decision / approval_* / tool_denied 事件；
   - 将结构化结果追加到下一轮模型 messages。
5. 继续调用模型，直到停止条件。

暂停与恢复（Stage 10）：
- ``run()`` 在 ASK 处暂停，返回 ``AgentRunResult(stop_reason=awaiting_approval,
  pending_approval=...)``。
- ``resume(ApprovalResponse)`` 校验 request_id / run_id / fingerprint（防篡改、
  防跨 run、防重放），批准则执行原 ToolCall 一次，拒绝则回填
  ``TOOL_REJECTED_BY_USER``，然后从暂停位置继续处理同一轮剩余 ToolCall，
  再继续 Agent 循环。
- resume **不重置** max_tool_turns 与重复调用检测状态。

硬约束：
- Runtime 不直接调用 PipelineRunner；只能通过 ToolRegistry 调用工具。
- PolicyEngine 是**唯一**的执行前授权入口；每个已知工具执行前必过 policy.decide。
- Runtime 不自行判断金融校验是否通过；金融状态以 ToolResult 为唯一事实来源。
- 工具失败后必须把失败结果反馈给模型，不能假装成功。
- requires_user_action=True 时本轮停止，不能继续自动修复。
- 不允许无限循环（max_tool_turns + 重复检测双保险）。
- 审批只决定"是否执行"，执行仍走 ToolRegistry → PipelineRunner → Remediation
  Agent，**不绕过**删行阈值、轮数限制、标签泄漏保护等内部安全门。
- 不记录或输出隐藏推理过程；只记录输入、工具调用、工具结果和最终文本。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from .context import AgentContext
from .model_client import ModelClient
from .models import (
    AgentEvent,
    AgentRunResult,
    AssistantTurn,
    EventType,
    StopReason,
    ToolCall,
    ToolResult,
)
from .policy import (
    ApprovalResponse,
    PolicyAction,
    PolicyDecision,
    PolicyEngine,
    PendingApproval,
    make_fingerprint,
    new_request_id,
)
from .registry import ToolRegistry


DEFAULT_MAX_TOOL_TURNS = 12

# 内部哨兵：表示"本轮 tool_calls 已全部处理完，应继续下一轮模型调用"。
# 与 StopReason.COMPLETED（模型返回 final_text，真正结束）区分开。
_CONTINUE = "_continue"

# resume 校验失败时的 outcome 标签（写入 APPROVAL_RESOLVED 事件）
_OUTCOME_WRONG_ID = "rejected_wrong_id"
_OUTCOME_CROSS_RUN = "rejected_cross_run"
_OUTCOME_TAMPERED = "rejected_tampered"
_OUTCOME_NO_PENDING = "no_pending"
_OUTCOME_APPROVED = "approved"
_OUTCOME_REJECTED = "rejected"


class AgentRuntime:
    """最小 tool-calling Agent Runtime + 进程内审批恢复。

    用法::

        runtime = AgentRuntime(
            model=fake_model,
            registry=build_default_registry(),
            context=ctx,
            policy=PolicyEngine(),  # 默认策略
            max_tool_turns=12,
        )
        result = runtime.run("Please profile the data and report.")
        if result.stop_reason == StopReason.AWAITING_APPROVAL:
            resp = ApprovalResponse(
                request_id=result.pending_approval.request_id,
                approved=True,
            )
            result = runtime.resume(resp)
    """

    def __init__(
        self,
        model: ModelClient,
        registry: ToolRegistry,
        context: AgentContext,
        policy: PolicyEngine | None = None,
        max_tool_turns: int = DEFAULT_MAX_TOOL_TURNS,
        event_callback: "Callable[[AgentEvent], None] | None" = None,
    ) -> None:
        if max_tool_turns < 1:
            raise ValueError(f"max_tool_turns must be >= 1, got {max_tool_turns}")
        self.model = model
        self.registry = registry
        self.context = context
        self.policy = policy if policy is not None else PolicyEngine()
        self.max_tool_turns = int(max_tool_turns)
        # Stage 11：可选事件回调，供 CLI 实时打印工具调用进度。
        # 回调异常被吞掉，绝不影响 Runtime 主循环；不打印完整 messages / 隐藏推理 / API Key。
        self._event_callback = event_callback

        # 运行时状态（run 开始时重置；resume 不重置）
        self._events: list[AgentEvent] = []
        self._messages: list[dict[str, Any]] = []
        self._tool_turns = 0
        self._prev_calls_fp: str | None = None

        # 暂停状态（ASK 时设置；resume 消费后清空）
        self._pending: PendingApproval | None = None
        self._paused_turn: AssistantTurn | None = None
        self._paused_index: int = 0

    # ------------------------------------------------------------------
    # 主入口：run / resume
    # ------------------------------------------------------------------

    def run(self, user_message: str) -> AgentRunResult:
        """运行一次 Agent run。

        返回 :class:`AgentRunResult`。若 ``stop_reason == awaiting_approval``，
        调用方应收集 :attr:`AgentRunResult.pending_approval`，构造
        :class:`ApprovalResponse` 后调用 :meth:`resume`。
        """
        self._reset_state()
        self._append_user_message(user_message)

        try:
            while True:
                outcome = self._do_model_turn()
                if outcome == _CONTINUE:
                    continue
                return self._result(outcome)
        except Exception as exc:  # noqa: BLE001
            self._record_event(
                EventType.RUNTIME_STOP,
                {
                    "stop_reason": StopReason.RUNTIME_ERROR,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return self._result(StopReason.RUNTIME_ERROR)

    def resume(self, response: ApprovalResponse) -> AgentRunResult:
        """审批恢复：消费一次 PendingApproval，执行或拒绝原 ToolCall，继续循环。

        校验（任一失败即拒绝，**保留 pending 不消费**，返回 awaiting_approval）：
        - 当前必须有 pending（无 pending → runtime_error，防重放）；
        - ``response.request_id == pending.request_id``；
        - ``pending.run_id == context.run_id``（防跨 run）；
        - ``make_fingerprint(pending.call, pending.run_id) == pending.fingerprint``
          （防参数篡改）。

        校验通过后**一次性消费** pending（清空 _pending/_paused_turn/_paused_index）：
        - approved=True：执行原 ToolCall 一次，回填结果；
        - approved=False：回填 ``TOOL_REJECTED_BY_USER``，不执行。

        然后从暂停位置 +1 继续处理同一轮剩余 ToolCall，再继续 Agent 循环。
        resume **不重置** max_tool_turns 与重复检测状态。
        """
        if self._pending is None:
            # 无 pending：可能是重复审批或未暂停。记 runtime_error。
            self._record_event(
                EventType.APPROVAL_RESOLVED,
                {"outcome": _OUTCOME_NO_PENDING, "request_id": response.request_id},
            )
            self._record_event(
                EventType.RUNTIME_STOP,
                {"stop_reason": StopReason.RUNTIME_ERROR,
                 "error": "resume called with no pending approval"},
            )
            return self._result(StopReason.RUNTIME_ERROR)

        pending = self._pending
        # 校验 request_id
        if response.request_id != pending.request_id:
            self._record_event(
                EventType.APPROVAL_RESOLVED,
                {
                    "outcome": _OUTCOME_WRONG_ID,
                    "expected": pending.request_id,
                    "got": response.request_id,
                },
            )
            return self._result(StopReason.AWAITING_APPROVAL)

        # 校验 run_id（防跨 run）
        if pending.run_id != self.context.run_id:
            self._record_event(
                EventType.APPROVAL_RESOLVED,
                {
                    "outcome": _OUTCOME_CROSS_RUN,
                    "expected_run": self.context.run_id,
                    "pending_run": pending.run_id,
                },
            )
            return self._result(StopReason.AWAITING_APPROVAL)

        # 校验 fingerprint（防参数篡改）
        current_fp = make_fingerprint(
            pending.tool_name, pending.arguments, pending.run_id
        )
        if current_fp != pending.fingerprint:
            self._record_event(
                EventType.APPROVAL_RESOLVED,
                {
                    "outcome": _OUTCOME_TAMPERED,
                    "expected": pending.fingerprint,
                    "got": current_fp,
                },
            )
            return self._result(StopReason.AWAITING_APPROVAL)

        # 校验通过：一次性消费 pending
        turn = self._paused_turn
        start_index = self._paused_index
        self._pending = None
        self._paused_turn = None
        self._paused_index = 0

        try:
            # 执行或拒绝原 ToolCall
            call = ToolCall(
                call_id=pending.call_id,
                name=pending.tool_name,
                arguments=pending.arguments,
            )
            if response.approved:
                self._record_event(
                    EventType.APPROVAL_RESOLVED,
                    {"outcome": _OUTCOME_APPROVED, "request_id": pending.request_id},
                )
                self._record_event(
                    EventType.TOOL_CALL,
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "resumed": True,
                    },
                )
                result = self.registry.execute(call, self.context)
            else:
                self._record_event(
                    EventType.APPROVAL_RESOLVED,
                    {"outcome": _OUTCOME_REJECTED, "request_id": pending.request_id},
                )
                self._record_event(
                    EventType.TOOL_CALL,
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "resumed": True,
                    },
                )
                result = ToolResult.failure(
                    f"tool {call.name} rejected by user",
                    code="TOOL_REJECTED_BY_USER",
                    status="rejected",
                    retryable=False,
                    next_actions=[],
                )

            self._record_event(
                EventType.TOOL_RESULT,
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "result": result.to_dict(),
                },
            )
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "content": json.dumps(result.to_dict(), ensure_ascii=False),
                }
            )
            if result.requires_user_action:
                self._record_event(
                    EventType.RUNTIME_STOP,
                    {"stop_reason": StopReason.REQUIRES_USER_ACTION},
                )
                return self._result(StopReason.REQUIRES_USER_ACTION)

            # 从暂停位置 +1 继续处理同一轮剩余 ToolCall
            outcome = self._process_calls(turn, start_index + 1)
            if outcome == _CONTINUE:
                while True:
                    o2 = self._do_model_turn()
                    if o2 == _CONTINUE:
                        continue
                    return self._result(o2)
            return self._result(outcome)
        except Exception as exc:  # noqa: BLE001
            self._record_event(
                EventType.RUNTIME_STOP,
                {
                    "stop_reason": StopReason.RUNTIME_ERROR,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return self._result(StopReason.RUNTIME_ERROR)

    # ------------------------------------------------------------------
    # 模型轮：调用模型 + 处理整轮 tool_calls
    # ------------------------------------------------------------------

    def _do_model_turn(self) -> str:
        """一次模型轮：调用模型，处理整轮 tool_calls。

        返回 stop_reason（真正停止），或 ``_CONTINUE`` 表示本轮 tool_calls 已
        全部处理完、应继续下一轮模型调用。
        """
        tools = self.registry.schemas_for_model()
        turn: AssistantTurn = self.model.complete(self._messages, tools)

        # 协议校验
        if not turn.is_valid():
            self._record_event(
                EventType.ASSISTANT_TURN,
                {
                    "final_text": turn.final_text,
                    "tool_calls": [c.to_dict() for c in turn.tool_calls],
                    "valid": False,
                },
            )
            self._record_event(
                EventType.RUNTIME_STOP,
                {"stop_reason": StopReason.MODEL_PROTOCOL_ERROR},
            )
            return StopReason.MODEL_PROTOCOL_ERROR

        # 记录 assistant_turn 事件
        self._record_event(
            EventType.ASSISTANT_TURN,
            {
                "final_text": turn.final_text,
                "tool_calls": [c.to_dict() for c in turn.tool_calls],
                "valid": True,
            },
        )

        # final_text → 结束
        if turn.final_text is not None and turn.final_text.strip():
            self._messages.append(
                {"role": "assistant", "content": turn.final_text}
            )
            self._record_event(
                EventType.RUNTIME_STOP,
                {"stop_reason": StopReason.COMPLETED, "tool_turns": self._tool_turns},
            )
            return StopReason.COMPLETED

        # tool_calls
        # 重复检测：相同工具名 + 规范化参数连续重复
        if self._is_repeated_tool_calls(turn.tool_calls):
            self._record_event(
                EventType.RUNTIME_STOP,
                {
                    "stop_reason": StopReason.REPEATED_TOOL_CALL,
                    "tool_calls": [c.to_dict() for c in turn.tool_calls],
                },
            )
            return StopReason.REPEATED_TOOL_CALL

        # 达到 max_tool_turns
        if self._tool_turns >= self.max_tool_turns:
            self._record_event(
                EventType.RUNTIME_STOP,
                {
                    "stop_reason": StopReason.MAX_TOOL_TURNS,
                    "tool_turns": self._tool_turns,
                    "max_tool_turns": self.max_tool_turns,
                },
            )
            return StopReason.MAX_TOOL_TURNS

        self._tool_turns += 1

        # 整轮的 assistant 消息在此一次性追加（含 tool_calls）；
        # 后续 _process_calls / resume 只追加 tool 结果，保证暂停时消息序列
        # 是 assistant[c1,c2,c3] + tool(c1)...，无丢失无重复。
        self._messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [c.to_dict() for c in turn.tool_calls],
            }
        )

        # 顺序处理本轮 tool_calls（不并行）
        return self._process_calls(turn, 0)

    def _process_calls(self, turn: AssistantTurn, start_index: int) -> str:
        """从 start_index 顺序处理 turn.tool_calls。

        返回 stop_reason（真正停止），或 ``_CONTINUE`` 表示本轮全部处理完、
        应继续下一轮模型调用。遇到 ASK 时暂停并返回 awaiting_approval。
        """
        for i in range(start_index, len(turn.tool_calls)):
            call = turn.tool_calls[i]
            spec = self.registry.get(call.name)

            # 未知工具：不过 policy，直接走 registry（返回 UNKNOWN_TOOL）
            if spec is None:
                self._record_event(
                    EventType.TOOL_CALL,
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                )
                result = self.registry.execute(call, self.context)
                self._record_event(
                    EventType.TOOL_RESULT,
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "result": result.to_dict(),
                    },
                )
                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": json.dumps(result.to_dict(), ensure_ascii=False),
                    }
                )
                if result.requires_user_action:
                    self._record_event(
                        EventType.RUNTIME_STOP,
                        {"stop_reason": StopReason.REQUIRES_USER_ACTION},
                    )
                    return StopReason.REQUIRES_USER_ACTION
                continue

            # 已知工具：执行前过 PolicyEngine（唯一授权入口）
            decision = self.policy.decide(
                call.name, spec.risk_level, run_id=self.context.run_id
            )
            self._record_event(
                EventType.POLICY_DECISION,
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "risk_level": spec.risk_level.value,
                    "action": decision.action.value,
                    "rule_id": decision.rule_id,
                    "reason": decision.reason,
                },
            )

            if decision.action == PolicyAction.DENY:
                # DENY：不执行 handler，回填结构化拒绝
                self._record_event(
                    EventType.TOOL_CALL,
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                )
                self._record_event(
                    EventType.TOOL_DENIED,
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "rule_id": decision.rule_id,
                        "reason": decision.reason,
                    },
                )
                result = ToolResult.failure(
                    f"tool {call.name} denied by policy: {decision.reason}",
                    code="TOOL_DENIED_BY_POLICY",
                    status="denied",
                    retryable=False,
                    next_actions=[],
                )
                self._record_event(
                    EventType.TOOL_RESULT,
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "result": result.to_dict(),
                    },
                )
                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": json.dumps(result.to_dict(), ensure_ascii=False),
                    }
                )
                continue

            if decision.action == PolicyAction.ASK:
                # ASK：不执行 handler，创建 PendingApproval，暂停
                pending = PendingApproval(
                    request_id=new_request_id(),
                    call_id=call.call_id,
                    tool_name=call.name,
                    arguments=dict(call.arguments),
                    fingerprint=make_fingerprint(
                        call.name, call.arguments, self.context.run_id
                    ),
                    run_id=self.context.run_id,
                )
                self._pending = pending
                self._paused_turn = turn
                self._paused_index = i
                self._record_event(
                    EventType.APPROVAL_REQUESTED,
                    {
                        "request_id": pending.request_id,
                        "call_id": pending.call_id,
                        "tool_name": pending.tool_name,
                        "arguments": pending.arguments,
                        "run_id": pending.run_id,
                    },
                )
                self._record_event(
                    EventType.RUNTIME_STOP,
                    {"stop_reason": StopReason.AWAITING_APPROVAL},
                )
                return StopReason.AWAITING_APPROVAL

            # ALLOW：记录决策已在上；执行 handler
            self._record_event(
                EventType.TOOL_CALL,
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                },
            )
            result = self.registry.execute(call, self.context)
            self._record_event(
                EventType.TOOL_RESULT,
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "result": result.to_dict(),
                },
            )
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "content": json.dumps(result.to_dict(), ensure_ascii=False),
                }
            )
            if result.requires_user_action:
                self._record_event(
                    EventType.RUNTIME_STOP,
                    {"stop_reason": StopReason.REQUIRES_USER_ACTION},
                )
                return StopReason.REQUIRES_USER_ACTION

        # 本轮全部处理完
        return _CONTINUE

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _reset_state(self) -> None:
        """run 开始时重置运行时状态（resume 不调用此方法）。"""
        self._events = []
        self._messages = []
        self._tool_turns = 0
        self._prev_calls_fp = None
        self._pending = None
        self._paused_turn = None
        self._paused_index = 0

    def _result(self, stop_reason: str) -> AgentRunResult:
        return AgentRunResult(
            final_text=self._extract_final_text(stop_reason),
            stop_reason=stop_reason,
            events=list(self._events),
            tool_turns=self._tool_turns,
            pending_approval=self._pending if stop_reason == StopReason.AWAITING_APPROVAL else None,
        )

    def _extract_final_text(self, stop_reason: str) -> str | None:
        """从最后一条 assistant 消息提取 final_text（仅 completed 时有）。"""
        if stop_reason != StopReason.COMPLETED:
            return None
        # 最后一条 assistant 消息的 content 即 final_text
        for msg in reversed(self._messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return None

    def _append_user_message(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})
        self._record_event(EventType.USER_MESSAGE, {"content": text})

    def _record_event(self, event_type: EventType, payload: dict[str, Any]) -> None:
        event = AgentEvent(
            event_type=event_type.value,
            timestamp=_now_iso(),
            payload=payload,
        )
        self._events.append(event)
        # Stage 11：实时回调（异常吞掉，不影响主循环）
        cb = self._event_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:  # noqa: BLE001
                pass

    def _is_repeated_tool_calls(self, calls: list[ToolCall]) -> bool:
        """检测当前 tool_calls 是否与上一轮完全相同（工具名 + 规范化参数）。

        规范化：把 arguments 用 json.dumps(sort_keys=True) 序列化后比较。
        连续两轮调用完全相同的工具集合 → 视为重复循环，停止。
        """
        if not calls:
            return False
        current = self._fingerprint_calls(calls)
        prev = self._prev_calls_fp
        self._prev_calls_fp = current
        if prev is None:
            return False
        return prev == current

    @staticmethod
    def _fingerprint_calls(calls: list[ToolCall]) -> str:
        """规范化一组 tool_calls 为可比较的指纹串。"""
        items = []
        for c in calls:
            items.append(
                {
                    "name": c.name,
                    "arguments": json.dumps(
                        c.arguments, sort_keys=True, ensure_ascii=False
                    ),
                }
            )
        return json.dumps(items, sort_keys=True, ensure_ascii=False)


def _now_iso() -> str:
    """ISO8601 时间戳（Runtime 内部审计用，不作为业务时间源）。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
