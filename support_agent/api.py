import logging
import time
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from support_agent.agent import SupportAgent
from support_agent.config import Settings
from support_agent.db import ConflictError, NotFoundError, QuotaError, Repository
from support_agent.logging import configure_logging
from support_agent.model import ChatModel, HTTPChatModel
from support_agent.schemas import ApprovalRequest, ChatRequest
from support_agent.security import DemoGuard
from support_agent.storage import demo_storage

logger = logging.getLogger("support_agent")


def create_app(settings: Settings | None = None, model: ChatModel | None = None):
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        configure_logging(config.log_level)
        with demo_storage(config) as (runtime, instance_id):
            repository = Repository(
                runtime.database_path, runtime.approval_ttl_seconds, isolated=runtime.protected
            )
            repository.initialize()
            app.state.settings = runtime
            app.state.demo_instance_id = instance_id
            app.state.repository = repository
            app.state.guard = DemoGuard(runtime)
            app.state.agent = SupportAgent(runtime, repository, model or HTTPChatModel(runtime))
            try:
                yield
            finally:
                app.state.agent.close()

    app = FastAPI(
        title="ServiceDesk · IT Support Agent",
        version="0.1.0",
        lifespan=lifespan,
        description="Synthetic IT support demo. Protected mode isolates visitor sessions; local mode is shared.",
        docs_url=None if config.app_mode == "cloud" else "/docs",
        redoc_url=None if config.app_mode == "cloud" else "/redoc",
        openapi_url=None if config.app_mode == "cloud" else "/openapi.json",
    )

    @app.exception_handler(NotFoundError)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(QuotaError)
    async def quota(request, exc):
        return JSONResponse(
            status_code=429, content={"detail": str(exc)}, headers={"Retry-After": "60"}
        )

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
        guard = request.app.state.guard
        reserved = False
        try:
            request.state.owner_id = None
            if config.protected and request.url.path != "/healthz":
                owner = guard.owner(
                    request.headers.get("X-API-Key"), request.headers.get("X-Demo-Session")
                )
                if owner is None:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "detail": "Valid API credentials and a demo session are required."
                        },
                    )
                request.state.owner_id = owner
                if not guard.allowed(owner, action=request.method == "POST"):
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": "Demo request limit reached. Wait a minute before retrying."
                        },
                        headers={"Retry-After": "60"},
                    )
                if request.method == "POST":
                    # Bound the body while reading, including chunked uploads.
                    size = 0
                    chunks = []
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > config.max_request_body_bytes:
                            return JSONResponse(
                                status_code=413,
                                content={"detail": "Request body exceeds the demo size limit."},
                            )
                        chunks.append(chunk)
                    request._body = b"".join(chunks)
                    if (
                        request.url.path.endswith(("/messages", "/resume"))
                        or "/approvals/" in request.url.path
                    ):
                        reserved = guard.active_run.acquire(blocking=False)
                        if not reserved:
                            return JSONResponse(
                                status_code=429,
                                content={
                                    "detail": "The demo is processing another investigation. Please retry shortly."
                                },
                                headers={"Retry-After": "5"},
                            )
            response = await call_next(request)
        except Exception as exc:
            # ServerErrorMiddleware re-raises even after rendering a 500 response.
            # Handle endpoint failures here so Uvicorn cannot emit raw exception
            # messages (which may contain credentials) in an unsanitized traceback.
            response = await unexpected(request, exc)
        finally:
            if reserved:
                guard.active_run.release()
        logger.info(
            "request_completed",
            extra={
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return response

    # Server-to-server Streamlit requests need no CORS permission. Optional exact
    # browser origins may preflight; CORS never grants API authentication.
    if config.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-API-Key", "X-Demo-Session"],
            allow_credentials=False,
        )

    @app.get("/healthz")
    def liveness():
        return {"status": "ok"}

    def owned(request, conversation_id):
        request.app.state.repository.conversation(str(conversation_id), request.state.owner_id)

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
            "storage_mode": config.storage_mode,
            "session_isolation": config.protected,
            "reset_demo_on_start": config.reset_demo_on_start,
            "demo_instance_id": request.app.state.demo_instance_id,
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
        repository = request.app.state.repository
        if not config.protected:
            return repository.create_conversation()
        return repository.create_conversation(
            request.state.owner_id,
            config.max_conversations_per_session,
            config.max_conversations_total,
        )

    @app.get("/conversations")
    def conversations(request: Request):
        return request.app.state.repository.conversations(request.state.owner_id)

    @app.get("/conversations/{conversation_id}")
    def conversation(conversation_id: UUID, request: Request):
        owned(request, conversation_id)
        return request.app.state.agent.view(str(conversation_id))

    @app.get("/conversations/{conversation_id}/activity")
    def activity(conversation_id: UUID, request: Request):
        owned(request, conversation_id)
        repository = request.app.state.repository
        repository.conversation(str(conversation_id))
        return repository.activity(str(conversation_id))

    @app.post("/conversations/{conversation_id}/messages")
    def chat(conversation_id: UUID, body: ChatRequest, request: Request):
        owned(request, conversation_id)
        return request.app.state.agent.chat(str(conversation_id), body.message)

    @app.post("/conversations/{conversation_id}/approvals/{approval_id}")
    def approval(conversation_id: UUID, approval_id: UUID, body: ApprovalRequest, request: Request):
        owned(request, conversation_id)
        return request.app.state.agent.approve(
            str(conversation_id), str(approval_id), body.approved
        )

    @app.post("/conversations/{conversation_id}/resume")
    def resume(conversation_id: UUID, request: Request):
        owned(request, conversation_id)
        return request.app.state.agent.resume(str(conversation_id))

    return app


app = create_app()
