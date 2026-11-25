"""Cloud/local backend launcher: one worker, provider PORT, no access payload logs."""

import os

import uvicorn


def main():
    # Validate only the non-secret port; application settings validate at startup.
    try:
        port = int(os.getenv("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit("PORT must be an integer from 1 to 65535") from None
    uvicorn.run("support_agent.api:app", host="0.0.0.0", port=port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
