"""ToolRegistry 单元测试（Stage 9 MVP）。

覆盖：
A1. 正常注册和查找。
A2. 重复工具名拒绝。
A3. 未知工具返回结构化错误。
A4. 缺少 required 参数。
A5. 参数类型错误。
A6. handler 异常转 ToolResult。
A7. schema 能导出给 ModelClient。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
for p in (str(SRC), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent_runtime.models import RiskLevel, ToolCall, ToolResult, ToolSpec  # noqa: E402
from agent_runtime.registry import (  # noqa: E402
    SchemaValidationError,
    ToolRegistry,
    build_registry,
    validate_arguments,
)


def _spec(
    name: str = "echo",
    schema: dict | None = None,
    handler=None,
    risk=RiskLevel.READ,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"test tool {name}",
        input_schema=schema or {"type": "object", "properties": {}, "required": []},
        risk_level=risk,
        handler=handler or (lambda args, ctx: ToolResult.success("ok")),
    )


class TestRegistryCore(unittest.TestCase):
    # A1. 正常注册和查找
    def test_register_and_get(self):
        reg = ToolRegistry()
        s = _spec("echo")
        reg.register(s)
        self.assertIs(reg.get("echo"), s)
        self.assertIsNone(reg.get("nope"))
        self.assertEqual(reg.names(), ["echo"])
        self.assertEqual(len(reg.list_specs()), 1)

    # A2. 重复工具名拒绝
    def test_duplicate_name_rejected(self):
        reg = ToolRegistry()
        reg.register(_spec("echo"))
        with self.assertRaises(ValueError):
            reg.register(_spec("echo"))

    # A3. 未知工具返回结构化错误
    def test_unknown_tool_returns_structured_error(self):
        reg = ToolRegistry()
        call = ToolCall(call_id="c1", name="nope", arguments={})
        result = reg.execute(call, context=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unknown_tool")
        self.assertEqual(result.error.code, "UNKNOWN_TOOL")
        self.assertFalse(result.error.retryable)

    # A6. handler 异常转 ToolResult
    def test_handler_exception_converts_to_toolresult(self):
        def boom(args, ctx):
            raise RuntimeError("kaboom")

        reg = ToolRegistry()
        reg.register(_spec("boom", handler=boom))
        call = ToolCall(call_id="c1", name="boom", arguments={})
        result = reg.execute(call, context=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_ERROR")
        # 不泄漏完整 traceback：message 只含类型与消息
        self.assertIn("RuntimeError", result.error.message)
        self.assertIn("kaboom", result.error.message)
        self.assertNotIn("Traceback", result.error.message)

    # A7. schema 能导出给 ModelClient
    def test_schemas_for_model(self):
        reg = build_registry(
            [
                _spec(
                    "add",
                    schema={
                        "type": "object",
                        "properties": {"x": {"type": "integer"}},
                        "required": ["x"],
                    },
                )
            ]
        )
        schemas = reg.schemas_for_model()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["name"], "add")
        self.assertIn("input_schema", schemas[0])
        self.assertEqual(schemas[0]["risk_level"], "read")
        # 不含 handler
        self.assertNotIn("handler", schemas[0])


class TestSchemaValidation(unittest.TestCase):
    # A4. 缺少 required 参数
    def test_missing_required_argument(self):
        reg = ToolRegistry()
        reg.register(
            _spec(
                "add",
                schema={
                    "type": "object",
                    "properties": {"x": {"type": "integer"}},
                    "required": ["x"],
                },
            )
        )
        call = ToolCall(call_id="c1", name="add", arguments={})
        result = reg.execute(call, context=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "invalid_arguments")
        self.assertEqual(result.error.code, "INVALID_TOOL_ARGUMENTS")
        self.assertTrue(result.error.retryable)

    # A5. 参数类型错误
    def test_wrong_argument_type(self):
        reg = ToolRegistry()
        reg.register(
            _spec(
                "add",
                schema={
                    "type": "object",
                    "properties": {"x": {"type": "integer"}},
                    "required": ["x"],
                },
            )
        )
        call = ToolCall(call_id="c1", name="add", arguments={"x": "not-int"})
        result = reg.execute(call, context=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "INVALID_TOOL_ARGUMENTS")

    def test_boolean_strict_not_int(self):
        # bool 是 int 子类，必须严格区分
        reg = ToolRegistry()
        reg.register(
            _spec(
                "set_flag",
                schema={
                    "type": "object",
                    "properties": {"flag": {"type": "boolean"}},
                    "required": ["flag"],
                },
            )
        )
        # 传 int 应失败
        r1 = reg.execute(
            ToolCall(call_id="c1", name="set_flag", arguments={"flag": 1}),
            context=None,
        )
        self.assertFalse(r1.ok)
        # 传 bool 应通过
        r2 = reg.execute(
            ToolCall(call_id="c2", name="set_flag", arguments={"flag": True}),
            context=None,
        )
        self.assertTrue(r2.ok)

    def test_enum_validation(self):
        reg = ToolRegistry()
        reg.register(
            _spec(
                "pick",
                schema={
                    "type": "object",
                    "properties": {"color": {"type": "string", "enum": ["red", "blue"]}},
                    "required": ["color"],
                },
            )
        )
        r_ok = reg.execute(
            ToolCall(call_id="c1", name="pick", arguments={"color": "red"}),
            context=None,
        )
        self.assertTrue(r_ok.ok)
        r_bad = reg.execute(
            ToolCall(call_id="c2", name="pick", arguments={"color": "green"}),
            context=None,
        )
        self.assertFalse(r_bad.ok)

    def test_array_items_validation(self):
        reg = ToolRegistry()
        reg.register(
            _spec(
                "sum_list",
                schema={
                    "type": "object",
                    "properties": {"vals": {"type": "array", "items": {"type": "integer"}}},
                    "required": ["vals"],
                },
            )
        )
        r_ok = reg.execute(
            ToolCall(call_id="c1", name="sum_list", arguments={"vals": [1, 2, 3]}),
            context=None,
        )
        self.assertTrue(r_ok.ok)
        r_bad = reg.execute(
            ToolCall(call_id="c2", name="sum_list", arguments={"vals": [1, "x"]}),
            context=None,
        )
        self.assertFalse(r_bad.ok)

    def test_validate_arguments_direct(self):
        # 直接测 validate_arguments
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
        validate_arguments(schema, {"n": 5})  # ok
        with self.assertRaises(SchemaValidationError):
            validate_arguments(schema, {"n": "x"})
        with self.assertRaises(SchemaValidationError):
            validate_arguments(schema, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
