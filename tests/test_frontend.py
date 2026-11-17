"""Exercise the actual Streamlit UI against the FastAPI app, with only the LLM mocked."""

from pathlib import Path

import httpx
from langchain_core.messages import AIMessage
from streamlit.testing.v1 import AppTest

from tests.conftest import CREATE, tool_call

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


def attach_api(monkeypatch, client):
    def request(method, url, json=None, **kwargs):
        return client.request(method, httpx.URL(url).raw_path.decode(), json=json)

    monkeypatch.setattr(httpx, "request", request)


def test_chat_activity_and_approval_controls(client, model, monkeypatch):
    attach_api(monkeypatch, client)
    model.responses.extend(
        [
            tool_call("create_ticket", CREATE),
            AIMessage(content="Created the simulated ticket after approval."),
        ]
    )
    app = AppTest.from_file(FRONTEND, default_timeout=15).run()
    assert not app.exception
    assert app.chat_input
    app.chat_input[0].set_value("Prepare a ticket for ExpenseHub").run()
    assert not app.exception
    assert any("approval is required" in warning.value for warning in app.warning)
    assert app.chat_input[0].disabled
    approve = next(button for button in app.button if button.label == "Approve this change")
    approve.click().run()
    assert not app.exception
    assert not app.chat_input[0].disabled
    assert any(
        "Created the simulated ticket after approval." in text.value for text in app.markdown
    )
    with client.app.state.repository.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 2


def test_unavailable_backend_is_clear(monkeypatch):
    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("private-host")

    monkeypatch.setattr(httpx, "request", unavailable)
    app = AppTest.from_file(FRONTEND, default_timeout=15).run()
    assert not app.exception
    assert any("Cannot reach the backend" in error.value for error in app.error)
    assert not app.chat_input
