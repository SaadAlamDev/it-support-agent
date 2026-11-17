from collections import deque

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from support_agent.api import create_app
from support_agent.config import Settings


class ScriptedModel:
    def __init__(self, responses=()):
        self.responses = deque(responses)
        self.seen = []

    def invoke(self, messages):
        self.seen.append(messages)
        if not self.responses:
            raise AssertionError("Unexpected model invocation")
        value = self.responses.popleft()
        if isinstance(value, Exception):
            raise value
        return value


def tool_call(name, args, call_id="call_test"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


CREATE = {
    "title": "ExpenseHub unavailable",
    "description": "Synthetic HTTP 503 failure after one retry.",
    "category": "application",
    "priority": "high",
}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None, database_path=tmp_path / "support.db", checkpoint_path=tmp_path / "graph.db"
    )


@pytest.fixture
def model():
    return ScriptedModel()


@pytest.fixture
def client(settings, model):
    with TestClient(create_app(settings, model)) as value:
        yield value


@pytest.fixture
def conversation(client):
    response = client.post("/conversations")
    assert response.status_code == 201
    return response.json()["id"]


def send(client, conversation, text="Investigate the issue"):
    response = client.post(f"/conversations/{conversation}/messages", json={"message": text})
    assert response.status_code == 200, response.text
    return response.json()


def decide(client, conversation, proposal, approved=True):
    return client.post(
        f"/conversations/{conversation}/approvals/{proposal['id']}", json={"approved": approved}
    )
