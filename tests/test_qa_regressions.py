"""Deterministic QA regressions. Scripted models here are NOT live-LLM evidence."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from streamlit.testing.v1 import AppTest

from support_agent.api import create_app
from support_agent.db import ConflictError, Repository
from support_agent.model import HTTPChatModel, ModelError
from tests.conftest import CREATE, ScriptedModel, decide, send, tool_call
from tests.test_frontend import FRONTEND, attach_api


@pytest.mark.parametrize(
    "payload",
    [
        {"message": []},
        {"message": 42},
        {"message": "invalid"},
        {"message": {"content": "answer", "tool_calls": {}}},
        {"message": {"content": "answer", "tool_calls": False}},
        {
            "message": {
                "content": [],
                "tool_calls": [{"function": {"name": "get_ticket", "arguments": {"ticket_id": 1}}}],
            }
        },
    ],
)
def test_provider_wrong_json_shapes_are_model_errors(settings, payload):
    model = HTTPChatModel(
        settings, httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    )
    try:
        with pytest.raises(ModelError) as caught:
            model.invoke([HumanMessage(content="Check ticket 1")])
        assert caught.value.code == "invalid_model_output"
    finally:
        model.close()


def test_unexpected_api_error_does_not_escape_or_expose_secret(client, monkeypatch, capsys):
    canary = "QA_ONLY_SECRET_DO_NOT_EMIT"

    def failure():
        raise RuntimeError(canary)

    monkeypatch.setattr(client.app.state.repository, "create_conversation", failure)
    # Default TestClient raises if the exception escapes the ASGI application,
    # just as Uvicorn would receive it and log an unsanitized traceback.
    response = client.post("/conversations")
    assert response.status_code == 500
    assert canary not in response.text
    assert canary not in capsys.readouterr().err


@pytest.mark.parametrize("failure_stage", ["insert", "approval_receipt"])
def test_failed_ticket_insert_rolls_back_and_same_approval_can_retry(settings, failure_stage):
    model = ScriptedModel(
        [tool_call("create_ticket", CREATE), AIMessage(content="Approved write completed.")]
    )
    with TestClient(create_app(settings, model), raise_server_exceptions=False) as client:
        conversation = client.post("/conversations").json()["id"]
        proposal = send(client, conversation)["pending_approval"]
        repository = client.app.state.repository
        with repository.connect() as c:
            if failure_stage == "insert":
                c.execute(
                    "CREATE TRIGGER qa_block_insert BEFORE INSERT ON tickets BEGIN SELECT RAISE(ABORT, 'QA_ONLY_WRITE_FAILURE'); END"
                )
            else:
                # Fail AFTER the ticket INSERT, while storing the approval receipt.
                c.execute(
                    "CREATE TRIGGER qa_block_insert BEFORE UPDATE ON approvals WHEN NEW.status='approved' BEGIN SELECT RAISE(ABORT, 'QA_ONLY_WRITE_FAILURE'); END"
                )
        response = decide(client, conversation, proposal)
        assert response.status_code == 500
        assert "QA_ONLY_WRITE_FAILURE" not in response.text
        with repository.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
            assert (
                c.execute("SELECT status FROM approvals WHERE id=?", (proposal["id"],)).fetchone()[
                    0
                ]
                == "pending"
            )
            c.execute("DROP TRIGGER qa_block_insert")
        response = decide(client, conversation, proposal)
        assert response.status_code == 200, response.text
        assert response.json()["activity"][0]["result"]["ticket"]["title"] == CREATE["title"]
        with repository.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 2
        assert decide(client, conversation, proposal).status_code == 409


def test_success_receipt_tells_model_approval_already_completed(client, conversation, model):
    model.responses.extend(
        [tool_call("create_ticket", CREATE), AIMessage(content="Created after approval.")]
    )
    proposal = send(client, conversation)["pending_approval"]
    assert decide(client, conversation, proposal).status_code == 200
    message = next(m for m in model.seen[-1] if isinstance(m, ToolMessage))
    data = json.loads(message.content)["data"]
    assert data.get("execution_status") == "completed"
    assert data.get("approval") == {"id": proposal["id"], "status": "approved"}


def test_approved_write_survives_followup_model_timeout(client, conversation, model):
    model.responses.extend(
        [tool_call("create_ticket", CREATE), ModelError("model_timeout", "Model timed out.")]
    )
    proposal = send(client, conversation)["pending_approval"]
    response = decide(client, conversation, proposal)
    assert response.status_code == 200
    view = response.json()
    assert view["error"]["code"] == "model_timeout" and view["pending_approval"] is None
    assert view["activity"][0]["result"]["ticket"]["id"] == 2
    with client.app.state.repository.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 2
    assert decide(client, conversation, proposal).status_code == 409


def test_replay_uses_immutable_receipt_even_when_ticket_disappears(client, conversation, model):
    model.responses.extend(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "missing_update",
                        "name": "update_ticket",
                        "args": {"ticket_id": 1, "note": "QA observation"},
                    },
                    {"id": "second_proposal", "name": "create_ticket", "args": CREATE},
                ],
            ),
            AIMessage(
                content="The disappeared ticket was not updated; the second change was rejected."
            ),
        ]
    )
    first = send(client, conversation)["pending_approval"]
    with client.app.state.repository.connect() as c:
        c.execute("DELETE FROM tickets WHERE id=1")
    response = decide(client, conversation, first)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["activity"][0]["result"]["error"] == "ticket_conflict"
    assert view["pending_approval"]["id"] != first["id"]
    assert decide(client, conversation, view["pending_approval"], False).status_code == 200


def test_concurrent_decisions_write_exactly_once(settings):
    repository = Repository(settings.database_path)
    repository.initialize()
    conversation = repository.create_conversation()["id"]
    proposal = repository.propose(
        conversation, {"id": str(uuid4()), "name": "create_ticket", "args": CREATE}
    )

    def decision():
        try:
            repository.decide(conversation, proposal["id"], True)
            return "approved"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: decision(), range(2)))
    assert sorted(results) == ["approved", "conflict"]
    with repository.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 2


def test_history_bound_returns_conflict_without_extra_model_call(settings):
    settings.max_history_messages = 10
    model = ScriptedModel([AIMessage(content="Response") for _ in range(5)])
    with TestClient(create_app(settings, model)) as client:
        conversation = client.post("/conversations").json()["id"]
        for _ in range(5):
            send(client, conversation)
        response = client.post(f"/conversations/{conversation}/messages", json={"message": "More"})
        assert response.status_code == 409
        assert len(model.seen) == 5


@pytest.mark.parametrize("status,body", [(200, "<html>not JSON</html>"), (503, "[]")])
def test_frontend_bad_backend_payload_is_a_visible_error(monkeypatch, status, body):
    monkeypatch.setattr(httpx, "request", lambda *a, **kw: httpx.Response(status, text=body))
    app = AppTest.from_file(FRONTEND, default_timeout=15).run()
    assert not app.exception
    assert app.error
    assert not app.chat_input


def test_frontend_reject_and_switch_persisted_conversations(client, model, monkeypatch):
    attach_api(monkeypatch, client)
    model.responses.extend(
        [
            AIMessage(content="Remembering the QA first conversation."),
            tool_call("create_ticket", CREATE),
            AIMessage(content="Rejected; no write."),
        ]
    )
    app = AppTest.from_file(FRONTEND, default_timeout=15).run()
    app.chat_input[0].set_value("QA first conversation").run()
    first = app.session_state.conversation_id
    next(b for b in app.button if b.label == "＋ New investigation").click().run()
    second = app.session_state.conversation_id
    assert first != second
    app.chat_input[0].set_value("Prepare a ticket").run()
    next(b for b in app.button if b.label == "Reject").click().run()
    assert not app.exception and not app.chat_input[0].disabled
    with client.app.state.repository.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    app.selectbox[0].select(first).run()
    assert app.session_state.conversation_id == first
    assert any("Remembering the QA first conversation." in m.value for m in app.markdown)
