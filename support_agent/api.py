import logging
import time
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from support_agent.agent import SupportAgent
from support_agent.config import Settings
from support_agent.db import ConflictError, NotFoundError, Repository
from support_agent.logging import configure_logging
from support_agent.model import ChatModel, HTTPChatModel
from support_agent.schemas import ApprovalRequest, ChatRequest

logger = logging.getLogger("support_agent")


def create_app(settings: Settings | None = None, model: ChatModel | None = None):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings()
        configure_logging(config.log_level)
        repository = Repository(config.database_path, config.approval_ttl_seconds)
        repository.initialize()
        app.state.settings = config
        app.state.repository = repository
        app.state.agent = SupportAgent(config, repository, model or HTTPChatModel(config))
        yield
        app.state.agent.close()

    app = FastAPI(
        title="ServiceDesk · IT Support Agent",
        version="0.1.0",
        lifespan=lifespan,
        description="Local, single-user demo. All enterprise services and tickets are simulated.",
    )

    @app.exception_handler(NotFoundError)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logger.error("request_failed", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=500,
            content={
                "detail": "The request could not complete. Refresh the investigation and check for a recoverable run before retrying."
            },
        )

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        logger.info(
            "request_completed",
            extra={
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return response

    @app.get("/health")
    def health(request: Request):
        config = request.app.state.settings
        with request.app.state.repository.connect() as c:
            c.execute("SELECT 1")
        return {
            "status": "ok",
            "simulated": True,
            "provider": config.llm_provider,
            "model": config.llm_model,
            "max_model_steps": config.max_model_steps,
            "max_tool_calls": config.max_tool_calls,
            "request_timeout_seconds": config.model_timeout_seconds * config.max_model_steps + 30,
        }

    @app.get("/health/model")
    def model_health(request: Request):
        model = request.app.state.agent.model
        return (
            model.check()
            if hasattr(model, "check")
            else {"available": None, "message": "Injected test model; no live provider probe."}
        )

    @app.post("/conversations", status_code=201)
    def create_conversation(request: Request):
        return request.app.state.repository.create_conversation()

    @app.get("/conversations")
    def conversations(request: Request):
        return request.app.state.repository.conversations()

    @app.get("/conversations/{conversation_id}")
    def conversation(conversation_id: UUID, request: Request):
        return request.app.state.agent.view(str(conversation_id))

    @app.get("/conversations/{conversation_id}/activity")
    def activity(conversation_id: UUID, request: Request):
        repository = request.app.state.repository
        repository.conversation(str(conversation_id))
        return repository.activity(str(conversation_id))

    @app.post("/conversations/{conversation_id}/messages")
    def chat(conversation_id: UUID, body: ChatRequest, request: Request):
        return request.app.state.agent.chat(str(conversation_id), body.message)

    @app.post("/conversations/{conversation_id}/approvals/{approval_id}")
    def approval(conversation_id: UUID, approval_id: UUID, body: ApprovalRequest, request: Request):
        return request.app.state.agent.approve(
            str(conversation_id), str(approval_id), body.approved
        )

    @app.post("/conversations/{conversation_id}/resume")
    def resume(conversation_id: UUID, request: Request):
        return request.app.state.agent.resume(str(conversation_id))

    return app


app = create_app()
