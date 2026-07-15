"""Agent Runtime 包（Stage 9–10）。

提供模型驱动的 tool-calling Agent Runtime 骨架 + 确定性权限审批：

- :mod:`agent_runtime.models`       核心数据结构（ToolCall/ToolResult/ToolSpec/...）
- :mod:`agent_runtime.context`       AgentContext + run_id 隔离
- :mod:`agent_runtime.registry`      ToolRegistry + 基础 schema 校验
- :mod:`agent_runtime.model_client`  ModelClient Protocol（不依赖具体 SDK）
- :mod:`agent_runtime.policy`        PolicyEngine + 审批数据模型（Stage 10）
- :mod:`agent_runtime.runtime`       有界 tool-calling 循环 + 进程内审批恢复

本轮**不**接入真实 LLM；这些组件由测试中的 Fake Model 驱动验证。
"""

from __future__ import annotations

from .models import (
    AgentEvent,
    AgentRunResult,
    AssistantTurn,
    EventType,
    RiskLevel,
    StopReason,
    ToolCall,
    ToolError,
    ToolHandler,
    ToolResult,
    ToolSpec,
)
from .policy import (
    ApprovalResponse,
    PolicyAction,
    PolicyConfig,
    PolicyDecision,
    PolicyEngine,
    PolicyRule,
    PendingApproval,
)

__all__ = [
    "AgentEvent",
    "AgentRunResult",
    "ApprovalResponse",
    "AssistantTurn",
    "EventType",
    "PendingApproval",
    "PolicyAction",
    "PolicyConfig",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyRule",
    "RiskLevel",
    "StopReason",
    "ToolCall",
    "ToolError",
    "ToolHandler",
    "ToolResult",
    "ToolSpec",
]
