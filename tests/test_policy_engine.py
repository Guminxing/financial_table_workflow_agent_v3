"""PolicyEngine 测试（Stage 10）。

覆盖：
1. read/write/guarded/unknown risk 的默认决策。
2. 工具级 allow/ask/deny 和优先级。
3. ASK/DENY 时 handler 未执行（在 test_runtime_approval 中验证）。
4. 确定性：相同输入多次 decide 结果一致。
5. 所有结构可 JSON 序列化。

PolicyEngine 完全确定性：不调用模型、不做 IO、不读墙钟。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
for p in (str(SRC), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent_runtime.models import RiskLevel  # noqa: E402
from agent_runtime.policy import (  # noqa: E402
    ApprovalResponse,
    PolicyAction,
    PolicyConfig,
    PolicyDecision,
    PolicyEngine,
    PolicyRule,
    PendingApproval,
    make_fingerprint,
    new_request_id,
)


class TestPolicyDefaults(unittest.TestCase):
    """1. read/write/guarded/unknown risk 的默认决策。"""

    def setUp(self):
        self.engine = PolicyEngine()  # 默认策略

    def test_read_default_allow(self):
        d = self.engine.decide("inspect_pipeline_status", RiskLevel.READ)
        self.assertEqual(d.action, PolicyAction.ALLOW)
        self.assertEqual(d.rule_id, "risk_default:read")

    def test_workspace_write_default_allow(self):
        d = self.engine.decide("profile_financial_data", RiskLevel.WORKSPACE_WRITE)
        self.assertEqual(d.action, PolicyAction.ALLOW)
        self.assertEqual(d.rule_id, "risk_default:workspace_write")

    def test_guarded_default_ask(self):
        d = self.engine.decide("run_safe_remediation", RiskLevel.GUARDED)
        self.assertEqual(d.action, PolicyAction.ASK)
        self.assertEqual(d.rule_id, "risk_default:guarded")

    def test_unknown_risk_default_deny(self):
        # 构造一个不在 risk_defaults 中的"风险等级"：用一个未注册的 RiskLevel 值
        # RiskLevel 是 str Enum，risk_defaults 只含 READ/WORKSPACE_WRITE/GUARDED，
        # 任何其它值都走 default_action=DENY。
        # 这里用一个伪造的 RiskLike（str 子类）模拟"未知 risk"。
        class UnknownRisk(str):
            value = "unknown_risk"

        # 直接用一个不在 risk_defaults 的 RiskLevel 成员不可能（Enum 闭合），
        # 所以用 PolicyConfig.default() 但 risk_defaults 清空来验证兜底 DENY。
        engine = PolicyEngine(
            PolicyConfig(risk_defaults={}, rules=[], default_action=PolicyAction.DENY)
        )
        d = engine.decide("any_tool", RiskLevel.GUARDED)
        self.assertEqual(d.action, PolicyAction.DENY)
        self.assertEqual(d.rule_id, "default")


class TestPolicyRulesAndPriority(unittest.TestCase):
    """2. 工具级 allow/ask/deny 和优先级。"""

    def test_tool_level_allow_overrides_risk_default(self):
        # 默认 guarded→ASK，但工具级 ALLOW 应胜
        cfg = PolicyConfig.with_overrides({"run_safe_remediation": PolicyAction.ALLOW})
        engine = PolicyEngine(cfg)
        d = engine.decide("run_safe_remediation", RiskLevel.GUARDED)
        self.assertEqual(d.action, PolicyAction.ALLOW)
        self.assertEqual(d.rule_id, "override:run_safe_remediation")

    def test_tool_level_deny_overrides_risk_default(self):
        # 默认 read→ALLOW，但工具级 DENY 应胜
        cfg = PolicyConfig.with_overrides({"inspect_pipeline_status": PolicyAction.DENY})
        engine = PolicyEngine(cfg)
        d = engine.decide("inspect_pipeline_status", RiskLevel.READ)
        self.assertEqual(d.action, PolicyAction.DENY)

    def test_tool_level_ask_overrides_allow(self):
        # 默认 workspace_write→ALLOW，但工具级 ASK 应胜
        cfg = PolicyConfig.with_overrides({"profile_financial_data": PolicyAction.ASK})
        engine = PolicyEngine(cfg)
        d = engine.decide("profile_financial_data", RiskLevel.WORKSPACE_WRITE)
        self.assertEqual(d.action, PolicyAction.ASK)

    def test_priority_deny_beats_ask_beats_allow(self):
        # 同一工具同时有 ALLOW / ASK / DENY 三条规则 → DENY 胜
        cfg = PolicyConfig(
            risk_defaults={RiskLevel.READ: PolicyAction.ALLOW},
            rules=[
                PolicyRule(rule_id="r_allow", action=PolicyAction.ALLOW, tool_names=["t"]),
                PolicyRule(rule_id="r_ask", action=PolicyAction.ASK, tool_names=["t"]),
                PolicyRule(rule_id="r_deny", action=PolicyAction.DENY, tool_names=["t"]),
            ],
            default_action=PolicyAction.DENY,
        )
        engine = PolicyEngine(cfg)
        d = engine.decide("t", RiskLevel.READ)
        self.assertEqual(d.action, PolicyAction.DENY)
        self.assertEqual(d.rule_id, "r_deny")

    def test_priority_ask_beats_allow(self):
        cfg = PolicyConfig(
            risk_defaults={RiskLevel.READ: PolicyAction.ALLOW},
            rules=[
                PolicyRule(rule_id="r_allow", action=PolicyAction.ALLOW, tool_names=["t"]),
                PolicyRule(rule_id="r_ask", action=PolicyAction.ASK, tool_names=["t"]),
            ],
            default_action=PolicyAction.DENY,
        )
        engine = PolicyEngine(cfg)
        d = engine.decide("t", RiskLevel.READ)
        self.assertEqual(d.action, PolicyAction.ASK)
        self.assertEqual(d.rule_id, "r_ask")

    def test_tool_rule_does_not_match_other_tools(self):
        # 工具级规则只对指定工具生效，不影响其它工具
        cfg = PolicyConfig.with_overrides({"t_deny": PolicyAction.DENY})
        engine = PolicyEngine(cfg)
        d = engine.decide("t_other", RiskLevel.READ)
        # t_other 不匹配 t_deny 规则 → 走 risk_default:read → ALLOW
        self.assertEqual(d.action, PolicyAction.ALLOW)
        self.assertEqual(d.rule_id, "risk_default:read")

    def test_rule_risk_level_filter(self):
        # 规则限定 risk_levels：只对 guarded 生效，read 不匹配
        cfg = PolicyConfig(
            risk_defaults={
                RiskLevel.READ: PolicyAction.ALLOW,
                RiskLevel.GUARDED: PolicyAction.ASK,
            },
            rules=[
                PolicyRule(
                    rule_id="guarded_only_allow",
                    action=PolicyAction.ALLOW,
                    tool_names=["t"],
                    risk_levels=[RiskLevel.GUARDED],
                ),
            ],
            default_action=PolicyAction.DENY,
        )
        engine = PolicyEngine(cfg)
        # guarded → 规则匹配 → ALLOW
        self.assertEqual(
            engine.decide("t", RiskLevel.GUARDED).action, PolicyAction.ALLOW
        )
        # read → 规则不匹配（risk_levels 限定 guarded）→ risk_default:read → ALLOW
        self.assertEqual(
            engine.decide("t", RiskLevel.READ).action, PolicyAction.ALLOW
        )
        self.assertEqual(
            engine.decide("t", RiskLevel.READ).rule_id, "risk_default:read"
        )


class TestDeterminism(unittest.TestCase):
    """4. 确定性：相同输入多次 decide 结果一致。"""

    def test_same_input_same_decision(self):
        engine = PolicyEngine()
        d1 = engine.decide("run_safe_remediation", RiskLevel.GUARDED)
        d2 = engine.decide("run_safe_remediation", RiskLevel.GUARDED)
        d3 = engine.decide("run_safe_remediation", RiskLevel.GUARDED)
        self.assertEqual(d1.action, d2.action)
        self.assertEqual(d2.action, d3.action)
        self.assertEqual(d1.rule_id, d2.rule_id)
        self.assertEqual(d1.reason, d2.reason)

    def test_decision_is_pure(self):
        # decide 不修改 config 状态
        engine = PolicyEngine()
        before = json.dumps(engine.config.to_dict(), sort_keys=True)
        engine.decide("a", RiskLevel.READ)
        engine.decide("b", RiskLevel.GUARDED)
        after = json.dumps(engine.config.to_dict(), sort_keys=True)
        self.assertEqual(before, after)


class TestSerialization(unittest.TestCase):
    """5. 所有结构可 JSON 序列化。"""

    def test_all_to_dict_json_serializable(self):
        engine = PolicyEngine()
        d = engine.decide("run_safe_remediation", RiskLevel.GUARDED)
        # PolicyDecision
        json.dumps(d.to_dict())
        # PolicyConfig
        json.dumps(engine.config.to_dict())
        # PolicyRule
        rule = PolicyRule(rule_id="r", action=PolicyAction.ASK, tool_names=["t"])
        json.dumps(rule.to_dict())
        # PendingApproval
        pa = PendingApproval(
            request_id=new_request_id(),
            call_id="c1",
            tool_name="t",
            arguments={"x": 1},
            fingerprint=make_fingerprint("t", {"x": 1}, "run_1"),
            run_id="run_1",
        )
        json.dumps(pa.to_dict())
        # ApprovalResponse
        json.dumps(ApprovalResponse(request_id=pa.request_id, approved=True).to_dict())

    def test_fingerprint_stable_and_run_scoped(self):
        # 相同 (name, args, run_id) → 相同指纹
        fp1 = make_fingerprint("t", {"a": 1, "b": 2}, "run_1")
        fp2 = make_fingerprint("t", {"b": 2, "a": 1}, "run_1")  # 顺序不同
        self.assertEqual(fp1, fp2)
        # 不同 run_id → 不同指纹
        fp3 = make_fingerprint("t", {"a": 1, "b": 2}, "run_2")
        self.assertNotEqual(fp1, fp3)
        # 不同 args → 不同指纹
        fp4 = make_fingerprint("t", {"a": 1, "b": 3}, "run_1")
        self.assertNotEqual(fp1, fp4)

    def test_request_id_unique(self):
        ids = {new_request_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
