"""ModelClient Protocol（Stage 9 MVP）。

定义抽象模型接口，**不**依赖任何具体 SDK（OpenAI / Anthropic / Gemini / Ollama）。

- 生产代码只定义 Protocol。
- FakeModel 放在测试代码中（见 tests/test_agent_runtime.py 的 ScriptedFakeModel）。
- 不读取环境变量中的 API Key。
- Runtime 不应知道具体模型供应商。

本轮不接入真实 LLM；该 Protocol 由测试中的 Fake Model 驱动验证。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import AssistantTurn


@runtime_checkable
class ModelClient(Protocol):
    """抽象模型客户端协议。

    实现者负责把 ``messages`` + ``tools`` 喂给某个模型，返回一轮
    :class:`AssistantTurn`（要么 final_text，要么 tool_calls）。

    约定：
    - ``messages`` 是 OpenAI 风格的 ``{"role": ..., "content": ...}`` 列表，
      但本协议不绑定某家 API；Runtime 只要求它是 list[dict]。
    - ``tools`` 是 ToolRegistry.schemas_for_model() 的输出（通用 JSON Schema 风格）。
    - 返回的 AssistantTurn 必须通过 ``is_valid()`` 校验（final_text 与 tool_calls
      恰一非空），否则 Runtime 以 ``model_protocol_error`` 停止。
    """

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        """返回模型一轮输出。"""
        ...
