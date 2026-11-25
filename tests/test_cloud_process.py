"""Real local HTTP processes with a SCRIPTED provider, never real hosted inference.

One test launches the exact cloud command with an unreachable HTTPS provider.
The second uses APP_MODE=local solely to admit a loopback HTTP model fixture;
API auth, session isolation, quotas, graph execution and approval remain enabled.
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

from tests.conftest import CREATE
from tests.test_cloud_deployment import API_KEY, headers
from tests.test_frontend import FRONTEND

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url, process):
    deadline = time.monotonic() + 20
    with httpx.Client(timeout=0.5, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(
                    "Local deployment process exited during startup; inspect its test log"
                )
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise AssertionError("Local deployment did not become healthy")


def stop(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def environment(tmp_path, **overrides):
    return (
        os.environ
        | {
            "APP_MODE": "cloud",
            "STORAGE_MODE": "ephemeral",
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "openai/gpt-oss-20b",
            "OPENAI_API_KEY": "synthetic-http-fixture-credential",
            "SUPPORT_API_KEY": API_KEY,
            "MODEL_TIMEOUT_SECONDS": "1",
            "DATABASE_PATH": str(tmp_path / "support.db"),
            "CHECKPOINT_PATH": str(tmp_path / "checkpoints.db"),
            "DEMO_ACTIONS_PER_MINUTE": "60",
            "MAX_MODEL_CALLS_PER_DAY": "100",
            "PORT": str(free_port()),
            "CORS_ALLOWED_ORIGINS": "",
            "MAX_CONTEXT_CHARACTERS": "24000",
        }
        | overrides
    )


def start_backend(env, log):
    process = subprocess.Popen(
        [os.getenv("CLOUD_BACKEND_PYTHON", sys.executable), "-m", "support_agent.serve"],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    wait_ready(f"http://127.0.0.1:{env['PORT']}/healthz", process)
    return process


def test_exact_cloud_command_auth_outage_and_restart(tmp_path):
    env = environment(tmp_path, OPENAI_BASE_URL=f"https://127.0.0.1:{free_port()}/v1")
    process = None
    with (tmp_path / "backend.log").open("w") as log:
        try:
            process = start_backend(env, log)
            with httpx.Client(
                base_url=f"http://127.0.0.1:{env['PORT']}",
                headers=headers(),
                trust_env=False,
                timeout=10,
            ) as client:
                assert client.get("/healthz").json() == {"status": "ok"}
                assert client.get("/health", headers={"X-API-Key": "wrong"}).status_code == 401
                conversation = client.post("/conversations").json()["id"]
                result = client.post(
                    f"/conversations/{conversation}/messages", json={"message": "Check the VPN"}
                ).json()
                assert result["error"]["code"] == "model_unavailable"
                assert result["messages"][-1]["application_notice"] is True
                stop(process)
                process = start_backend(env, log)
                assert client.get(f"/conversations/{conversation}").json() == result
        finally:
            stop(process)
    text = (tmp_path / "backend.log").read_text()
    assert API_KEY not in text and env["OPENAI_API_KEY"] not in text


def tool(name, arguments):
    return {
        "content": None,
        "tool_calls": [
            {
                "id": "fixture_call",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def test_local_http_provider_ui_approval_and_process_restart(tmp_path, monkeypatch):
    responses = deque(
        [
            tool("get_system_status", {"service": "vpn"}),
            {"content": "SCRIPTED FIXTURE: degraded VPN, incident SIM-VPN-104."},
            tool("create_ticket", CREATE),
            {"content": "SCRIPTED FIXTURE: rejected, no ticket changed."},
            tool("create_ticket", CREATE),
            {"content": "SCRIPTED FIXTURE: approved ticket created."},
            tool("get_system_status", {"service": "vpn"}),
            {"content": "SCRIPTED FIXTURE: degraded VPN, incident SIM-VPN-104."},
            tool("create_ticket", CREATE),
            {"content": "SCRIPTED FIXTURE: approved CLI ticket created."},
        ]
    )
    received = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(payload)
            self.reply(
                {
                    "choices": [
                        {
                            "finish_reason": "tool_calls"
                            if responses[0].get("tool_calls")
                            else "stop",
                            "message": responses.popleft(),
                        }
                    ]
                }
            )

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    env = environment(
        tmp_path, APP_MODE="local", OPENAI_BASE_URL=f"http://127.0.0.1:{provider.server_port}/v1"
    )
    base = f"http://127.0.0.1:{env['PORT']}"
    for name in ["API_BASE_URL", "SUPPORT_API_KEY"]:
        monkeypatch.setenv(name, base if name == "API_BASE_URL" else API_KEY)
    backend = frontend = None
    with (tmp_path / "services.log").open("w") as log:
        try:
            backend = start_backend(env, log)
            frontend_port = free_port()
            frontend = subprocess.Popen(
                [
                    os.getenv("CLOUD_FRONTEND_PYTHON", sys.executable),
                    "-m",
                    "streamlit",
                    "run",
                    "frontend/app.py",
                    "--server.address=127.0.0.1",
                    f"--server.port={frontend_port}",
                    "--server.headless=true",
                ],
                cwd=ROOT,
                env=env | {"API_BASE_URL": base},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            wait_ready(f"http://127.0.0.1:{frontend_port}/_stcore/health", frontend)
            app = AppTest.from_file(FRONTEND, default_timeout=20).run()
            assert not app.exception
            app.chat_input[0].set_value("Check simulated VPN status").run()
            assert not app.exception
            assert any("SIM-VPN-104" in m.value for m in app.markdown)
            app.chat_input[0].set_value("Prepare a synthetic ticket").run()
            assert app.chat_input[0].disabled
            with closing(sqlite3.connect(env["DATABASE_PATH"])) as c:
                assert c.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
            next(b for b in app.button if b.label == "Reject").click().run()
            assert not app.exception
            app.chat_input[0].set_value("Prepare another synthetic ticket").run()
            assert app.chat_input[0].disabled
            # Restart while paused, before the explicit approval click.
            stop(backend)
            backend = start_backend(env, log)
            app.run()
            assert not app.exception and app.chat_input[0].disabled
            next(b for b in app.button if b.label == "Approve this change").click().run()
            assert not app.exception and not app.chat_input[0].disabled
            assert any("approved ticket created" in m.value for m in app.markdown)
            with closing(sqlite3.connect(env["DATABASE_PATH"])) as c:
                assert c.execute("SELECT count(*) FROM tickets").fetchone()[0] == 2
                assert (
                    c.execute("SELECT count(*) FROM approvals WHERE status='approved'").fetchone()[
                        0
                    ]
                    == 1
                )
                assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            cli_report = tmp_path / "cloud-smoke.json"
            command = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_cloud.py",
                    "--api",
                    base,
                    "--ticket-workflow",
                    "--output",
                    str(cli_report),
                ],
                cwd=ROOT,
                env=env,
                input="approve\n",
                text=True,
                capture_output=True,
                timeout=20,
            )
            assert command.returncode == 0, command.stdout + command.stderr
            assert API_KEY not in command.stdout
            evidence = json.loads(cli_report.read_text())
            assert evidence["result"] == "passed within the recorded scope"
            assert "explicitly approved synthetic ticket committed" in evidence["cases"]
            assert not responses and len(received) == 10
            assert all(len(p["tools"]) == 5 for p in received)
            read = json.loads(received[1]["messages"][-1]["content"])["data"]
            assert read["services"][0]["incident"] == "SIM-VPN-104"
            receipt = json.loads(received[-1]["messages"][-1]["content"])["data"]
            assert receipt["approval"]["status"] == "approved"
            assert receipt["execution_status"] == "completed"
            assert API_KEY not in "\n".join(m.value for m in app.markdown)
        finally:
            stop(frontend)
            stop(backend)
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=3)
    logs = (tmp_path / "services.log").read_text()
    assert API_KEY not in logs and env["OPENAI_API_KEY"] not in logs


def test_exact_cloud_reset_profile_reseeds_both_databases(tmp_path):
    original_app = tmp_path / "support.db"
    original_graph = tmp_path / "checkpoints.db"
    original_app.write_bytes(b"leave the configured application file untouched")
    original_graph.write_bytes(b"leave the configured checkpoint file untouched")
    env = environment(
        tmp_path,
        RESET_DEMO_ON_START="true",
        OPENAI_BASE_URL=f"https://127.0.0.1:{free_port()}/v1",
    )
    process = None
    with (tmp_path / "reset.log").open("w") as log:
        try:
            process = start_backend(env, log)
            with httpx.Client(
                base_url=f"http://127.0.0.1:{env['PORT']}",
                headers=headers(),
                trust_env=False,
                timeout=10,
            ) as client:
                initial = client.get("/health").json()
                assert initial["reset_demo_on_start"] and initial["demo_instance_id"]
                conversation = client.post("/conversations").json()["id"]
                assert client.get(f"/conversations/{conversation}").status_code == 200
                generations = list(tmp_path.glob("servicedesk-demo-*"))
                assert len(generations) == 1
                assert (generations[0] / "support.db").is_file()
                assert (generations[0] / "checkpoints.db").is_file()
                stop(process)
                assert not generations[0].exists()
                process = start_backend(env, log)
                fresh = client.get("/health").json()
                assert fresh["demo_instance_id"] != initial["demo_instance_id"]
                assert client.get(f"/conversations/{conversation}").status_code == 404
                assert client.get("/conversations").json() == []
                new = client.post("/conversations").json()["id"]
                assert new != conversation
                assert client.get(f"/conversations/{new}").json()["messages"] == []
        finally:
            stop(process)
    assert original_app.read_bytes() == b"leave the configured application file untouched"
    assert original_graph.read_bytes() == b"leave the configured checkpoint file untouched"
