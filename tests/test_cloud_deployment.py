"""Cloud safety checks. All models/transports here are MOCKED, not hosted inference."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from frontend.api_client import request as frontend_request
from support_agent.api import create_app
from support_agent.config import Settings
from support_agent.db import QuotaError, Repository
from support_agent.model import HTTPChatModel, ModelError
from tests.conftest import CREATE, ScriptedModel, tool_call
from tests.test_frontend import FRONTEND

API_KEY = "test-only-demo-api-key-for-local-qa"
SESSION_A = "a" * 43
SESSION_B = "b" * 43


def cloud_settings(tmp_path, **overrides):
    values = dict(
        _env_file=None,
        app_mode="cloud",
        storage_mode="ephemeral",
        llm_provider="openai",
        openai_base_url="https://api.groq.com/openai/v1",
        llm_model="openai/gpt-oss-20b",
        openai_api_key="synthetic-provider-credential",
        support_api_key=API_KEY,
        model_timeout_seconds=30,
        database_path=tmp_path / "support.db",
        checkpoint_path=tmp_path / "checkpoints.db",
        demo_actions_per_minute=60,
    )
    return Settings(**(values | overrides))


def headers(session=SESSION_A):
    return {"X-API-Key": API_KEY, "X-Demo-Session": session}


def create(client, session=SESSION_A):
    result = client.post("/conversations", headers=headers(session))
    assert result.status_code == 201, result.text
    return result.json()["id"]


def turn(client, conversation, session=SESSION_A):
    return client.post(
        f"/conversations/{conversation}/messages",
        headers=headers(session),
        json={"message": "Synthetic investigation"},
    )


def decide(client, conversation, approval_id, approved=True, session=SESSION_A):
    return client.post(
        f"/conversations/{conversation}/approvals/{approval_id}",
        headers=headers(session),
        json={"approved": approved},
    )


@pytest.mark.parametrize(
    "override",
    [
        {"support_api_key": ""},
        {"support_api_key": "short"},
        {"storage_mode": "local"},
        {"llm_provider": "ollama"},
        {"model_timeout_seconds": 180},
        {"openai_base_url": "http://example.com/v1"},
        {"openai_base_url": "https://" + "user" + ":" + "password" + "@example.com/v1"},
        {"cors_allowed_origins": "*"},
        {"cors_allowed_origins": "https://example.com/path"},
    ],
)
def test_cloud_config_fails_closed(tmp_path, override):
    with pytest.raises(ValidationError):
        cloud_settings(tmp_path, **override)


def test_secret_validation_does_not_print_values(tmp_path):
    with pytest.raises(ValidationError) as error:
        cloud_settings(tmp_path, support_api_key="canary")
    assert "canary" not in str(error.value)


def test_liveness_and_authentication(tmp_path):
    with TestClient(create_app(cloud_settings(tmp_path), ScriptedModel())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        for candidate in [
            {},
            {"X-API-Key": API_KEY},
            headers("invalid"),
            headers() | {"X-API-Key": "wrong"},
        ]:
            assert client.get("/health", headers=candidate).status_code == 401
        data = client.get("/health", headers=headers()).json()
        assert data["storage_mode"] == "ephemeral" and data["session_isolation"]
        assert API_KEY not in json.dumps(data)
        assert client.get("/docs", headers=headers()).status_code == 404


def test_conversations_and_approvals_are_session_scoped(tmp_path):
    model = ScriptedModel(
        [tool_call("create_ticket", CREATE), AIMessage(content="Approved write complete")]
    )
    settings = cloud_settings(tmp_path)
    with TestClient(create_app(settings, model)) as client:
        first, second = create(client), create(client, SESSION_B)
        assert [c["id"] for c in client.get("/conversations", headers=headers()).json()] == [first]
        for suffix in ["", "/activity"]:
            assert (
                client.get(
                    f"/conversations/{first}{suffix}", headers=headers(SESSION_B)
                ).status_code
                == 404
            )
        proposal = turn(client, first).json()["pending_approval"]
        assert client.app.state.repository.ticket(1, first)["ticket"]["id"] == 1
        with client.app.state.repository.connect() as c:
            assert c.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
        assert decide(client, first, proposal["id"], session=SESSION_B).status_code == 404
        assert turn(client, first, SESSION_B).status_code == 404
        assert (
            client.post(f"/conversations/{first}/resume", headers=headers(SESSION_B)).status_code
            == 404
        )
        assert turn(client, first).status_code == 409  # chat cannot approve
        forged = client.post(
            f"/conversations/{first}/approvals/{proposal['id']}",
            headers=headers(),
            json={"approved": True, "arguments": {"priority": "low"}},
        )
        assert forged.status_code == 422
        assert decide(client, first, proposal["id"]).status_code == 200
        assert decide(client, first, proposal["id"]).status_code == 409
        repository = client.app.state.repository
        assert repository.ticket(2, first)["ticket"]["title"] == CREATE["title"]
        with pytest.raises(Exception, match="Ticket not found"):
            repository.ticket(2, second)
        with repository.connect() as c:
            assert c.execute("SELECT count(*) FROM tickets").fetchone()[0] == 2
    # Fresh instance, same key/session/file: ownership, history, and receipts survive.
    with TestClient(create_app(settings, ScriptedModel())) as client:
        view = client.get(f"/conversations/{first}", headers=headers()).json()
        assert view["messages"][-1]["content"] == "Approved write complete"
        assert client.get(f"/conversations/{first}", headers=headers(SESSION_B)).status_code == 404


def test_ticket_access_is_enforced_for_model_calls_and_updates(tmp_path):
    model = ScriptedModel(
        [
            tool_call("create_ticket", CREATE),
            AIMessage(content="Created"),
            tool_call("get_ticket", {"ticket_id": 2}),
            AIMessage(content="Not found"),
            tool_call("update_ticket", {"ticket_id": 2, "note": "forged"}),
            AIMessage(content="No access"),
            tool_call("update_ticket", {"ticket_id": 1, "note": "forged"}),
            AIMessage(content="Read only"),
            tool_call(
                "update_ticket", {"ticket_id": 2, "note": "Approved owner note"}, "owned_update"
            ),
            AIMessage(content="Updated"),
        ]
    )
    with TestClient(create_app(cloud_settings(tmp_path), model)) as client:
        first, second = create(client), create(client, SESSION_B)
        proposal = turn(client, first).json()["pending_approval"]
        assert decide(client, first, proposal["id"]).status_code == 200
        read = turn(client, second, SESSION_B).json()
        assert read["activity"][-1]["result"]["error"] == "not_found"
        for _ in range(2):
            result = turn(client, second, SESSION_B).json()
            assert result["pending_approval"] is None
            assert result["activity"][-1]["result"]["error"] == "invalid_proposal"
        pending = turn(client, first).json()["pending_approval"]
        assert decide(client, first, pending["id"]).status_code == 200
        assert (
            client.app.state.repository.ticket(2, first)["ticket"]["notes"][-1]["text"]
            == "Approved owner note"
        )


def test_rate_body_capacity_and_daily_limits(tmp_path):
    model = ScriptedModel([AIMessage(content="Once")])
    settings = cloud_settings(
        tmp_path,
        max_model_calls_per_day=1,
        max_request_body_bytes=1024,
        max_conversations_per_session=1,
        max_conversations_total=2,
    )
    with TestClient(create_app(settings, model)) as client:
        conversation = create(client)
        assert client.post("/conversations", headers=headers()).status_code == 429
        create(client, SESSION_B)
        assert client.post("/conversations", headers=headers("c" * 43)).status_code == 429
        assert (
            client.post(
                f"/conversations/{conversation}/messages", headers=headers(), content=b"x" * 1025
            ).status_code
            == 413
        )
        assert turn(client, conversation).json()["messages"][-1]["content"] == "Once"
        assert turn(client, conversation).json()["error"]["code"] == "daily_limit"
        assert len(model.seen) == 1
    with TestClient(create_app(settings, ScriptedModel())) as client:
        assert turn(client, conversation).json()["error"]["code"] == "daily_limit"
    settings = cloud_settings(tmp_path / "rate", demo_actions_per_minute=1)
    with TestClient(create_app(settings, ScriptedModel())) as client:
        conversation = create(client)
        limited = turn(client, conversation)
        assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"


def test_active_run_rejects_queue_but_not_health(tmp_path):
    started, release = threading.Event(), threading.Event()

    class SlowModel:
        def invoke(self, messages):
            started.set()
            assert release.wait(5)
            return AIMessage(content="Finished")

    with TestClient(create_app(cloud_settings(tmp_path), SlowModel())) as client:
        conversation = create(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(turn, client, conversation)
            try:
                assert started.wait(3)
                assert turn(client, conversation).status_code == 429
                assert client.get("/healthz").status_code == 200
            finally:
                release.set()
            assert first.result().status_code == 200


def test_cors_is_explicit_and_does_not_authorize(tmp_path):
    settings = cloud_settings(tmp_path, cors_allowed_origins="https://demo.streamlit.app")
    with TestClient(create_app(settings, ScriptedModel())) as client:
        accepted = client.options(
            "/conversations",
            headers={
                "Origin": "https://demo.streamlit.app",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-Key,X-Demo-Session",
            },
        )
        assert accepted.status_code == 200
        assert accepted.headers["access-control-allow-origin"] == "https://demo.streamlit.app"
        denied = client.options(
            "/conversations",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert denied.status_code == 400
        assert (
            client.get(
                "/conversations", headers={"Origin": "https://demo.streamlit.app"}
            ).status_code
            == 401
        )


def test_additive_migration_preserves_local_data(tmp_path):
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as c, c:
        c.execute(
            "CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at REAL NOT NULL)"
        )
        c.execute("INSERT INTO conversations VALUES ('old','Saved local investigation',1)")
    repo = Repository(path)
    repo.initialize()
    repo.initialize()
    assert repo.conversation("old")["title"] == "Saved local investigation"
    assert repo.ticket(1)["ticket"]["id"] == 1


def test_budget_atomic_under_concurrent_reservations(tmp_path):
    repo = Repository(tmp_path / "quota.db")
    repo.initialize()

    def reserve():
        try:
            repo.consume_quota("model:test-day", 1)
            return "reserved"
        except QuotaError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == ["blocked", "reserved"]


def test_hosted_wire_contract_and_model_list(tmp_path):
    settings = cloud_settings(tmp_path, max_completion_tokens=1536, hosted_reasoning_effort="low")

    def handler(request):
        assert request.headers["Authorization"] == "Bearer synthetic-provider-credential"
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": settings.llm_model}]})
        payload = json.loads(request.content)
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert payload["max_completion_tokens"] == 1536
        assert payload["reasoning_effort"] == "low"
        assert len(payload["tools"]) == 5
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "Wire contract only"}}]
            },
        )

    model = HTTPChatModel(settings, httpx.MockTransport(handler))
    assert model.check()["available"] is True
    assert (
        model.invoke([HumanMessage(content="Synthetic question")]).content == "Wire contract only"
    )
    with pytest.raises(ModelError, match="context limit"):
        model.invoke([HumanMessage(content="x" * 25000)])
    model.close()
    model = HTTPChatModel(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, json={"data": []}))
    )
    assert model.check()["available"] is False
    model.close()


def test_frontend_sends_secrets_only_in_server_headers(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", "https://backend.example")
    monkeypatch.setenv("SUPPORT_API_KEY", API_KEY)

    def handler(method, url, **kwargs):
        assert url == "https://backend.example/conversations"
        assert kwargs["headers"] == headers()
        assert API_KEY not in url
        assert kwargs["trust_env"] is False
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "request", handler)
    assert frontend_request("GET", "/conversations", session_token=SESSION_A) == []


def test_cloud_frontend_actual_approval_and_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPORT_API_KEY", API_KEY)
    model = ScriptedModel(
        [tool_call("create_ticket", CREATE), AIMessage(content="Approved synthetic write")]
    )
    with TestClient(create_app(cloud_settings(tmp_path), model)) as client:

        def bridge(method, url, json=None, **kwargs):
            return client.request(
                method, httpx.URL(url).raw_path.decode(), json=json, headers=kwargs.get("headers")
            )

        monkeypatch.setattr(httpx, "request", bridge)
        app = AppTest.from_file(FRONTEND, default_timeout=15).run()
        assert not app.exception
        assert any("Disposable demo" in item.value for item in app.warning)
        app.chat_input[0].set_value("Prepare a synthetic ticket").run()
        assert not app.exception and app.chat_input[0].disabled
        next(b for b in app.button if b.label == "Approve this change").click().run()
        assert not app.exception and not app.chat_input[0].disabled
        assert any("Approved synthetic write" in item.value for item in app.markdown)
        other_browser = AppTest.from_file(FRONTEND, default_timeout=15).run()
        assert not other_browser.exception
        assert other_browser.session_state.conversation_id != app.session_state.conversation_id
        assert not any("Approved synthetic write" in item.value for item in other_browser.markdown)


def test_port_launcher_uses_host_port_and_single_worker(monkeypatch):
    from support_agent.serve import main

    captured = {}
    monkeypatch.setattr("support_agent.serve.uvicorn.run", lambda app, **kw: captured.update(kw))
    monkeypatch.setenv("PORT", "10000")
    main()
    assert captured == {"host": "0.0.0.0", "port": 10000, "workers": 1, "access_log": False}
    monkeypatch.setenv("PORT", "invalid")
    with pytest.raises(SystemExit, match="PORT must"):
        main()


def test_frontend_dependencies_are_separate_from_backend():
    root = Path(__file__).resolve().parents[1]
    frontend = (root / "frontend/requirements.txt").read_text()
    backend = (root / "requirements-backend.txt").read_text()
    assert "streamlit==" in frontend and "langgraph==" not in frontend
    assert "langgraph==" in backend and "streamlit==" not in backend


def test_cloud_budget_after_approval_keeps_committed_receipt(tmp_path):
    model = ScriptedModel([tool_call("create_ticket", CREATE)])
    with TestClient(
        create_app(cloud_settings(tmp_path, max_model_calls_per_day=1), model)
    ) as client:
        conversation = create(client)
        pending = turn(client, conversation).json()["pending_approval"]
        result = decide(client, conversation, pending["id"]).json()
        assert result["error"]["code"] == "daily_limit"
        assert result["pending_approval"] is None
        assert result["activity"][0]["result"]["approval"]["status"] == "approved"
        assert (
            client.app.state.repository.ticket(2, conversation)["ticket"]["title"]
            == CREATE["title"]
        )
        assert decide(client, conversation, pending["id"]).status_code == 409


def test_render_refuses_accidental_local_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(ValidationError, match="Render requires APP_MODE=cloud"):
        Settings(
            _env_file=None,
            database_path=tmp_path / "local.db",
            checkpoint_path=tmp_path / "state.db",
        )


@pytest.mark.parametrize(
    "override",
    [
        {"storage_mode": "persistent"},
        {"app_mode": "local"},
    ],
)
def test_reset_profile_cannot_target_local_or_persistent_storage(tmp_path, override):
    with pytest.raises(ValidationError, match="only allowed for cloud ephemeral"):
        cloud_settings(tmp_path, reset_demo_on_start=True, **override)
    with pytest.raises(ValidationError, match="same parent"):
        cloud_settings(
            tmp_path, reset_demo_on_start=True, checkpoint_path=tmp_path / "another" / "graph.db"
        )


def test_reset_uses_fresh_paired_state_and_never_deletes_configured_files(tmp_path):
    settings = cloud_settings(tmp_path, reset_demo_on_start=True)
    legacy = Repository(settings.database_path)
    legacy.initialize()
    legacy_conversation = legacy.create_conversation()["id"]
    settings.checkpoint_path.write_bytes(b"existing checkpoint file must remain untouched")
    with TestClient(
        create_app(settings, ScriptedModel([tool_call("create_ticket", CREATE)]))
    ) as client:
        instance = client.get("/health", headers=headers()).json()["demo_instance_id"]
        first_path = client.app.state.settings.database_path
        assert first_path.parent == client.app.state.settings.checkpoint_path.parent
        assert first_path != settings.database_path
        assert client.get("/conversations", headers=headers()).json() == []
        previous = create(client)
        old_proposal = turn(client, previous).json()["pending_approval"]
    assert not first_path.parent.exists()
    model = ScriptedModel(
        [tool_call("create_ticket", CREATE), AIMessage(content="New boot approved")]
    )
    with TestClient(create_app(settings, model)) as client:
        health = client.get("/health", headers=headers()).json()
        assert health["demo_instance_id"] != instance and health["reset_demo_on_start"]
        assert client.get(f"/conversations/{previous}", headers=headers()).status_code == 404
        assert decide(client, previous, old_proposal["id"]).status_code == 404
        current = create(client)
        proposal = turn(client, current).json()["pending_approval"]
        assert decide(client, current, old_proposal["id"]).status_code == 409
        assert decide(client, current, proposal["id"]).status_code == 200
        assert client.app.state.repository.ticket(2, current)["ticket"]["title"] == CREATE["title"]
    assert legacy.conversation(legacy_conversation)["title"] == "New investigation"
    assert (
        settings.checkpoint_path.read_bytes() == b"existing checkpoint file must remain untouched"
    )


def test_frontend_starts_clean_after_demo_generation_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPORT_API_KEY", API_KEY)
    settings = cloud_settings(tmp_path, reset_demo_on_start=True)
    active = {}

    def bridge(method, url, json=None, **kwargs):
        return active["client"].request(
            method, httpx.URL(url).raw_path.decode(), json=json, headers=kwargs.get("headers")
        )

    monkeypatch.setattr(httpx, "request", bridge)
    with TestClient(
        create_app(settings, ScriptedModel([AIMessage(content="Previous boot private history")]))
    ) as client:
        active["client"] = client
        app = AppTest.from_file(FRONTEND, default_timeout=15).run()
        app.chat_input[0].set_value("Remember a synthetic note").run()
        assert not app.exception
        previous = app.session_state.conversation_id
        assert any("Previous boot private history" in m.value for m in app.markdown)
    with TestClient(create_app(settings, ScriptedModel())) as client:
        active["client"] = client
        app.run()
        assert not app.exception
        assert app.session_state.conversation_id != previous
        assert any("disposable demo restarted" in i.value for i in app.info)
        assert not any("Previous boot private history" in m.value for m in app.markdown)


def test_real_groq_smoke_refuses_wrong_provider_key_or_missing_key(monkeypatch, tmp_path, capsys):
    from scripts import smoke_groq

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-other-provider-key")
    monkeypatch.setattr("sys.argv", ["smoke_groq.py"])
    assert smoke_groq.configured_key() == ""
    assert smoke_groq.main() == 2
    output = capsys.readouterr()
    assert "No provider request or inference was performed" in output.err
    assert "synthetic-other-provider-key" not in output.err + output.out
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-groq-credential")
    assert smoke_groq.configured_key() == "synthetic-groq-credential"


def test_isolated_repository_listing_fails_closed_without_owner(tmp_path):
    from support_agent.db import NotFoundError

    repository = Repository(tmp_path / "scoped.db", isolated=True)
    repository.initialize()
    first = repository.create_conversation(owner_id="synthetic-owner-a")
    repository.create_conversation(owner_id="synthetic-owner-b")
    with pytest.raises(NotFoundError, match="demo session"):
        repository.conversations()
    assert repository.conversations("synthetic-owner-a") == [first]
