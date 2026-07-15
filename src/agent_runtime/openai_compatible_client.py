"""OpenAI-compatible ModelClient 适配器（Stage 11）。

把通用 ``ToolSpec`` / Runtime messages 与 OpenAI-compatible Chat Completions
tool-calling 协议互转，让真实 LLM 驱动 Stage 9–10 的 AgentRuntime。

配置来源（**只**从环境变量读取，CLI 可在构造时覆盖 model/base_url）：

- ``FTA_LLM_API_KEY``  —— API Key，**只**放进 HTTP ``Authorization`` 头，
  绝不写入日志 / 事件 / 错误信息。
- ``FTA_LLM_BASE_URL`` —— OpenAI-compatible base URL（如 ``https://api.openai.com/v1``）。
- ``FTA_LLM_MODEL``   —— 模型名。

转换职责（全部为模块级纯函数，便于单测）：

- :func:`tool_spec_to_provider`  通用 ToolSpec schema → provider function tool schema
- :func:`messages_to_provider`     Runtime messages → provider messages
- :func:`response_to_turn`         provider 响应 → :class:`AssistantTurn`

错误处理：
- timeout / HTTP error / 空 choices / 非法 JSON / 非法响应结构 → 抛
  :class:`ModelRequestError` / :class:`ModelResponseError`。
- 错误信息**绝不**包含 API Key（``_scrub`` 兜底替换）。
- 使用可注入 ``requests.Session``，测试全部 mock，不访问网络。
- 非必要不新增依赖（仅用已有的 ``requests``）。
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

from .model_client import ModelClient  # noqa: F401  (Protocol, 供 isinstance/类型提示)
from .models import AssistantTurn, ToolCall


# ======================================================================
# 异常
# ======================================================================


class ModelError(RuntimeError):
    """模型客户端错误基类。错误信息绝不包含 API Key。"""


class ModelConfigError(ModelError, ValueError):
    """缺少 api_key / base_url / model 配置。"""


class ModelRequestError(ModelError):
    """调用模型的网络 / HTTP 错误。"""


class ModelResponseError(ModelError):
    """模型响应结构非法（空 choices / 非法 JSON / 非法 tool_call）。"""


# ======================================================================
# 纯转换函数
# ======================================================================


def tool_spec_to_provider(spec: dict[str, Any]) -> dict[str, Any]:
    """通用 ToolSpec schema（``schemas_for_model()`` 输出）→ provider function tool schema。

    通用 schema 形如 ``{"name", "description", "input_schema", "risk_level"}``；
    provider 形如 ``{"type": "function", "function": {"name", "description",
    "parameters"}}``。``risk_level`` 不发给 provider（仅 Runtime/Policy 内部用）。
    """
    if not isinstance(spec, dict):
        raise ModelResponseError("tool spec is not an object")
    parameters = spec.get("input_schema")
    if not isinstance(parameters, dict):
        # 兜底：缺 schema 时给一个空 object，避免 provider 报错
        parameters = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": spec.get("name"),
            "description": spec.get("description", ""),
            "parameters": parameters,
        },
    }


def messages_to_provider(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runtime messages → provider messages。

    Runtime 的消息形态：

    - ``{"role": "user", "content": str}``
    - ``{"role": "assistant", "content": str}``  （final_text）
    - ``{"role": "assistant", "content": None,
        "tool_calls": [{"call_id", "name", "arguments": dict}]}``
    - ``{"role": "tool", "tool_call_id": str, "name": str, "content": str}``

    provider 形态：

    - assistant tool_calls：``{"id", "type": "function",
      "function": {"name", "arguments": <JSON string>}}``
    - tool 结果：``{"role": "tool", "tool_call_id", "content"}``（去掉 name）
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            raise ModelResponseError("message is not an object")
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                if not isinstance(c, dict):
                    raise ModelResponseError("tool_call entry is not an object")
                calls.append(
                    {
                        "id": c.get("call_id", ""),
                        "type": "function",
                        "function": {
                            "name": c.get("name", ""),
                            "arguments": json.dumps(
                                c.get("arguments", {}),
                                ensure_ascii=False,
                            ),
                        },
                    }
                )
            out.append(
                {
                    "role": "assistant",
                    "content": m.get("content"),
                    "tool_calls": calls,
                }
            )
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }
            )
        else:
            # user / system / 纯文本 assistant：原样透传
            out.append({"role": role, "content": m.get("content", "")})
    return out


def _parse_provider_tool_call(tc: Any) -> ToolCall:
    """解析 provider 返回的单个 tool_call → :class:`ToolCall`。

    ``function.arguments`` 必须解析为 JSON object：
    - dict → 直接用；
    - 字符串 → ``json.loads``，必须是 dict，否则 ModelResponseError；
    - None / 空串 → ``{}``；
    - 其他类型 → ModelResponseError。
    """
    if not isinstance(tc, dict):
        raise ModelResponseError("tool_call is not an object")
    call_id = tc.get("id")
    call_id = "" if call_id is None else str(call_id)
    fn = tc.get("function")
    if not isinstance(fn, dict):
        raise ModelResponseError("tool_call.function is not an object")
    name = fn.get("name")
    if not isinstance(name, str) or not name:
        raise ModelResponseError("tool_call.function.name is missing")

    args_raw = fn.get("arguments")
    if args_raw is None:
        arguments: dict[str, Any] = {}
    elif isinstance(args_raw, dict):
        arguments = args_raw
    elif isinstance(args_raw, str):
        s = args_raw.strip()
        if not s:
            arguments = {}
        else:
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                raise ModelResponseError(
                    "tool_call.function.arguments is not valid JSON"
                )
            if not isinstance(parsed, dict):
                raise ModelResponseError(
                    "tool_call.function.arguments is not a JSON object"
                )
            arguments = parsed
    else:
        raise ModelResponseError(
            "tool_call.function.arguments has invalid type "
            f"({type(args_raw).__name__})"
        )
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


def response_to_turn(data: Any) -> AssistantTurn:
    """provider 响应 JSON → :class:`AssistantTurn`。

    - 有 ``tool_calls`` → ``AssistantTurn(tool_calls=[...])``（丢弃伴随的 content，
      避免 AssistantTurn 的 XOR 校验失败；中间叙述不回传，符合"不记录隐藏推理"）。
    - 无 ``tool_calls`` 且 content 非空 → ``AssistantTurn(final_text=content)``。
    - 两者皆空 → ``AssistantTurn()``（非法，Runtime 以 model_protocol_error 停止）。
    """
    if not isinstance(data, dict):
        raise ModelResponseError("response is not a JSON object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelResponseError("response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ModelResponseError("response choice is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ModelResponseError("response message is not an object")

    raw_calls = message.get("tool_calls")
    parsed_calls: list[ToolCall] = []
    if raw_calls:
        if not isinstance(raw_calls, list):
            raise ModelResponseError("tool_calls is not a list")
        for tc in raw_calls:
            parsed_calls.append(_parse_provider_tool_call(tc))

    if parsed_calls:
        return AssistantTurn(tool_calls=parsed_calls)

    content = message.get("content")
    if content is not None and str(content).strip():
        return AssistantTurn(final_text=str(content))

    # 两者皆空：返回非法 turn，Runtime 以 model_protocol_error 停止
    return AssistantTurn()


# ======================================================================
# OpenAICompatibleModelClient
# ======================================================================


class OpenAICompatibleModelClient:
    """OpenAI-compatible Chat Completions 模型客户端。

    实现 :class:`agent_runtime.model_client.ModelClient` Protocol（结构化）。

    用法::

        client = OpenAICompatibleModelClient(
            system_prompt=open("prompts/financial_agent_system.md").read(),
        )
        runtime = AgentRuntime(model=client, registry=..., context=..., policy=...)

    构造参数（均可选，缺省读环境变量）：

    - ``api_key`` / ``base_url`` / ``model``：覆盖 ``FTA_LLM_*`` 环境变量。
    - ``session``：可注入 ``requests.Session``（测试 mock，不访问网络）。
    - ``timeout``：HTTP 超时秒数。
    - ``system_prompt``：system prompt 文本（由 CLI 读取文件后注入）。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 60.0,
        system_prompt: str | None = None,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.environ.get("FTA_LLM_API_KEY")
        )
        self._base_url = (
            base_url if base_url is not None else os.environ.get("FTA_LLM_BASE_URL")
        )
        self._model = model if model is not None else os.environ.get("FTA_LLM_MODEL")
        self._session = session if session is not None else requests.Session()
        self._timeout = float(timeout)
        self._system_prompt = system_prompt

        missing: list[str] = []
        if not self._api_key:
            missing.append("FTA_LLM_API_KEY")
        if not self._base_url:
            missing.append("FTA_LLM_BASE_URL")
        if not self._model:
            missing.append("FTA_LLM_MODEL")
        if missing:
            raise ModelConfigError(
                "OpenAICompatibleModelClient is not configured; missing "
                "environment variable(s): " + ", ".join(missing)
            )

    # ------------------------------------------------------------------
    # ModelClient Protocol
    # ------------------------------------------------------------------

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        """调用模型并返回一轮 :class:`AssistantTurn`。"""
        provider_messages: list[dict[str, Any]] = []
        if self._system_prompt:
            provider_messages.append(
                {"role": "system", "content": self._system_prompt}
            )
        provider_messages.extend(messages_to_provider(messages))
        provider_tools = [tool_spec_to_provider(t) for t in tools]

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": provider_messages,
        }
        if provider_tools:
            payload["tools"] = provider_tools

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = self._chat_url()

        try:
            resp = self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise ModelRequestError(
                self._scrub(f"model request timed out after {self._timeout}s")
            ) from exc
        except requests.exceptions.RequestException as exc:
            # 只暴露异常类型，不暴露可能含 URL 的完整异常串
            raise ModelRequestError(
                self._scrub(f"model request failed: {type(exc).__name__}")
            ) from exc

        if resp.status_code != 200:
            raise ModelRequestError(
                self._scrub(
                    f"model returned HTTP {resp.status_code}: "
                    f"{self._safe_body(resp)}"
                )
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise ModelRequestError(
                self._scrub("model response is not valid JSON")
            ) from exc

        try:
            return response_to_turn(data)
        except ModelResponseError as exc:
            raise ModelResponseError(self._scrub(str(exc))) from exc

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _chat_url(self) -> str:
        base = (self._base_url or "").rstrip("/")
        if not base:
            return ""
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _safe_body(self, resp: Any) -> str:
        """取响应正文（截断），用于错误信息。正文来自服务端，不含 API Key。"""
        try:
            text = resp.text
        except Exception:  # noqa: BLE001
            return ""
        if text is None:
            return ""
        if len(text) > 500:
            return text[:500] + "...(truncated)"
        return text

    def _scrub(self, text: str) -> str:
        """兜底：若错误信息意外包含 API Key，替换为 ``***``。"""
        if self._api_key and self._api_key in text:
            return text.replace(self._api_key, "***")
        return text


# 让 isinstance(client, ModelClient) 在结构化 Protocol 下成立（已具备 complete 方法）。
__all__ = [
    "ModelError",
    "ModelConfigError",
    "ModelRequestError",
    "ModelResponseError",
    "OpenAICompatibleModelClient",
    "tool_spec_to_provider",
    "messages_to_provider",
    "response_to_turn",
]
