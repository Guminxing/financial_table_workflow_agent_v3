"""ToolRegistry + 基础 schema 校验（Stage 9 MVP）。

ToolRegistry 负责：
- 注册 / 查找 / 列出 ToolSpec。
- 把 ToolSpec 导出为通用 JSON Schema 风格结构（给 ModelClient，不绑定某家 API）。
- 执行 ToolCall：按 input_schema 做基础校验 → 调 handler → 把异常转为结构化 ToolResult。

错误处理约定（绝不静默吞掉，也不把 traceback 泄漏给模型）：
- 未知工具 → ``ToolResult.failure(code="UNKNOWN_TOOL")``，不抛到 Runtime 顶层。
- 参数校验失败 → ``ToolResult.failure(code="INVALID_TOOL_ARGUMENTS", retryable=True)``。
- handler 抛异常 → ``ToolResult.failure(code="TOOL_EXECUTION_ERROR")``，
  message 只含异常类型与消息，不含完整 traceback（traceback 仅在 verbose 时打印）。
"""

from __future__ import annotations

import sys
import traceback
from typing import Any

from .models import RiskLevel, ToolCall, ToolResult, ToolSpec


# ======================================================================
# 基础 JSON Schema 校验器
# ======================================================================


class SchemaValidationError(ValueError):
    """schema 校验失败。携带 (path, message)。"""

    def __init__(self, path: str, message: str):
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    """按一个最小 JSON Schema 子集校验 arguments。

    支持的 schema 关键字（本轮只实现项目工具所需子集）：
    - ``type``: ``object`` / ``string`` / ``integer`` / ``number`` / ``boolean`` / ``array``
    - ``required``: list[str]（object 必填属性）
    - ``properties``: dict[str, schema]（object 属性子 schema）
    - ``enum``: list（值必须在枚举内）
    - ``items``: schema（array 元素子 schema）

    限制（在 docs/LLM_AGENT.md 中说明）：
    - 不支持 ``additionalProperties``、``pattern``、``minimum/maximum``、
      ``minItems/maxItems``、``oneOf/allOf/anyOf`` 等高级关键字。
    - ``integer`` 接受 int；``number`` 接受 int/float（不含 bool）。
    - ``boolean`` 严格接受 bool（Python 中 bool 是 int 子类，需显式排除）。
    """
    if not isinstance(schema, dict):
        raise SchemaValidationError("$", "schema must be a dict")
    _validate_node(schema, arguments, "$")


def _validate_node(schema: dict[str, Any], value: Any, path: str) -> None:
    if not isinstance(schema, dict):
        raise SchemaValidationError(path, "schema node must be a dict")

    # enum 优先（任何类型都可带 enum）
    if "enum" in schema:
        enum_vals = schema["enum"]
        if not isinstance(enum_vals, list):
            raise SchemaValidationError(path, "enum must be a list")
        if value not in enum_vals:
            raise SchemaValidationError(
                path, f"value {value!r} not in enum {enum_vals!r}"
            )

    node_type = schema.get("type")
    if node_type is None:
        # 无 type 只有 enum：enum 已校验，通过
        return

    if node_type == "object":
        if not isinstance(value, dict):
            raise SchemaValidationError(path, f"expected object, got {type(value).__name__}")
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise SchemaValidationError(path, "required must be a list")
        for key in required:
            if key not in value:
                raise SchemaValidationError(path, f"missing required property '{key}'")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise SchemaValidationError(path, "properties must be a dict")
        for key, sub_schema in properties.items():
            if key in value:
                _validate_node(sub_schema, value[key], f"{path}.{key}")
        return

    if node_type == "string":
        if not isinstance(value, str):
            raise SchemaValidationError(path, f"expected string, got {type(value).__name__}")
        return

    if node_type == "integer":
        # bool 是 int 子类，必须排除
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaValidationError(path, f"expected integer, got {type(value).__name__}")
        return

    if node_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaValidationError(path, f"expected number, got {type(value).__name__}")
        return

    if node_type == "boolean":
        if not isinstance(value, bool):
            raise SchemaValidationError(path, f"expected boolean, got {type(value).__name__}")
        return

    if node_type == "array":
        if not isinstance(value, list):
            raise SchemaValidationError(path, f"expected array, got {type(value).__name__}")
        items = schema.get("items")
        if items is not None:
            for i, item in enumerate(value):
                _validate_node(items, item, f"{path}[{i}]")
        return

    raise SchemaValidationError(path, f"unsupported type {node_type!r}")


