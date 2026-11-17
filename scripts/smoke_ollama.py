"""Real API → LangGraph → Ollama → tool → Ollama smoke test; no fallback model.

Requires the running FastAPI backend and Ollama. Fails unless the real model
requests get_system_status, a successful result is observed, and a final model
response follows. It never grants ticket approval.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

QUESTION = (
    "Is the simulated corporate VPN healthy right now? "
    "Please check the current service status and tell me whether there is a matching incident. "
    "Do not create or update any tickets."
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("data/smoke/ollama.json"))
    args = parser.parse_args()
    started = time.monotonic()
    try:
        with httpx.Client(base_url=args.api, timeout=1200, trust_env=False) as client:
            health = client.get("/health")
            health.raise_for_status()
            health = health.json()
            if health["provider"] != "ollama":
                raise ValueError(
                    "Backend is not configured for Ollama; refusing to test a different provider."
                )
            response = client.post("/conversations")
            response.raise_for_status()
            conversation_id = response.json()["id"]
            print(f"MODEL: {health['model']}\nQUESTION: {QUESTION}", flush=True)
            response = client.post(
                f"/conversations/{conversation_id}/messages", json={"message": QUESTION}
            )
            response.raise_for_status()
            result = response.json()
            evidence = {
                "provider": "ollama",
                "model": health["model"],
                "question": QUESTION,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "result": result,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, indent=2) + "\n")
            if result["error"]:
                raise ValueError(
                    f"Backend model error: {result['error']['code']}: {result['error']['message']}"
                )
            if result["pending_approval"]:
                raise ValueError("Unexpected mutation proposal; the smoke test did not approve it.")
            status_calls = [
                r
                for r in result["activity"]
                if r["tool"] == "get_system_status" and r["status"] == "completed"
            ]
            if not status_calls or result["usage"]["model_steps"] < 2:
                raise ValueError(
                    "No successful real model-requested status tool call and follow-up model step were observed."
                )
            answers = [
                m
                for m in result["messages"]
                if m["role"] == "assistant" and not m["application_notice"]
            ]
            if not answers:
                raise ValueError("No final model response was generated.")
            for index, record in enumerate(result["activity"], 1):
                print(f"\nTOOL {index}: {record['tool']}")
                print("ARGUMENTS:", json.dumps(record["arguments"]))
                print("RESULT:", json.dumps(record["result"], indent=2))
            print("\nFINAL MODEL RESPONSE:\n" + answers[-1]["content"])
            print(
                f"\nPASS: {result['usage']['model_steps']} model steps, {len(result['activity'])} tool calls, {evidence['elapsed_seconds']} seconds."
            )
            print(f"Saved actual trace: {args.output}")
        return 0
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        print(f"SMOKE TEST FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
