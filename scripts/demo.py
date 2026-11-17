"""Interactive API smoke/demo client. Requires a running backend and real model."""

import argparse
import json
import sys

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument(
        "--prompt",
        default="ExpenseHub shows HTTP 503. Check status, look up troubleshooting, and prepare a high priority ticket.",
    )
    args = parser.parse_args()
    try:
        with httpx.Client(base_url=args.api, timeout=600, trust_env=False) as client:

            def post(path, body=None):
                response = client.post(path, json=body)
                response.raise_for_status()
                return response.json()

            conversation = post("/conversations")
            path = "/conversations/" + conversation["id"]
            print("Conversation:", conversation["id"])
            result = post(path + "/messages", {"message": args.prompt})
            while True:
                print(json.dumps(result, indent=2))
                if result["error"]:
                    print(
                        "Application error; no successful model answer for this step.",
                        file=sys.stderr,
                    )
                    return 1
                pending = result["pending_approval"]
                if not pending:
                    break
                answer = input(
                    "Approve the EXACT proposal shown above? Type approve, or press Enter to reject: "
                )
                result = post(
                    path + "/approvals/" + pending["id"], {"approved": answer == "approve"}
                )
        return 0
    except (httpx.HTTPError, EOFError) as exc:
        print(
            f"Demo could not complete ({type(exc).__name__}). Check the API and model server; no approval was inferred.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
