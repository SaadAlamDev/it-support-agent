"""Provider diagnostics are tested with mocked transports, never live Groq calls."""

import errno
import io
import json
import logging
import socket
import ssl

import httpx
import pytest
from langchain_core.messages import HumanMessage
from pydantic import SecretStr

from support_agent.logging import JsonFormatter
from support_agent.model import HTTPChatModel, ModelError

CANARY = "synthetic-provider-detail-must-not-be-logged"


@pytest.fixture
def diagnostic_log():
    logger = logging.getLogger("support_agent")
    old = logger.handlers, logger.level, logger.propagate
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield stream
    finally:
        logger.handlers, level, logger.propagate = old
        logger.setLevel(level)


def hosted(settings):
    settings.llm_provider = "openai"
    settings.llm_model = "openai/gpt-oss-20b"
    settings.openai_base_url = "https://api.groq.com/openai/v1"
    settings.openai_api_key = SecretStr(CANARY)
    return settings


@pytest.mark.parametrize(
    "cause,expected",
    [
        (socket.gaierror(socket.EAI_NONAME, CANARY), "dns_error"),
        (ssl.SSLCertVerificationError(1, CANARY), "tls_certificate_error"),
        (ssl.SSLError(1, CANARY), "tls_error"),
        (ConnectionRefusedError(errno.ECONNREFUSED, CANARY), "connection_refused"),
        (OSError(errno.ENETUNREACH, CANARY), "network_unreachable"),
        (None, "connection_error"),
    ],
)
def test_hosted_transport_reports_safe_cause(settings, diagnostic_log, cause, expected):
    def failure(request):
        raise httpx.ConnectError(CANARY) from cause

    model = HTTPChatModel(hosted(settings), httpx.MockTransport(failure))
    try:
        with pytest.raises(ModelError) as caught:
            model.invoke([HumanMessage(content="A synthetic VPN question")])
        assert caught.value.code == "model_unavailable"
        assert "Ollama" not in caught.value.message
        assert CANARY not in caught.value.message
        records = [json.loads(line) for line in diagnostic_log.getvalue().splitlines()]
        failed = next(r for r in records if r["event"] == "model_transport_failed")
        assert failed["provider"] == "openai"
        assert failed["model_endpoint"] == "groq"
        assert failed["transport_error"] == "ConnectError"
        assert failed["failure_reason"] == expected
        assert CANARY not in diagnostic_log.getvalue()
        assert "https://" not in diagnostic_log.getvalue()
    finally:
        model.close()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500])
def test_http_rejection_logs_status_not_body_or_key(settings, diagnostic_log, status):
    model = HTTPChatModel(
        hosted(settings),
        httpx.MockTransport(lambda r: httpx.Response(status, json={"error": CANARY})),
    )
    try:
        with pytest.raises(ModelError) as caught:
            model.invoke([HumanMessage(content="Synthetic question")])
        assert caught.value.code == "model_provider_error"
        if status == 404:
            assert "Ollama" not in caught.value.message
        records = [json.loads(line) for line in diagnostic_log.getvalue().splitlines()]
        failed = next(r for r in records if r["event"] == "model_http_failed")
        assert failed["provider_status_code"] == status
        assert failed["model_endpoint"] == "groq"
        assert CANARY not in diagnostic_log.getvalue() + caught.value.message
    finally:
        model.close()


def test_provider_configuration_log_is_only_safe_labels(settings, diagnostic_log):
    settings = hosted(settings)
    settings.openai_base_url = "https://" + CANARY + ".invalid/v1?token=" + CANARY
    model = HTTPChatModel(settings, httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    try:
        record = json.loads(diagnostic_log.getvalue().splitlines()[0])
        assert record["event"] == "model_configured"
        assert record["model_endpoint"] == "hosted_other"
        assert record["key_configured"] is True
        assert CANARY not in diagnostic_log.getvalue()
    finally:
        model.close()


@pytest.mark.parametrize(
    "failure,reason",
    [
        (httpx.ReadTimeout(CANARY), "timeout"),
        (httpx.ProxyError(CANARY), "proxy_error"),
        (httpx.RemoteProtocolError(CANARY), "protocol_error"),
    ],
)
def test_other_transport_errors_are_sanitized(settings, diagnostic_log, failure, reason):
    def handler(request):
        raise failure

    model = HTTPChatModel(hosted(settings), httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelError):
            model.invoke([HumanMessage(content="Synthetic question")])
        records = [json.loads(line) for line in diagnostic_log.getvalue().splitlines()]
        assert (
            next(r for r in records if r["event"] == "model_transport_failed")["failure_reason"]
            == reason
        )
        assert CANARY not in diagnostic_log.getvalue()
    finally:
        model.close()


def test_groq_payload_excludes_ollama_and_unsupported_fields(settings):
    config = hosted(settings)
    config.hosted_reasoning_effort = "low"
    config.max_completion_tokens = 1536

    def handler(request):
        payload = json.loads(request.content)
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert set(payload) == {
            "model",
            "messages",
            "stream",
            "tools",
            "max_completion_tokens",
            "reasoning_effort",
        }
        assert payload["reasoning_effort"] == "low"
        assert len(payload["tools"]) == 5
        assert all("name" not in message for message in payload["messages"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "Protocol fixture only"}, "finish_reason": "stop"}
                ]
            },
        )

    model = HTTPChatModel(config, httpx.MockTransport(handler))
    try:
        assert (
            model.invoke([HumanMessage(content="Synthetic question")]).content
            == "Protocol fixture only"
        )
    finally:
        model.close()


@pytest.mark.parametrize("available,expected_exit", [(True, 0), (False, 1)])
def test_groq_probe_only_never_runs_model_or_claims_inference(
    monkeypatch, tmp_path, capsys, available, expected_exit
):
    from scripts import smoke_groq
    from support_agent.api import create_app

    class ProbeOnlyModel:
        def check(self):
            return {"available": available, "message": "Synthetic probe fixture"}

        def invoke(self, messages):
            raise AssertionError("Probe-only mode must not generate")

    monkeypatch.setenv("GROQ_API_KEY", CANARY)
    monkeypatch.setattr(
        smoke_groq, "create_app", lambda settings: create_app(settings, ProbeOnlyModel())
    )
    output = tmp_path / "probe.json"
    monkeypatch.setattr("sys.argv", ["smoke_groq.py", "--probe-only", "--output", str(output)])
    assert smoke_groq.main() == expected_exit
    evidence = json.loads(output.read_text())
    assert (
        evidence["scope"]
        == "Groq connectivity/model-list probe only; generation and tool calling remain unverified"
    )
    assert evidence["result"] == ("probe_passed_generation_unverified" if available else "failed")
    assert "trace" not in evidence
    printed = capsys.readouterr()
    assert CANARY not in printed.out + printed.err + output.read_text()
