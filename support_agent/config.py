from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
        if self.database_path.resolve() == self.checkpoint_path.resolve():
            raise ValueError("Application and checkpoint databases must have separate paths")
        if self.llm_provider == "openai" and not self.openai_api_key.get_secret_value():
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return self
