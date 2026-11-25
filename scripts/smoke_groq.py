"""Read-only REAL Groq -> LangGraph -> SQLite tool -> Groq smoke, before deployment.

Run only in an authorized server runtime with provider-managed environment secrets. It never
prints a credential, publishes a server, approves a ticket, or substitutes a model.
"""

import argparse
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from support_agent.api import create_app
from support_agent.config import Settings

QUESTION = (
    "Is the simulated corporate VPN healthy right now? Check its current service status "
    "and matching incident, then explain the result. Do not create or update tickets."
)


class SmokeFailure(Exception):
    """Safe, application-authored diagnostics; never a raw provider response."""


def configured_key():
    key = os.getenv("GROQ_API_KEY", "")
    if not key and urlsplit(os.getenv("OPENAI_BASE_URL", "")).hostname == "api.groq.com":
        key = os.getenv("OPENAI_API_KEY", "")
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Check account/model-list connectivity without chat generation or tools",
    )
    parser.add_argument("--output", type=Path, default=Path("data/smoke/groq.json"))
    args = parser.parse_args()
    key = configured_key()
    if not key:
        print(
            "BLOCKED: configure the Groq key only in the hosting-provider secret settings and run in an authorized server environment. No provider request or inference was performed.",
            file=sys.stderr,
        )
        return 2
    report = {
        "scope": "Local API/agent with real Groq HTTPS inference; public deployment remains unverified",
        "model": args.model,
        "provider_origin": "https://api.groq.com",
        "question": QUESTION,
        "result": "failed",
    }
    if args.probe_only:
        report["scope"] = (
            "Groq connectivity/model-list probe only; generation and tool calling remain unverified"
        )
    try:
        # All application state is disposable and separate from the developer's DBs.
        with tempfile.TemporaryDirectory(prefix="servicedesk-groq-smoke-") as directory:
            root = Path(directory)
            shared = secrets.token_urlsafe(32)
            settings = Settings(
                _env_file=None,
                app_mode="cloud",
                storage_mode="ephemeral",
                reset_demo_on_start=True,
                llm_provider="openai",
                llm_model=args.model,
                openai_base_url="https://api.groq.com/openai/v1",
                openai_api_key=key,
                support_api_key=shared,
                database_path=root / "support.db",
                checkpoint_path=root / "checkpoints.db",
                model_timeout_seconds=30,
                max_model_steps=3,
                max_tool_calls=3,
                max_model_calls_per_day=3,
                max_completion_tokens=1536,
                max_context_characters=16000,
                hosted_reasoning_effort="low" if args.model.startswith("openai/gpt-oss-") else "",
                cors_allowed_origins="",
            )
            with TestClient(create_app(settings)) as client:
                client.headers.update(
                    {"X-API-Key": shared, "X-Demo-Session": secrets.token_urlsafe(32)}
                )
                probe = client.get("/health/model").json()
                report["model_probe"] = probe
                print("MODEL-LIST PROBE:", json.dumps(probe))
                if not probe.get("available"):
                    raise SmokeFailure(
                        "Configured model is not visible/reachable for this Groq account; inspect the private provider configuration."
                    )
                if args.probe_only:
                    report["result"] = "probe_passed_generation_unverified"
                    print(
                        "PROBE PASSED: model visible; no chat generation or tool execution was tested."
                    )
                    return 0
                conversation = client.post("/conversations")
                if conversation.status_code != 201:
                    raise SmokeFailure("Could not initialize isolated smoke conversation")
                response = client.post(
                    f"/conversations/{conversation.json()['id']}/messages",
                    json={"message": QUESTION},
                )
                if response.status_code != 200:
                    raise SmokeFailure("The local API could not complete the smoke request")
                view = response.json()
                report["trace"] = view
                if view["error"]:
                    raise SmokeFailure("Agent/provider failure: " + view["error"]["code"])
                if view["pending_approval"]:
                    raise SmokeFailure("The model requested a mutation; it was not approved")
                reads = [
                    a
                    for a in view["activity"]
                    if a["tool"] == "get_system_status" and a["status"] == "completed"
                ]
                if not reads or view["usage"]["model_steps"] < 2:
                    raise SmokeFailure(
                        "No completed status tool and follow-up model invocation were observed"
                    )
                services = [s for call in reads for s in call["result"].get("services", [])]
                if not any(s["name"] == "vpn" and s["incident"] == "SIM-VPN-104" for s in services):
                    raise SmokeFailure(
                        "Tool result did not contain the expected synthetic VPN fixture"
                    )
                answers = [
                    m["content"]
                    for m in view["messages"]
                    if m["role"] == "assistant" and not m["application_notice"]
                ]
                if (
                    not answers
                    or "SIM-VPN-104" not in answers[-1]
                    or "degraded" not in answers[-1].lower()
                ):
                    raise SmokeFailure(
                        "Final response did not reflect the retrieved VPN status/incident"
                    )
                with client.app.state.repository.connect() as c:
                    if c.execute("SELECT count(*) FROM tickets").fetchone()[0] != 1:
                        raise SmokeFailure("Unexpected ticket write during a read-only smoke")
                report["result"] = "passed"
                print("REAL GROQ MODEL:", args.model)
                print("TOOL TRACE:", json.dumps(view["activity"], indent=2))
                print("FINAL MODEL RESPONSE:", answers[-1])
                print(
                    "PASS: real Groq tool round trip; no ticket approved or changed. Public deployment is unverified."
                )
        return 0
    except Exception as exc:
        # Deliberately avoid raw transport/startup exception messages and settings.
        report["failure_type"] = type(exc).__name__
        if isinstance(exc, SmokeFailure):
            report["failure"] = str(exc)
            print(str(exc), file=sys.stderr)
        print(
            "SMOKE FAILED. No successful real-provider result is claimed; inspect the saved synthetic trace and private configuration.",
            file=sys.stderr,
        )
        return 1
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print("Evidence saved:", args.output)


if __name__ == "__main__":
    raise SystemExit(main())
