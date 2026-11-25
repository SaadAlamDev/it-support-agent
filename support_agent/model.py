"""Small HTTP adapters for Ollama native chat and hosted Chat Completions.

Both receive the same JSON tool schemas; the model chooses which tools to use.
No local text classifier, canned answer fallback, or automatic side effects.
"""

import errno
import json
import logging
import socket
import ssl
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from support_agent.config import Settings
from support_agent.tools import tool_definitions

logger = logging.getLogger("support_agent")


def transport_failure_reason(exc):
    """Classify typed causes without logging exception text, URLs, or headers."""
    cause = exc
    for _ in range(8):
        if isinstance(cause, ssl.SSLCertVerificationError):
            return "tls_certificate_error"
        if isinstance(cause, ssl.SSLError):
            return "tls_error"
        if isinstance(cause, socket.gaierror):
            return "dns_error"
        if isinstance(cause, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(cause, OSError) and cause.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            return "network_unreachable"
        cause = cause.__cause__ or cause.__context__
        if cause is None:
            break
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ProxyError):
        return "proxy_error"
    if isinstance(exc, httpx.ProtocolError):
        return "protocol_error"
    if isinstance(exc, httpx.ConnectError):
        return "connection_error"
    return "transport_error"


class ModelError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ChatModel(Protocol):
    def invoke(self, messages: list[BaseMessage]) -> AIMessage: ...


