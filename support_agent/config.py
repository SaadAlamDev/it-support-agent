import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_mode: Literal["local", "cloud"] = "local"
    storage_mode: Literal["local", "ephemeral", "persistent"] = "local"
    reset_demo_on_start: bool = False
    support_api_key: SecretStr = SecretStr("")
    cors_allowed_origins: str = ""
    max_completion_tokens: int = Field(default=2048, ge=128, le=4096)
    hosted_reasoning_effort: Literal["", "low", "medium", "high"] = ""
    max_context_characters: int = Field(default=24000, ge=8000, le=100000)
    max_model_calls_per_day: int = Field(default=200, ge=1, le=100000)
    max_conversations_per_session: int = Field(default=5, ge=1, le=100)
    max_conversations_total: int = Field(default=200, ge=1, le=10000)
    demo_requests_per_minute: int = Field(default=180, ge=10, le=1000)
    demo_actions_per_minute: int = Field(default=6, ge=1, le=60)
    max_request_body_bytes: int = Field(default=20000, ge=1024, le=64000)

    llm_provider: Literal["ollama", "openai"] = "ollama"
    llm_model: str = "qwen3:4b-instruct"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_num_ctx: int = Field(default=8192, ge=2048, le=65536)
    ollama_num_predict: int = Field(default=1024, ge=128, le=4096)
    ollama_num_thread: int = Field(default=6, ge=1, le=128)
    ollama_think: Literal["auto", "true", "false"] = "auto"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr = SecretStr("")
    model_timeout_seconds: float = Field(default=180, ge=0.1, le=300)
    database_path: Path = Path("data/support.db")
    checkpoint_path: Path = Path("data/checkpoints.db")
    max_model_steps: int = Field(default=6, ge=1, le=12)
    max_tool_calls: int = Field(default=8, ge=1, le=20)
    max_history_messages: int = Field(default=100, ge=10, le=300)
    approval_ttl_seconds: int = Field(default=1800, ge=1, le=86400)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def valid_paths(self):
        if self.reset_demo_on_start and (
            self.app_mode != "cloud" or self.storage_mode != "ephemeral"
        ):
            raise ValueError("RESET_DEMO_ON_START is only allowed for cloud ephemeral demos")
        if (
            self.reset_demo_on_start
            and self.database_path.parent.resolve() != self.checkpoint_path.parent.resolve()
        ):
            raise ValueError("Resettable demo databases must use the same parent directory")
        if os.getenv("RENDER", "").lower() == "true" and self.app_mode != "cloud":
            raise ValueError(
                "Render requires APP_MODE=cloud; refusing an unprotected local profile"
            )
        if self.database_path.resolve() == self.checkpoint_path.resolve():
            raise ValueError("Application and checkpoint databases must have separate paths")
        if self.llm_provider == "openai" and not self.openai_api_key.get_secret_value():
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in ("", "/")
                or (self.app_mode == "cloud" and parsed.scheme != "https")
            ):
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must contain explicit origins; wildcard is forbidden"
                )
        if self.app_mode == "cloud":
            if len(self.support_api_key.get_secret_value()) < 32:
                raise ValueError("Cloud mode requires a SUPPORT_API_KEY of at least 32 characters")
            if self.storage_mode == "local":
                raise ValueError(
                    "Cloud mode requires explicit STORAGE_MODE=ephemeral or persistent"
                )
            if self.llm_provider != "openai":
                raise ValueError("Cloud mode requires the hosted Chat Completions provider")
            if self.model_timeout_seconds > 60:
                raise ValueError("Cloud mode requires MODEL_TIMEOUT_SECONDS <= 60")
            url = urlsplit(self.openai_base_url)
            if (
                url.scheme != "https"
                or not url.netloc
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("Cloud hosted provider requires a credential-free HTTPS base URL")
        return self

    @property
    def protected(self):
        return bool(self.support_api_key.get_secret_value())

    @property
    def allowed_origins(self):
        return [s.strip().rstrip("/") for s in self.cors_allowed_origins.split(",") if s.strip()]
