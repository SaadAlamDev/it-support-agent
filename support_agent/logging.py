import json
import logging
import time


class JsonFormatter(logging.Formatter):
    """Allow-listed metadata only: never log prompts, model output, URLs, or exceptions."""

    def format(self, record):
        value = {"timestamp": time.time(), "level": record.levelname, "event": record.getMessage()}
        for key in (
            "conversation_id",
            "tool",
            "error_type",
            "duration_ms",
            "status_code",
            "provider",
            "model_endpoint",
            "key_configured",
            "transport_error",
            "failure_reason",
            "provider_status_code",
        ):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value)


def configure_logging(level: str):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("support_agent")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
    # HTTP client logs can contain credential-bearing provider URLs.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
