"""OpenAICompatibleModelClient 适配器测试（Stage 11）。

覆盖：
1. ToolSpec 转 provider schema。
2. Runtime messages 转 provider messages。
3. 解析 final text。
4. 解析单个和多个 tool_calls。
5. 非法 arguments JSON。
6. 空 choices 和错误响应结构。
7. timeout / HTTP error。
8. 错误信息不包含 API Key。
9. 缺少 api_key / base_url / model 时明确错误。
10. 测试不访问真实网络（全部 mock requests.Session）。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
for p in (str(SRC), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent_runtime.models import AssistantTurn  # noqa: E402
from agent_runtime.openai_compatible_client import (  # noqa: E402
    ModelConfigError,
    ModelRequestError,
    ModelResponseError,
    OpenAICompatibleModelClient,
    messages_to_provider,
    response_to_turn,
    tool_spec_to_provider,
)


# ======================================================================
# 假 requests.Session：记录调用、返回预设响应
# ======================================================================


class FakeResponse:
    def __init__(self, status_code: int, payload: object, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else (
            json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
        )

    def json(self):
        if isinstance(self._payload, (dict, list)):
            return self._payload
        raise ValueError("not json")


class FakeSession:
    """记录 post() 调用并按队列返回 FakeResponse / 抛异常。"""

    def __init__(self, responses=None, raise_exc=None):
        # responses: list[FakeResponse]；raise_exc: list[Exception]（与 post 调用一一对应）
        self._responses = list(responses or [])
        self._raise = list(raise_exc or [])
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        if self._raise:
            raise self._raise.pop(0)
        if not self._responses:
            raise AssertionError("FakeSession: no more responses queued")
        return self._responses.pop(0)


def _make_client(
    *,
    api_key="sk-test-secret-key-12345",
    base_url="https://api.example.com/v1",
    model="test-model",
    session=None,
    system_prompt="you are a test agent",
):
    return OpenAICompatibleModelClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        session=session if session is not None else FakeSession(),
        system_prompt=system_prompt,
    )


def _ok_response(message: dict) -> FakeResponse:
    return FakeResponse(200, {"choices": [{"message": message}]})


# ======================================================================
# 1. ToolSpec 转 provider schema
# ======================================================================


class TestToolSpecToProvider(unittest.TestCase):
    def test_basic_conversion(self):
        spec = {
            "name": "profile_financial_data",
            "description": "Stage 1: profile CSVs",
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": [],
            },
            "risk_level": "workspace_write",
        }
        out = tool_spec_to_provider(spec)
        self.assertEqual(out["type"], "function")
        self.assertEqual(out["function"]["name"], "profile_financial_data")
        self.assertEqual(out["function"]["description"], "Stage 1: profile CSVs")
        # parameters 即 input_schema
        self.assertEqual(
            out["function"]["parameters"]["properties"]["x"]["type"], "string"
        )
        # risk_level 不发给 provider
        self.assertNotIn("risk_level", out)
        self.assertNotIn("risk_level", out["function"])

    def test_missing_input_schema_defaults_empty_object(self):
        out = tool_spec_to_provider({"name": "t", "description": "d"})
        self.assertEqual(out["function"]["parameters"], {"type": "object", "properties": {}})


# ======================================================================
# 2. Runtime messages 转 provider messages
# ======================================================================


class TestMessagesToProvider(unittest.TestCase):
    def test_user_and_text_assistant_passthrough(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        out = messages_to_provider(msgs)
        self.assertEqual(out[0], {"role": "user", "content": "hi"})
        self.assertEqual(out[1], {"role": "assistant", "content": "hello"})

    def test_assistant_tool_calls_and_tool_result(self):
        msgs = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"call_id": "c1", "name": "echo", "arguments": {"msg": "hi"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "echo",
                "content": '{"ok": true}',
            },
        ]
        out = messages_to_provider(msgs)
        # assistant tool_calls: arguments 序列化为 JSON 字符串
        self.assertEqual(out[0]["role"], "assistant")
        self.assertEqual(out[0]["tool_calls"][0]["id"], "c1")
        self.assertEqual(out[0]["tool_calls"][0]["type"], "function")
        self.assertEqual(out[0]["tool_calls"][0]["function"]["name"], "echo")
        self.assertEqual(
            out[0]["tool_calls"][0]["function"]["arguments"],
            json.dumps({"msg": "hi"}, ensure_ascii=False),
        )
        # tool 结果：去掉 name，保留 tool_call_id + content
        self.assertEqual(out[1]["role"], "tool")
        self.assertEqual(out[1]["tool_call_id"], "c1")
        self.assertEqual(out[1]["content"], '{"ok": true}')
        self.assertNotIn("name", out[1])


# ======================================================================
# 3. 解析 final text
# ======================================================================


class TestResponseToTurnFinalText(unittest.TestCase):
    def test_final_text(self):
        turn = response_to_turn(
            {"choices": [{"message": {"content": "All done."}}]}
        )
        self.assertEqual(turn.final_text, "All done.")
        self.assertEqual(turn.tool_calls, [])
        self.assertTrue(turn.is_valid())

    def test_empty_content_is_invalid_turn(self):
        turn = response_to_turn(
            {"choices": [{"message": {"content": ""}}]}
        )
        self.assertIsNone(turn.final_text)
        self.assertEqual(turn.tool_calls, [])
        self.assertFalse(turn.is_valid())


# ======================================================================
# 4. 解析单个和多个 tool_calls
# ======================================================================


class TestResponseToTurnToolCalls(unittest.TestCase):
    def test_single_tool_call(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "profile_financial_data",
                                    "arguments": '{"a": 1}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        turn = response_to_turn(data)
        self.assertEqual(len(turn.tool_calls), 1)
        c = turn.tool_calls[0]
        self.assertEqual(c.call_id, "call_1")
        self.assertEqual(c.name, "profile_financial_data")
        self.assertEqual(c.arguments, {"a": 1})
        self.assertTrue(turn.is_valid())

    def test_multiple_tool_calls(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "echo", "arguments": "{}"},
                            },
                            {
                                "id": "c2",
                                "type": "function",
                                "function": {
                                    "name": "boom",
                                    "arguments": '{"x": 2}',
                                },
                            },
                        ],
                    }
                }
            ]
        }
        turn = response_to_turn(data)
        self.assertEqual(len(turn.tool_calls), 2)
        self.assertEqual(turn.tool_calls[0].call_id, "c1")
        self.assertEqual(turn.tool_calls[1].arguments, {"x": 2})

    def test_arguments_as_dict_directly(self):
        data = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {"name": "t", "arguments": {"k": "v"}},
                            }
                        ]
                    }
                }
            ]
        }
        turn = response_to_turn(data)
        self.assertEqual(turn.tool_calls[0].arguments, {"k": "v"})

    def test_arguments_none_or_empty_string(self):
        for raw in (None, "", "   "):
            data = {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {"name": "t", "arguments": raw},
                                }
                            ]
                        }
                    }
                ]
            }
            turn = response_to_turn(data)
            self.assertEqual(turn.tool_calls[0].arguments, {}, f"raw={raw!r}")


# ======================================================================
# 5. 非法 arguments JSON
# ======================================================================


class TestInvalidArguments(unittest.TestCase):
    def test_invalid_json_arguments(self):
        data = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "t",
                                    "arguments": "{not json",
                                },
                            }
                        ]
                    }
                }
            ]
        }
        with self.assertRaises(ModelResponseError):
            response_to_turn(data)

    def test_arguments_not_object(self):
        data = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {"name": "t", "arguments": "[1,2,3]"},
                            }
                        ]
                    }
                }
            ]
        }
        with self.assertRaises(ModelResponseError):
            response_to_turn(data)

    def test_missing_function_name(self):
        data = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c1", "function": {"arguments": "{}"}}
                        ]
                    }
                }
            ]
        }
        with self.assertRaises(ModelResponseError):
            response_to_turn(data)


# ======================================================================
# 6. 空 choices 和错误响应结构
# ======================================================================


class TestBadResponseStructure(unittest.TestCase):
    def test_empty_choices(self):
        with self.assertRaises(ModelResponseError):
            response_to_turn({"choices": []})

    def test_missing_choices(self):
        with self.assertRaises(ModelResponseError):
            response_to_turn({})

    def test_response_not_object(self):
        with self.assertRaises(ModelResponseError):
            response_to_turn("not a dict")

    def test_choice_not_object(self):
        with self.assertRaises(ModelResponseError):
            response_to_turn({"choices": ["x"]})

    def test_message_not_object(self):
        with self.assertRaises(ModelResponseError):
            response_to_turn({"choices": [{"message": "x"}]})


# ======================================================================
# 7. timeout / HTTP error
# ======================================================================


class TestRequestErrors(unittest.TestCase):
    def test_timeout(self):
        import requests

        sess = FakeSession(raise_exc=[requests.exceptions.Timeout("timed out")])
        client = _make_client(session=sess)
        with self.assertRaises(ModelRequestError):
            client.complete([{"role": "user", "content": "hi"}], [])

    def test_http_error_status(self):
        sess = FakeSession(responses=[FakeResponse(500, {"error": "boom"})])
        client = _make_client(session=sess)
        with self.assertRaises(ModelRequestError):
            client.complete([{"role": "user", "content": "hi"}], [])

    def test_non_json_body(self):
        sess = FakeSession(responses=[FakeResponse(200, "not json", text="not json")])
        client = _make_client(session=sess)
        with self.assertRaises(ModelRequestError):
            client.complete([{"role": "user", "content": "hi"}], [])

    def test_request_exception_generic(self):
        import requests

        sess = FakeSession(
            raise_exc=[requests.exceptions.ConnectionError("conn refused")]
        )
        client = _make_client(session=sess)
        with self.assertRaises(ModelRequestError):
            client.complete([{"role": "user", "content": "hi"}], [])


# ======================================================================
# 8. 错误信息不包含 API Key
# ======================================================================


class TestNoKeyLeak(unittest.TestCase):
    SECRET = "sk-test-secret-key-12345"

    def test_http_error_message_excludes_key(self):
        # 服务端返回体里"意外"包含 key（模拟泄漏），错误信息必须 scrub
        sess = FakeSession(
            responses=[
                FakeResponse(
                    500,
                    {"error": "bad key " + self.SECRET},
                    text="bad key " + self.SECRET,
                )
            ]
        )
        client = _make_client(api_key=self.SECRET, session=sess)
        try:
            client.complete([{"role": "user", "content": "hi"}], [])
        except ModelRequestError as exc:
            self.assertNotIn(self.SECRET, str(exc))
        else:
            self.fail("expected ModelRequestError")

    def test_request_url_uses_base_url_not_key(self):
        sess = FakeSession(responses=[_ok_response({"content": "ok"})])
        client = _make_client(session=sess)
        client.complete([{"role": "user", "content": "hi"}], [])
        call = sess.calls[0]
        self.assertEqual(call["url"], "https://api.example.com/v1/chat/completions")
        # Authorization 头含 key（正常），但 url 不含 key
        self.assertNotIn(self.SECRET, call["url"])
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + self.SECRET)

    def test_post_payload_has_no_key(self):
        sess = FakeSession(responses=[_ok_response({"content": "ok"})])
        client = _make_client(session=sess)
        client.complete([{"role": "user", "content": "hi"}], [])
        payload = sess.calls[0]["json"]
        # payload 只含 model / messages / tools，不含 api_key
        self.assertNotIn("api_key", json.dumps(payload))
        self.assertNotIn(self.SECRET, json.dumps(payload))


# ======================================================================
# 9. 缺少配置时明确错误
# ======================================================================


class TestConfigErrors(unittest.TestCase):
    def setUp(self):
        # 清掉环境变量，确保走显式参数
        self._saved = {
            k: os.environ.get(k)
            for k in ("FTA_LLM_API_KEY", "FTA_LLM_BASE_URL", "FTA_LLM_MODEL")
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_missing_all(self):
        with self.assertRaises(ModelConfigError) as cm:
            OpenAICompatibleModelClient()
        msg = str(cm.exception)
        self.assertIn("FTA_LLM_API_KEY", msg)
        self.assertIn("FTA_LLM_BASE_URL", msg)
        self.assertIn("FTA_LLM_MODEL", msg)

    def test_missing_api_key_only(self):
        with self.assertRaises(ModelConfigError) as cm:
            OpenAICompatibleModelClient(
                base_url="https://x", model="m"
            )
        self.assertIn("FTA_LLM_API_KEY", str(cm.exception))
        self.assertNotIn("FTA_LLM_BASE_URL", str(cm.exception))

    def test_env_vars_used_when_args_absent(self):
        os.environ["FTA_LLM_API_KEY"] = "env-key"
        os.environ["FTA_LLM_BASE_URL"] = "https://env.example/v1"
        os.environ["FTA_LLM_MODEL"] = "env-model"
        client = OpenAICompatibleModelClient()
        # 能构造成功即说明读了环境变量
        self.assertIsNotNone(client)


# ======================================================================
# 10. complete() 端到端（mock，不访问网络）
# ======================================================================


class TestCompleteEndToEnd(unittest.TestCase):
    def test_complete_returns_tool_call_turn(self):
        sess = FakeSession(
            responses=[
                _ok_response(
                    {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": '{"msg": "hi"}',
                                },
                            }
                        ],
                    }
                )
            ]
        )
        client = _make_client(session=sess)
        turn = client.complete(
            [{"role": "user", "content": "echo hi"}],
            [{"name": "echo", "description": "d", "input_schema": {"type": "object"}}],
        )
        self.assertIsInstance(turn, AssistantTurn)
        self.assertEqual(turn.tool_calls[0].name, "echo")
        # 验证 system prompt 被前置
        payload = sess.calls[0]["json"]
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("test agent", payload["messages"][0]["content"])
        # tools 被转换
        self.assertEqual(payload["tools"][0]["type"], "function")
        self.assertEqual(payload["tools"][0]["function"]["name"], "echo")

    def test_complete_returns_final_text(self):
        sess = FakeSession(
            responses=[_ok_response({"content": "all done"})]
        )
        client = _make_client(session=sess)
        turn = client.complete([{"role": "user", "content": "go"}], [])
        self.assertEqual(turn.final_text, "all done")

    def test_no_tools_omits_tools_field(self):
        sess = FakeSession(responses=[_ok_response({"content": "ok"})])
        client = _make_client(session=sess)
        client.complete([{"role": "user", "content": "hi"}], [])
        self.assertNotIn("tools", sess.calls[0]["json"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
