"""Validated HTTP boundary for the Streamlit client."""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()


class APIError(Exception):
    pass


def request(method: str, path: str, body=None, timeout: float = 10, *, session_token=None):
    """Call the backend and reject invalid envelopes without exposing provider errors."""
    try:
        base = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
        key = os.getenv("SUPPORT_API_KEY", "")
        headers = {}
        if key:
            headers["X-API-Key"] = key
        if session_token:
            headers["X-Demo-Session"] = session_token
        response = httpx.request(
            method, base + path, json=body, timeout=timeout, trust_env=False, headers=headers
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise APIError(
                f"The backend returned invalid JSON (HTTP {response.status_code}). Refresh saved state before retrying a change."
            ) from exc
        if response.is_error:
            detail = (
                payload.get("detail", "Request failed")
                if isinstance(payload, dict)
                else "The backend returned an unexpected error response."
            )
            if response.status_code >= 500:
                detail = "The backend could not complete the request. Refresh saved state before retrying a change."
            if not isinstance(detail, str):
                detail = "The request was invalid. Check the entered values."
            raise APIError(f"{detail} (HTTP {response.status_code})")
        expects_list = method == "GET" and (path == "/conversations" or path.endswith("/activity"))
        if not isinstance(payload, list if expects_list else dict):
            raise APIError("The backend returned an unexpected response format.")
        if path == "/health" and not {
            "provider",
            "model",
            "request_timeout_seconds",
            "max_model_steps",
            "max_tool_calls",
        }.issubset(payload):
            raise APIError("The backend health response is incomplete.")
        return payload
    except httpx.TimeoutException as exc:
        raise APIError(
            "The backend request timed out. Refresh to inspect the saved result before retrying an approval."
        ) from exc
    except httpx.RequestError as exc:
        raise APIError(
            "Cannot reach the backend. Check API_BASE_URL; a sleeping free cloud service may need a minute to start. Retry after it wakes."
        ) from exc
