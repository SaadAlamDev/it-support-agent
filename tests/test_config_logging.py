import json
import logging

import pytest
from pydantic import ValidationError

from support_agent.config import Settings
from support_agent.logging import JsonFormatter


def test_configuration_validation(tmp_path):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="openai", openai_api_key="")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, database_path=tmp_path / "same.db", checkpoint_path=tmp_path / "same.db"
        )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_model_steps=0)


def test_environment_settings(monkeypatch):
    monkeypatch.setenv("MAX_TOOL_CALLS", "4")
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "12")
    assert Settings(_env_file=None).max_tool_calls == 4
    assert Settings(_env_file=None).model_timeout_seconds == 12


def test_structured_logger_excludes_non_allowlisted_metadata():
    record = logging.LogRecord("support_agent", logging.WARNING, "", 0, "model_failed", (), None)
    record.error_type = "model_timeout"
    record.api_key = "do-not-emit"
    record.arguments = {"password": "never-log"}
    value = JsonFormatter().format(record)
    assert json.loads(value)["error_type"] == "model_timeout"
    assert "do-not-emit" not in value and "never-log" not in value