# ======================================================================
# ToolRegistry
# ======================================================================


class ToolRegistry:
    """工具注册表。

    用法::

        registry = ToolRegistry()
        registry.register(ToolSpec(name="foo", ...))
        result = registry.execute(tool_call, context)
        schemas = registry.schemas_for_model()
    """

    def __init__(self, *, verbose: bool = False) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._verbose = verbose

    # ------------------------------------------------------------------
    # 注册 / 查找 / 列出
    # ------------------------------------------------------------------

    def register(self, spec: ToolSpec) -> None:
        """注册一个工具。重复工具名必须报错。"""
        if not isinstance(spec, ToolSpec):
            raise TypeError(f"spec must be a ToolSpec, got {type(spec).__name__}")
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        if not isinstance(spec.risk_level, RiskLevel):
            raise TypeError(
                f"spec.risk_level must be a RiskLevel, got {type(spec.risk_level).__name__}"
            )
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        """按名查找工具；未注册返回 None。"""
        return self._tools.get(name)

    def list_specs(self) -> list[ToolSpec]:
        """返回所有已注册工具（按注册顺序）。"""
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas_for_model(self) -> list[dict[str, Any]]:
        """导出给 ModelClient 的通用 schema 列表（不含 handler）。"""
        return [spec.to_schema_dict() for spec in self._tools.values()]

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    def execute(self, call: ToolCall, context: Any) -> ToolResult:
        """执行一次工具调用。

        - 未知工具 → 结构化 ToolResult（ok=False, code=UNKNOWN_TOOL）。
        - 参数校验失败 → 结构化 ToolResult（ok=False, code=INVALID_TOOL_ARGUMENTS, retryable=True）。
        - handler 异常 → 结构化 ToolResult（ok=False, code=TOOL_EXECUTION_ERROR）。
        - 不把完整 traceback 返回模型；verbose 时打印到 stderr。
        """
        spec = self._tools.get(call.name)
        if spec is None:
            return ToolResult.failure(
                f"unknown tool: {call.name}",
                code="UNKNOWN_TOOL",
                status="unknown_tool",
                retryable=False,
                next_actions=[],
            )

        # 基础 schema 校验
        try:
            validate_arguments(spec.input_schema, call.arguments)
        except SchemaValidationError as exc:
            return ToolResult.failure(
                f"invalid arguments for {call.name}: {exc.message} (at {exc.path})",
                code="INVALID_TOOL_ARGUMENTS",
                status="invalid_arguments",
                retryable=True,
                next_actions=[call.name],
            )

        # 执行 handler
        try:
            result = spec.handler(call.arguments, context)
        except Exception as exc:  # noqa: BLE001
            # 不把完整 traceback 返回模型；message 只含类型与消息
            if self._verbose:
                traceback.print_exc()
            return ToolResult.failure(
                f"{type(exc).__name__}: {exc}",
                code="TOOL_EXECUTION_ERROR",
                status="failed",
                retryable=False,
                next_actions=[],
            )

        # handler 必须返回 ToolResult
        if not isinstance(result, ToolResult):
            return ToolResult.failure(
                f"tool {call.name} handler returned non-ToolResult: {type(result).__name__}",
                code="TOOL_EXECUTION_ERROR",
                status="failed",
                retryable=False,
            )
        return result


def build_registry(specs: list[ToolSpec], *, verbose: bool = False) -> ToolRegistry:
    """便捷工厂：从 ToolSpec 列表构造 ToolRegistry。"""
    reg = ToolRegistry(verbose=verbose)
    for spec in specs:
        reg.register(spec)
    return reg