class HTTPChatModel:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.client = httpx.Client(
            timeout=settings.model_timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )
        logger.info("model_configured", extra=self.diagnostic_context())

    def diagnostic_context(self):
        """Only fixed endpoint labels and booleans; never return a configured URL."""
        s = self.settings
        base = s.ollama_base_url if s.llm_provider == "ollama" else s.openai_base_url
        try:
            target = urlsplit(base)
            loopback = target.hostname in ("localhost", "127.0.0.1", "::1")
            if s.llm_provider == "ollama":
                endpoint = "ollama_loopback" if loopback else "ollama_remote"
            elif target.hostname == "api.groq.com":
                endpoint = (
                    "groq" if target.path.rstrip("/") == "/openai/v1" else "groq_wrong_base_path"
                )
            elif target.hostname == "api.openai.com":
                endpoint = "openai"
            else:
                endpoint = "hosted_loopback" if loopback else "hosted_other"
        except ValueError:
            endpoint = "invalid_base_url"
        return {
            "provider": s.llm_provider,
            "model_endpoint": endpoint,
            "key_configured": bool(s.openai_api_key.get_secret_value())
            if s.llm_provider == "openai"
            else False,
        }

    def _log_transport_failure(self, exc):
        logger.warning(
            "model_transport_failed",
            extra={
                **self.diagnostic_context(),
                "transport_error": type(exc).__name__,
                "failure_reason": transport_failure_reason(exc),
            },
        )

    def close(self):
        self.client.close()

    def _request(self, method, path, payload=None):
        s = self.settings
        base = s.ollama_base_url if s.llm_provider == "ollama" else s.openai_base_url
        headers = {}
        if s.llm_provider == "openai":
            headers["Authorization"] = "Bearer " + s.openai_api_key.get_secret_value()
        try:
            with self.client.stream(
                method, base.rstrip("/") + path, json=payload, headers=headers
            ) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 512_000:
                        raise ModelError(
                            "invalid_model_output", "The model response exceeded the size limit."
                        )
                return json.loads(body)
        except httpx.TimeoutException as exc:
            self._log_transport_failure(exc)
            raise ModelError(
                "model_timeout",
                "The model request timed out. Check the model server and try again.",
            ) from exc
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            logger.warning(
                "model_http_failed",
                extra={
                    **self.diagnostic_context(),
                    "provider_status_code": code,
                },
            )
            if code == 401:
                message = (
                    "The model provider rejected its credentials. Check the backend configuration."
                )
            elif code == 403:
                message = "The model provider denied access. Check key/model permissions and account or network restrictions."
            elif code == 404:
                message = (
                    "Model or endpoint not found. Pull LLM_MODEL in Ollama."
                    if s.llm_provider == "ollama"
                    else "Hosted model or endpoint not found. Check LLM_MODEL and OPENAI_BASE_URL."
                )
            elif code == 429:
                message = "The model provider is rate limited. Please try again later."
            else:
                message = "The model server returned an error. Check its availability and model tool-calling support."
            raise ModelError("model_provider_error", message) from exc
        except httpx.RequestError as exc:
            self._log_transport_failure(exc)
            raise ModelError(
                "model_unavailable",
                (
                    "Cannot reach Ollama. Start Ollama and pull LLM_MODEL. No LLM answer was generated."
                    if s.llm_provider == "ollama"
                    else "Cannot reach the hosted model provider. Check OPENAI_BASE_URL and the backend's outbound DNS/TLS connectivity. See sanitized backend logs for the transport failure. No LLM answer was generated."
                ),
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise ModelError(
                "invalid_model_output",
                "The model server returned invalid JSON. Check that the endpoint supports chat and tool calling.",
            ) from exc

    def check(self):
        """Connectivity/model-list probe only; never claims generation was tested."""
        try:
            if self.settings.llm_provider == "ollama":
                value = self._request("GET", "/api/tags")
                names = {m["name"] for m in value["models"]}
                model = self.settings.llm_model
                if model not in names and model + ":latest" not in names:
                    return {
                        "available": False,
                        "message": "Ollama is reachable but LLM_MODEL is not installed. Pull that exact model tag.",
                    }
            else:
                value = self._request("GET", "/models")
                if not isinstance(value, dict) or not isinstance(value.get("data"), list):
                    raise ValueError("Invalid model list")
                if not any(
                    isinstance(m, dict)
                    and m.get("id") == self.settings.llm_model
                    and m.get("active", True)
                    for m in value["data"]
                ):
                    return {
                        "available": False,
                        "message": "Provider reachable but the configured model is absent or inactive for this account.",
                    }
            return {
                "available": True,
                "message": "Model server reachable. This probe does not test generation or tool calling.",
            }
        except ModelError as exc:
            return {"available": False, "message": exc.message}
        except (KeyError, TypeError, ValueError):
            return {
                "available": False,
                "message": "Model server returned an unexpected model-list format.",
            }

    def _messages(self, messages):
        ollama = self.settings.llm_provider == "ollama"
        result = []
        for message in messages:
            if isinstance(message, SystemMessage):
                result.append({"role": "system", "content": message.content})
            elif isinstance(message, HumanMessage):
                result.append({"role": "user", "content": message.content})
            elif isinstance(message, ToolMessage):
                entry = {"role": "tool", "content": message.content}
                entry.update(
                    {"tool_name": message.name}
                    if ollama
                    else {"tool_call_id": message.tool_call_id}
                )
                result.append(entry)
            elif isinstance(message, AIMessage):
                entry = {"role": "assistant", "content": message.content}
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["args"] if ollama else json.dumps(call["args"]),
                            },
                        }
                        for call in message.tool_calls
                    ]
                result.append(entry)
        return result

    def invoke(self, messages):
        ollama = self.settings.llm_provider == "ollama"
        payload = {
            "model": self.settings.llm_model,
            "messages": self._messages(messages),
            "stream": False,
            "tools": tool_definitions(),
        }
        if ollama:
            payload["options"] = {
                "temperature": 0,
                "num_predict": self.settings.ollama_num_predict,
                "num_ctx": self.settings.ollama_num_ctx,
                "num_thread": self.settings.ollama_num_thread,
            }
            # Instruct-only models may reject a thinking parameter. Hybrid models
            # can opt in/out explicitly; auto uses the model's supported default.
            if self.settings.ollama_think != "auto":
                payload["think"] = self.settings.ollama_think == "true"
        else:
            payload["max_completion_tokens"] = self.settings.max_completion_tokens
            if self.settings.hosted_reasoning_effort:
                payload["reasoning_effort"] = self.settings.hosted_reasoning_effort
        if (
            self.settings.protected
            and len(json.dumps(payload, ensure_ascii=False)) > self.settings.max_context_characters
        ):
            raise ModelError(
                "context_limit", "The demo context limit was reached. Start a new investigation."
            )
        value = self._request("POST", "/api/chat" if ollama else "/chat/completions", payload)
        try:
            if not isinstance(value, dict):
                raise ValueError("Response must be an object")
            if ollama:
                message, reason = value["message"], value.get("done_reason")
            else:
                choices = value["choices"]
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    raise ValueError("Invalid chat choices")
                message, reason = choices[0]["message"], choices[0].get("finish_reason")
            if not isinstance(message, dict):
                raise ValueError("Message must be an object")
            if reason in ("length", "content_filter") or (ollama and value.get("done") is False):
                raise ValueError("Incomplete generation")
            content = message.get("content")
            content = "" if content is None else content
            calls = message.get("tool_calls")
            calls = [] if calls is None else calls
            # Some Ollama templates open <think> in the prompt and, when asked to
            # disable thinking, leak reasoning into content rather than a separate
            # thinking field. Keep only the actual final answer, never that reasoning.
            if isinstance(content, str) and "</think>" in content:
                content = content.rsplit("</think>", 1)[1].strip()
            elif isinstance(content, str) and "<think>" in content:
                raise ValueError("Unfinished reasoning without a final answer")
            if (
                not isinstance(content, str)
                or len(content) > 16000
                or not isinstance(calls, list)
                or len(calls) > 20
            ):
                raise ValueError("Output limits")
            normalized = []
            for call in calls:
                function = call["function"]
                args = function["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                name = function["name"]
                if (
                    not isinstance(args, dict)
                    or len(json.dumps(args)) > 16000
                    or not isinstance(name, str)
                    or len(name) > 64
                ):
                    raise ValueError("Invalid tool call")
                # Use backend-issued identifiers to prevent cross-turn ID collisions/replay.
                normalized.append({"id": "call_" + uuid4().hex, "name": name, "args": args})
            if not content.strip() and not normalized:
                raise ValueError("Empty output")
            return AIMessage(content=content, tool_calls=normalized)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ModelError(
                "invalid_model_output",
                "The model returned an empty, incomplete, or invalid tool-call response. Try again with a model that supports tools.",
            ) from exc
