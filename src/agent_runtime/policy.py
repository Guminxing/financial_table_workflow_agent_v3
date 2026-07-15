"""PolicyEngine + 审批数据模型（Stage 10）。

在每次工具执行前加入**确定性**权限判断：

    ToolCall
    → PolicyEngine
      ├─ ALLOW：执行工具
      ├─ ASK：暂停并返回 PendingApproval
      └─ DENY：不执行，向模型回填结构化拒绝
    → ApprovalResponse
    → AgentRuntime.resume()
    → 执行或拒绝原 ToolCall
    → 继续 Agent 循环

设计原则：
- PolicyEngine **完全确定性**：相同输入永远得到相同决策；不调用模型、不做 IO、
  不读墙钟、不读环境变量。
- 模型和用户文本**不能**自行修改策略或声明"已授权"。策略只能由代码
  （``PolicyConfig``）在构造时确定。
- 审批只决定"是否执行"，执行仍走 ToolRegistry → PipelineRunner → Remediation
  Agent，**不绕过**删行阈值、轮数限制、标签泄漏保护等内部安全门。
- 所有结构可 JSON 序列化（带 ``to_dict()``）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .models import RiskLevel


# ======================================================================
# PolicyAction
# ======================================================================


class PolicyAction(str, Enum):
    """权限决策动作。"""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# action 优先级：DENY > ASK > ALLOW（数字越大优先级越高）
_ACTION_PRIORITY: dict[PolicyAction, int] = {
    PolicyAction.DENY: 3,
    PolicyAction.ASK: 2,
    PolicyAction.ALLOW: 1,
}


# ======================================================================
# PolicyRule / PolicyConfig
# ======================================================================


@dataclass
class PolicyRule:
    """一条策略规则。

    - ``rule_id``: 规则唯一标识（用于审计 / PolicyDecision.rule_id）。
    - ``action``: 命中时的决策动作。
    - ``tool_names``: 适用的工具名列表；空列表表示"任意工具"。
    - ``risk_levels``: 适用的风险等级列表；空列表表示"任意风险等级"。
    - ``priority``: 优先级（按 action 自动赋值：DENY=3 / ASK=2 / ALLOW=1）。
    """

    rule_id: str
    action: PolicyAction
    tool_names: list[str] = field(default_factory=list)
    risk_levels: list[RiskLevel] = field(default_factory=list)
    priority: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.action, PolicyAction):
            raise TypeError(
                f"action must be a PolicyAction, got {type(self.action).__name__}"
            )
        # 未显式指定 priority 时按 action 自动赋值
        if self.priority == 0:
            self.priority = _ACTION_PRIORITY[self.action]

    def matches(self, tool_name: str, risk_level: RiskLevel) -> bool:
        """是否匹配给定工具名 + 风险等级。"""
        if self.tool_names and tool_name not in self.tool_names:
            return False
        if self.risk_levels and risk_level not in self.risk_levels:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "action": self.action.value,
            "tool_names": list(self.tool_names),
            "risk_levels": [r.value for r in self.risk_levels],
            "priority": self.priority,
        }


@dataclass
class PolicyConfig:
    """策略配置。

    - ``risk_defaults``: 按风险等级的默认动作（无 tool 级规则命中时使用）。
    - ``rules``: tool 级规则列表（按优先级 + 声明顺序求值）。
    - ``default_action``: 兜底动作（risk_defaults 也未命中时）。
    """

    risk_defaults: dict[RiskLevel, PolicyAction] = field(default_factory=dict)
    rules: list[PolicyRule] = field(default_factory=list)
    default_action: PolicyAction = PolicyAction.DENY

    @classmethod
    def default(cls) -> "PolicyConfig":
        """默认策略：read→ALLOW / workspace_write→ALLOW / guarded→ASK / 未知→DENY。"""
        return cls(
            risk_defaults={
                RiskLevel.READ: PolicyAction.ALLOW,
                RiskLevel.WORKSPACE_WRITE: PolicyAction.ALLOW,
                RiskLevel.GUARDED: PolicyAction.ASK,
            },
            rules=[],
            default_action=PolicyAction.DENY,
        )

    @classmethod
    def with_overrides(
        cls,
        overrides: dict[str, PolicyAction],
        *,
        base: "PolicyConfig | None" = None,
    ) -> "PolicyConfig":
        """便捷工厂：在默认策略基础上加 tool 级覆盖。

        ``overrides`` 形如 ``{"run_safe_remediation": PolicyAction.ALLOW}``，
        每个覆盖转成一条 ``PolicyRule``（rule_id 自动生成）。
        """
        base = base if base is not None else cls.default()
        rules = list(base.rules)
        for tool_name, action in overrides.items():
            rules.append(
                PolicyRule(
                    rule_id=f"override:{tool_name}",
                    action=action,
                    tool_names=[tool_name],
                )
            )
        return cls(
            risk_defaults=dict(base.risk_defaults),
            rules=rules,
            default_action=base.default_action,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_defaults": {
                r.value: a.value for r, a in self.risk_defaults.items()
            },
            "rules": [r.to_dict() for r in self.rules],
            "default_action": self.default_action.value,
        }


# ======================================================================
# PolicyDecision
# ======================================================================


@dataclass
class PolicyDecision:
    """一次权限决策结果。

    - ``action``: ALLOW / ASK / DENY。
    - ``reason``: 人类可读决策原因。
    - ``rule_id``: 命中的规则 id（``risk_default:<risk>`` / ``default`` / 规则 id）。
    - ``tool_name``: 被判断的工具名。
    - ``risk_level``: 工具的风险等级。
    """

    action: PolicyAction
    reason: str
    rule_id: str
    tool_name: str
    risk_level: RiskLevel

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "tool_name": self.tool_name,
            "risk_level": self.risk_level.value,
        }


# ======================================================================
# PendingApproval / ApprovalResponse
# ======================================================================


@dataclass
class PendingApproval:
    """待审批请求（工具执行前因 ASK 暂停时创建）。

    - ``request_id``: uuid4 生成的请求标识（审批绑定）。
    - ``call_id``: 原始 ToolCall.call_id。
    - ``tool_name``: 工具名。
    - ``arguments``: 原始参数（审批绑定，防篡改）。
    - ``fingerprint``: tool name + arguments + run_id 的规范化指纹（防篡改 / 防重放）。
    - ``run_id``: 当前 run_id（防跨 run 审批）。
    """

    request_id: str
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    fingerprint: str
    run_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "fingerprint": self.fingerprint,
            "run_id": self.run_id,
        }


@dataclass
class ApprovalResponse:
    """用户对 PendingApproval 的响应。

    - ``request_id``: 对应的 PendingApproval.request_id。
    - ``approved``: True=批准执行 / False=拒绝。
    - ``note``: 可选备注。
    """

    request_id: str
    approved: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "approved": self.approved,
            "note": self.note,
        }


# ======================================================================
# PolicyEngine
# ======================================================================


class PolicyEngine:
    """确定性权限引擎。

    用法::

        engine = PolicyEngine()  # 默认策略
        decision = engine.decide("run_safe_remediation", RiskLevel.GUARDED)
        if decision.action == PolicyAction.ASK:
            # 暂停，等待 ApprovalResponse
            ...

    决策顺序（严格按 spec 优先级）：
    1. tool 级规则（``rules`` 中 ``tool_names`` 非空且匹配的规则）：
       按 action 优先级 DENY > ASK > ALLOW 取**第一个**匹配返回。
    2. risk 默认策略（``risk_defaults``）。
    3. 兜底 ``default_action``（默认 DENY）。

    完全确定性：相同 (tool_name, risk_level) 永远得到相同决策；不调用模型、
    不做 IO、不读墙钟。
    """

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config if config is not None else PolicyConfig.default()

    def decide(
        self,
        tool_name: str,
        risk_level: RiskLevel,
        *,
        run_id: str | None = None,
    ) -> PolicyDecision:
        """对一次工具调用做权限判断。

        ``run_id`` 仅用于审计记录（写入 reason），不参与决策逻辑。
        """
        # 1. tool 级规则：收集所有匹配的 tool 级规则，按 action 优先级取第一个
        matched: list[PolicyRule] = []
        for rule in self.config.rules:
            if rule.tool_names and rule.matches(tool_name, risk_level):
                matched.append(rule)
        if matched:
            # 按 priority 降序、声明顺序稳定排序，取第一个
            matched.sort(key=lambda r: -r.priority)
            winner = matched[0]
            return PolicyDecision(
                action=winner.action,
                reason=(
                    f"tool-level rule '{winner.rule_id}' matched "
                    f"tool={tool_name} risk={risk_level.value}"
                ),
                rule_id=winner.rule_id,
                tool_name=tool_name,
                risk_level=risk_level,
            )

        # 2. risk 默认策略
        default_for_risk = self.config.risk_defaults.get(risk_level)
        if default_for_risk is not None:
            return PolicyDecision(
                action=default_for_risk,
                reason=(
                    f"risk default for {risk_level.value}: "
                    f"{default_for_risk.value}"
                ),
                rule_id=f"risk_default:{risk_level.value}",
                tool_name=tool_name,
                risk_level=risk_level,
            )

        # 3. 兜底
        return PolicyDecision(
            action=self.config.default_action,
            reason=(
                f"no rule or risk default matched; fallback to "
                f"default_action={self.config.default_action.value}"
            ),
            rule_id="default",
            tool_name=tool_name,
            risk_level=risk_level,
        )


# ======================================================================
# 指纹工具（供 Runtime 与 PolicyEngine 共用）
# ======================================================================


def make_fingerprint(tool_name: str, arguments: dict[str, Any], run_id: str) -> str:
    """生成 tool name + arguments + run_id 的规范化指纹串。

    用于 PendingApproval.fingerprint，审批恢复时校验参数未被篡改、
    且属于当前 run。
    """
    return json.dumps(
        {
            "name": tool_name,
            "arguments": arguments,
            "run_id": run_id,
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def new_request_id() -> str:
    """生成 uuid4 请求标识（不依赖墙钟，确定性不受影响）。"""
    return str(uuid.uuid4())
