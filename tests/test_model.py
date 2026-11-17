import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from support_agent.model import HTTPChatModel, ModelError


def with_response(settings, value, status=200):
    return HTTPChatModel(
        settings, httpx.MockTransport(lambda r: httpx.Response(status, json=value))
    )


def test_ollama_payload_and_tool_result_round_trip(settings):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "get_system_status",
                                    "arguments": {"service": "vpn"},
                                }
                            }
                        ],
                    },
                    "done": True,
                },
            )
        return httpx.Response(
            200, json={"message": {"content": "The simulated VPN is degraded."}, "done": True}
        )

    model = HTTPChatModel(settings, httpx.MockTransport(handler))
    history = [SystemMessage(content="system"), HumanMessage(content="VPN issue")]
    first = model.invoke(history)
    assert first.tool_calls[0]["args"] == {"service": "vpn"}
    result = ToolMessage(
        content='{"status":"degraded"}',
        name="get_system_status",
        tool_call_id=first.tool_calls[0]["id"],
    )
    answer = model.invoke([*history, first, result])
    assert "degraded" in answer.content
    assert len(requests[0]["tools"]) == 5 and requests[0]["stream"] is False
    assert requests[0]["options"]["num_ctx"] == settings.ollama_num_ctx
    assert requests[0]["options"]["num_predict"] == settings.ollama_num_predict
    assert requests[0]["options"]["num_thread"] == settings.ollama_num_thread
    assert "think" not in requests[0]
    assert requests[1]["messages"][-1]["tool_name"] == "get_system_status"
    assert isinstance(requests[1]["messages"][-2]["tool_calls"][0]["function"]["arguments"], dict)
    model.close()


def test_hosted_compatible_payload(settings):
    settings.llm_provider = "openai"
    from pydantic import SecretStr

    settings.openai_api_key = SecretStr("private-test-key")

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer private-test-key"
        payload = json.loads(request.content)
        assert "max_completion_tokens" in payload
        assert payload["messages"][-1]["tool_call_id"] == "call_a"
        assert isinstance(payload["messages"][-2]["tool_calls"][0]["function"]["arguments"], str)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "upstream-id",
                                    "type": "function",
                                    "function": {
                                        "name": "get_ticket",
                                        "arguments": '{"ticket_id":1}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        )

    model = HTTPChatModel(settings, httpx.MockTransport(handler))
    result = model.invoke(
        [
            HumanMessage(content="check ticket"),
            AIMessage(
                content="", tool_calls=[{"id": "call_a", "name": "get_system_status", "args": {}}]
            ),
            ToolMessage(content="{}", tool_call_id="call_a", name="get_system_status"),
        ]
    )
    assert result.tool_calls[0]["args"] == {"ticket_id": 1}
    assert result.tool_calls[0]["id"] != "upstream-id"
    model.close()


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"message": {}},
        {"message": {"content": []}},
        {"message": {"content": 42}},
        {
            "message": {
                "tool_calls": [{"function": {"name": "get_ticket", "arguments": "not json"}}]
            }
        },
        {"message": {"tool_calls": [{"function": {"name": "get_ticket", "arguments": []}}]}},
        {"message": {"content": "incomplete"}, "done_reason": "length"},
        {"message": {"content": "incomplete"}, "done": False},
        {"message": {"content": "Reasoning only.</think>"}},
        {"message": {"content": "<think>Unfinished reasoning"}},
    ],
)
def test_invalid_outputs_fail_gracefully(settings, message):
    model = with_response(settings, message)
    with pytest.raises(ModelError) as error:
        model.invoke([HumanMessage(content="test")])
    assert error.value.code == "invalid_model_output"
    model.close()


@pytest.mark.parametrize(
    "exception,expected",
    [
        (httpx.ConnectError("secret-url"), "model_unavailable"),
        (httpx.ReadTimeout("private-host"), "model_timeout"),
    ],
)
def test_network_failure(settings, exception, expected):
    def handler(request):
        raise exception

    model = HTTPChatModel(settings, httpx.MockTransport(handler))
    with pytest.raises(ModelError) as error:
        model.invoke([HumanMessage(content="test")])
    assert error.value.code == expected
    assert "secret-url" not in str(error.value) and "private-host" not in str(error.value)
    assert model.check()["available"] is False
    model.close()


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_http_error_does_not_expose_provider_body(settings, status):
    model = with_response(settings, {"error": "super-secret-key"}, status)
    with pytest.raises(ModelError) as error:
        model.invoke([HumanMessage(content="test")])
    assert error.value.code == "model_provider_error"
    assert "super-secret-key" not in str(error.value)
    model.close()


def test_invalid_json_and_size_limit(settings):
    for body in ["not-json", "x" * 512001]:
        model = HTTPChatModel(
            settings, httpx.MockTransport(lambda request, body=body: httpx.Response(200, text=body))
        )
        with pytest.raises(ModelError) as error:
            model.invoke([HumanMessage(content="test")])
        assert error.value.code == "invalid_model_output"
        model.close()


def test_model_health_is_only_a_probe(settings):
    model = with_response(settings, {"models": [{"name": settings.llm_model}]})
    assert model.check()["available"] is True
    assert "does not test generation" in model.check()["message"]
    model.close()


def test_explicit_thinking_control_and_reasoning_leak_guard(settings):
    settings.ollama_think = "false"

    def handler(request):
        assert json.loads(request.content)["think"] is False
        return httpx.Response(
            200,
            json={
                "message": {"content": "Internal reasoning.</think>Actual final answer."},
                "done": True,
            },
        )

    model = HTTPChatModel(settings, httpx.MockTransport(handler))
    assert model.invoke([HumanMessage(content="test")]).content == "Actual final answer."
    model.close()
    model = with_response(settings, {"models": []})
    assert model.check()["available"] is False
    model.close()
