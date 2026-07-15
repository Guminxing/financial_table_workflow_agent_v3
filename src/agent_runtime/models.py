"""Agent Runtime 核心数据模型（Stage 9 MVP）。

定义 Agent Runtime 所需的最小、清晰、可序列化的数据结构：

- :class:`RiskLevel`        工具风险等级（本轮只记录，不实现完整审批）
- :class:`ToolCall`         模型发起的一次工具调用
- :class:`ToolError`        工具执行失败的结构化错误
- :class:`ToolResult`       工具执行结果（成功/失败统一结构）
- :class:`ToolSpec`         工具规格（名称/描述/schema/risk/handler）
- :class:`AssistantTurn`    模型一轮输出（final_text 或 tool_calls）
- :class:`AgentEvent`       Runtime 事件（可序列化为审计流）
- :class:`AgentRunResult`   一次 Agent run 的最终结果

设计原则：
- 所有结构都容易转换为 JSON；**不**把 DataFrame / PipelineRunner 对象放入事件。
- 不依赖任何具体模型 SDK。
- 不读取环境变量中的 API Key。
- 本轮不接入真实 LLM；这些结构由 Fake Model 在测试中驱动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:  # 仅用于类型提示，避免运行时循环 import
    from .policy import PendingApproval


# ======================================================================
# 风险等级
# ======================================================================


class RiskLevel(str, Enum):
    """工具风险等级。

    本轮只记录 risk_level，不实现完整权限审批（allow/ask/deny 属下一阶段）。

    - ``read``: 只读，不修改任何文件 / 状态。
    - ``workspace_write``: 只允许写当前 run_root 下的派生产物。
    - ``guarded``: 触发有界、可审计的金融修复闭环；受安全门约束。
    """

    READ = "read"
    WORKSPACE_WRITE = "workspace_write"
    GUARDED = "guarded"


# ======================================================================
# ToolCall / ToolError / ToolResult
# ======================================================================


@dataclass
class ToolCall:
    """模型发起的一次工具调用。

    - ``call_id``: 模型生成的调用标识，用于把结果回填到对应调用。
    - ``name``: 工具名（必须在 ToolRegistry 中注册）。
    - ``arguments``: 工具参数（dict）；由 ToolRegistry 按 input_schema 校验。
    """

    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class ToolError:
    """工具执行失败的结构化错误。

    - ``code``: 机器可读错误码（如 ``INVALID_TOOL_ARGUMENTS`` / ``UNKNOWN_TOOL``）。
    - ``message``: 人类可读错误描述（不含完整 traceback / 凭据）。
    - ``retryable``: 模型是否可重试（如参数错误通常可重试，安全门违反不可重试）。
    """

    code: str
    message: str
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass
class ToolResult:
    """工具执行结果（成功与失败统一结构）。

    约定：
    - ``ok=True`` 表示工具成功完成其职责；``ok=False`` 表示失败或被安全门挡下。
    - ``status``: 机器可读状态串（如 ``completed`` / ``failed`` / ``invalid_arguments``
      / ``not_needed`` / ``manual_review_required``）。
    - ``summary``: 一句话人类可读摘要（给模型与用户看）。
    - ``metrics``: 关键指标（行数 / 状态 / 轮数等），扁平 dict，可序列化。
    - ``artifacts``: 产物路径列表；**必须**属于当前 run_root。
    - ``next_actions``: 建议的下一步工具名（供模型参考，非强制）。
    - ``error``: 失败时的结构化错误；成功时为 None。
    - ``requires_user_action``: 需要人工介入时为 True（如 manual_review_required）；
      Runtime 见此即停止本轮自动循环。
    """

    ok: bool
    status: str
    summary: str
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    error: ToolError | None = None
    requires_user_action: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "summary": self.summary,
            "metrics": self.metrics,
            "artifacts": self.artifacts,
            "next_actions": self.next_actions,
            "error": self.error.to_dict() if self.error is not None else None,
            "requires_user_action": self.requires_user_action,
        }

    # ------------------------------------------------------------------
    # 便捷工厂
    # ------------------------------------------------------------------

    @classmethod
    def success(
        cls,
        summary: str,
        *,
        status: str = "completed",
        metrics: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
        next_actions: list[str] | None = None,
    ) -> "ToolResult":
        return cls(
            ok=True,
            status=status,
            summary=summary,
            metrics=metrics or {},
            artifacts=artifacts or [],
            next_actions=next_actions or [],
            error=None,
            requires_user_action=False,
        )

    @classmethod
    def failure(
        cls,
        summary: str,
        *,
        code: str,
        status: str = "failed",
        retryable: bool = False,
        metrics: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
        next_actions: list[str] | None = None,
        requires_user_action: bool = False,
    ) -> "ToolResult":
        return cls(
            ok=False,
            status=status,
            summary=summary,
            metrics=metrics or {},
            artifacts=artifacts or [],
            next_actions=next_actions or [],
            error=ToolError(code=code, message=summary, retryable=retryable),
            requires_user_action=requires_user_action,
        )


# ======================================================================
# ToolSpec
# ======================================================================


# 工具 handler 签名：(arguments: dict, context: AgentContext) -> ToolResult
ToolHandler = Callable[[dict[str, Any], Any], ToolResult]


@runtime_checkable
class ToolHandlerProtocol(Protocol):
    def __call__(self, arguments: dict[str, Any], context: Any) -> ToolResult:
        ...


@dataclass
class ToolSpec:
    """工具规格。

    - ``name``: 唯一工具名（snake_case）。
    - ``description``: 给模型看的工具用途说明。
    - ``input_schema``: 通用 JSON Schema 风格的参数 schema（不绑定某家模型 API）。
    - ``risk_level``: 风险等级（本轮只记录）。
    - ``handler``: 实际执行函数 ``(arguments, context) -> ToolResult``。
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: RiskLevel
    handler: ToolHandler

    def to_schema_dict(self) -> dict[str, Any]:
        """导出给 ModelClient 的通用 schema（不含 handler）。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "risk_level": self.risk_level.value,
        }


# ======================================================================
# AssistantTurn
# ======================================================================


@dataclass
class AssistantTurn:
    """模型一轮输出。

    一轮输出**要么**给出最终文本回答（``final_text`` 非空且 ``tool_calls`` 为空），
    **要么**给出一个或多个工具调用（``tool_calls`` 非空且 ``final_text`` 为 None）。
    两者同时为空或同时非空视为协议错误（Runtime 以 ``model_protocol_error`` 停止）。
    """

    final_text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def is_valid(self) -> bool:
        """校验 final_text 与 tool_calls 的组合是否合法。"""
        has_text = self.final_text is not None and self.final_text.strip() != ""
        has_calls = bool(self.tool_calls)
        # 合法：恰好其一为真
        return has_text != has_calls  # XOR

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_text": self.final_text,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
        }


# ======================================================================
# AgentEvent
# ======================================================================


class EventType(str, Enum):
    """Runtime 事件类型。"""

    USER_MESSAGE = "user_message"
    ASSISTANT_TURN = "assistant_turn"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    RUNTIME_STOP = "runtime_stop"
    # Stage 10：权限与审批事件
    POLICY_DECISION = "policy_decision"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    TOOL_DENIED = "tool_denied"


@dataclass
class AgentEvent:
    """Runtime 事件（审计流）。

    - ``event_type``: 见 :class:`EventType`。
    - ``timestamp``: ISO8601 字符串（由调用方传入，避免 Runtime 依赖墙钟）。
    - ``payload``: 事件内容（已序列化，不含 DataFrame / runner 对象）。
    """

    event_type: str
    timestamp: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


# ======================================================================
# AgentRunResult
# ======================================================================


@dataclass
class AgentRunResult:
    """一次 Agent run 的最终结果。

    - ``final_text``: 模型最终回答（可能为 None，如因 max_tool_turns 停止）。
    - ``stop_reason``: 见 Runtime 的 stop_reason 枚举。
    - ``events``: 完整事件流（审计用）。
    - ``tool_turns``: 实际执行的工具轮数。
    - ``pending_approval``: 当 ``stop_reason == awaiting_approval`` 时，携带
      待审批请求（含 request_id / call_id / tool_name / arguments / fingerprint /
      run_id）；其他情况为 None。
    """

    final_text: str | None
    stop_reason: str
    events: list[AgentEvent] = field(default_factory=list)
    tool_turns: int = 0
    pending_approval: "PendingApproval | None" = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_text": self.final_text,
            "stop_reason": self.stop_reason,
            "events": [e.to_dict() for e in self.events],
            "tool_turns": self.tool_turns,
            "pending_approval": (
                self.pending_approval.to_dict()
                if self.pending_approval is not None
                else None
            ),
        }


# ======================================================================
# Runtime stop reasons（常量，避免魔法字符串）
# ======================================================================


class StopReason:
    """Runtime 停止原因。"""

    COMPLETED = "completed"
    MAX_TOOL_TURNS = "max_tool_turns"
    REPEATED_TOOL_CALL = "repeated_tool_call"
    REQUIRES_USER_ACTION = "requires_user_action"
    MODEL_PROTOCOL_ERROR = "model_protocol_error"
    RUNTIME_ERROR = "runtime_error"
    # Stage 10：工具执行前等待审批
    AWAITING_APPROVAL = "awaiting_approval"
