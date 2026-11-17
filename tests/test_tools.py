import json

import pytest

from support_agent.db import Repository
from support_agent.tools import ToolService, tool_definitions


@pytest.fixture
def repository(settings):
    value = Repository(settings.database_path)
    value.initialize()
    return value


@pytest.mark.parametrize("category", ["vpn", "wifi", "email", "dns", "account", "application"])
def test_seeded_catalog(repository, category):
    result = repository.lookup(category, "", 3)
    assert result["simulated"] and result["untrusted_reference_data"]
    assert len(result["articles"]) == 2
    assert all(len(a["steps"]) >= 4 and a["category"] == category for a in result["articles"])
    assert repository.status(category)["services"][0]["name"] == category


def test_status_and_seed_idempotence(repository):
    before = repository.ticket(1)
    repository.initialize()
    assert repository.ticket(1) == before
    status = repository.status("all")
    assert len(status["services"]) == 6
    assert next(s for s in status["services"] if s["name"] == "vpn")["status"] == "degraded"


def test_parameterized_queries_and_ranking(repository):
    assert len(repository.lookup("vpn", "'; DROP TABLE tickets; --", 5)["articles"]) == 2
    assert repository.lookup("vpn' OR 1=1 --", "", 3)["articles"] == []
    assert repository.ticket(1)["ticket"]["id"] == 1
    assert "certificate" in repository.lookup("vpn", "certificate", 1)["articles"][0]["title"]


@pytest.mark.parametrize(
    "name,args",
    [
        ("arbitrary_shell", {"command": "whoami"}),
        ("get_ticket", {"ticket_id": "1 OR 1=1"}),
        ("get_ticket", {"ticket_id": True}),
        ("get_system_status", {"service": "payroll"}),
        ("lookup_troubleshooting_steps", {"category": "vpn", "limit": 100}),
        ("create_ticket", {"title": "x", "description": "y", "category": "vpn", "approved": True}),
        ("create_ticket", {"title": " ", "description": "y", "category": "vpn"}),
        ("update_ticket", {"ticket_id": 1}),
        ("update_ticket", {"ticket_id": 1, "status": "deleted"}),
    ],
)
def test_validation_rejects_unsafe_or_invalid_arguments(repository, name, args):
    values, error = ToolService(repository).validate({"name": name, "args": args})
    assert values is None and error["error"] in {"invalid_arguments", "unknown_tool"}


def test_read_dispatcher_cannot_mutate(repository):
    service = ToolService(repository)
    assert service.read("create_ticket", {})["error"] == "approval_required"
    assert service.read("update_ticket", {"ticket_id": 1})["error"] == "approval_required"
    assert service.read("get_ticket", {"ticket_id": 999})["error"] == "not_found"


def test_tool_failure_is_safe(repository, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("password=do-not-log")

    monkeypatch.setattr(repository, "status", broken)
    result = ToolService(repository).read("get_system_status", {"service": "all"})
    assert result["error"] == "tool_unavailable"
    assert "password" not in json.dumps(result)


def test_tools_have_narrow_schemas():
    tools = tool_definitions()
    assert len(tools) == 5
    assert all(t["function"]["parameters"]["additionalProperties"] is False for t in tools)
