"""Verify a deployed API's model/tool round trip, with an optional human-reviewed ticket.

Requires SUPPORT_API_KEY in the environment and an explicit backend URL. Never
reads or prints the hosted model key. Does not certify the public Streamlit UI.
"""

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

QUESTION = "Is the simulated corporate VPN healthy? Check its current service status and matching incident. Do not create or update tickets."


def healthy(view):
    if view.get("error"):
        raise ValueError("The backend reported a model/agent error: " + view["error"]["code"])
    if view.get("recoverable"):
        raise ValueError("The graph run was interrupted; inspect saved state before continuing")


def final_answer(view):
    answers = [
        m["content"]
        for m in view["messages"]
        if m["role"] == "assistant" and not m["application_notice"]
    ]
    if not answers:
        raise ValueError("No completed model response was observed")
    return answers[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api", required=True, help="Explicit HTTPS backend URL, or loopback for local tests"
    )
    parser.add_argument(
        "--ticket-workflow",
        action="store_true",
        help="Request a synthetic proposal, then prompt for exact human approval",
    )
    parser.add_argument("--output", type=Path, default=Path("data/smoke/cloud.json"))
    args = parser.parse_args()
    url = urlsplit(args.api)
    if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        parser.error("Use an origin URL without embedded credentials, path, query, or fragment")
    if url.scheme != "https" and not (
        url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost")
    ):
        parser.error("Use HTTPS except for loopback testing")
    key = os.getenv("SUPPORT_API_KEY", "")
    if len(key) < 32:
        parser.error("Set SUPPORT_API_KEY privately in your environment (at least 32 characters)")
    report = {
        "scope": "Backend HTTP model/tool workflow; public UI and actual provider provenance require separate verification",
        "cases": [],
        "traces": [],
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")

    try:
        session = secrets.token_urlsafe(32)
        with httpx.Client(
            base_url=args.api.rstrip("/"),
            headers={"X-API-Key": key, "X-Demo-Session": session},
            timeout=210,
            trust_env=False,
            follow_redirects=False,
        ) as client:

            def request(method, path, body=None):
                response = client.request(method, path, json=body)
                if response.status_code >= 400:
                    raise ValueError(
                        f"Backend request failed (HTTP {response.status_code}); inspect saved state and service logs"
                    )
                return response.json()

            health = request("GET", "/health")
            if health["provider"] != "openai" or not health.get("session_isolation"):
                raise ValueError(
                    "Expected a protected backend using the hosted Chat Completions adapter"
                )
            report["model"] = health["model"]
            report["storage_mode"] = health["storage_mode"]
            conversation = request("POST", "/conversations")["id"]
            path = f"/conversations/{conversation}"
            view = request("POST", path + "/messages", {"message": QUESTION})
            report["traces"].append(view)
            save()
            healthy(view)
            calls = [
                a
                for a in view["activity"]
                if a["tool"] == "get_system_status" and a["status"] == "completed"
            ]
            if not calls or view["usage"]["model_steps"] < 2 or view["pending_approval"]:
                raise ValueError(
                    "Expected an executed status tool and follow-up model step without a mutation"
                )
            services = [s for call in calls for s in call["result"].get("services", [])]
            if not any(s["name"] == "vpn" and s["incident"] == "SIM-VPN-104" for s in services):
                raise ValueError("Status tool did not return the expected synthetic VPN fixture")
            answer = final_answer(view)
            if "SIM-VPN-104" not in answer or "degraded" not in answer.lower():
                raise ValueError("The answer did not reflect the retrieved VPN incident/status")
            print("MODEL:", health["model"])
            print("ACTUAL API TOOL TRACE:", json.dumps(view["activity"], indent=2))
            print("FINAL API MODEL RESPONSE:", answer)
            report["cases"].append("status tool executed and final answer grounded")
            other = {"X-API-Key": key, "X-Demo-Session": secrets.token_urlsafe(32)}
            if client.get(path, headers=other).status_code != 404:
                raise ValueError("Another demo session could access the investigation")
            report["cases"].append("cross-session conversation access denied")
            if args.ticket_workflow:
                view = request(
                    "POST",
                    path + "/messages",
                    {
                        "message": "Prepare a new synthetic medium-priority VPN ticket titled 'Cloud smoke VPN' describing simulated tunnel disconnects. Call create_ticket to register the proposal, then pause for my review."
                    },
                )
                report["traces"].append(view)
                save()
                healthy(view)
                proposal = view["pending_approval"]
                if not proposal or proposal["tool"] != "create_ticket":
                    raise ValueError("The model did not register a create_ticket proposal")
                print("REVIEW EXACT SYNTHETIC PROPOSAL:")
                print(json.dumps(proposal["arguments"], indent=2))
                decision = (
                    input("Type approve to approve these exact fields; otherwise reject: ")
                    == "approve"
                )
                approval_path = path + "/approvals/" + proposal["id"]
                if (
                    client.post(approval_path, headers=other, json={"approved": True}).status_code
                    != 404
                ):
                    raise ValueError("Cross-session approval was not denied")
                view = request("POST", approval_path, {"approved": decision})
                report["traces"].append(view)
                save()
                # An approved receipt remains committed even if the summary model fails.
                receipts = [
                    a["result"]
                    for a in view["activity"]
                    if a["tool"] == "create_ticket" and a["call_id"] == proposal["call_id"]
                ]
                if not receipts:
                    raise ValueError("No decision receipt was recorded")
                if decision:
                    if not any(
                        r.get("execution_status") == "completed"
                        and r.get("approval", {}).get("status") == "approved"
                        for r in receipts
                    ):
                        raise ValueError("No committed approved ticket receipt")
                    report["cases"].append("explicitly approved synthetic ticket committed")
                else:
                    if receipts[-1].get("error") != "approval_rejected":
                        raise ValueError("Rejection receipt missing")
                    report["cases"].append(
                        "synthetic ticket proposal rejected; approval flow remains unverified"
                    )
                if client.post(approval_path, json={"approved": decision}).status_code != 409:
                    raise ValueError("A duplicate approval decision was not rejected")
                healthy(view)
                print("DECISION TRACE:", json.dumps(view["activity"], indent=2))
                print("FINAL API MODEL RESPONSE:", final_answer(view))
                report["cases"].append("replayed approval rejected")
            report["result"] = "passed within the recorded scope"
            save()
            print(
                "PASS: backend workflow checks. Public frontend and provider authenticity must be verified separately."
            )
            print("Evidence saved:", args.output)
        return 0
    except (httpx.HTTPError, ValueError, KeyError, EOFError) as exc:
        # Never format raw transport exceptions: URLs/headers may contain secrets.
        message = (
            str(exc)
            if isinstance(exc, ValueError) and not isinstance(exc, httpx.HTTPError)
            else type(exc).__name__
        )
        report["result"] = "failed"
        report["failure"] = message
        save()
        print("SMOKE FAILED:", message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
