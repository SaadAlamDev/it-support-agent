"""Export separate cloud dependency sets from the existing uv.lock, without resolving."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "requirements-backend.txt": ["streamlit"],
    "frontend/requirements.txt": [
        "fastapi",
        "langgraph",
        "langgraph-checkpoint-sqlite",
        "langchain-core",
        "pydantic-settings",
        "zstandard",
    ],
}


def main():
    for target, pruned in TARGETS.items():
        command = [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--no-header",
            "--no-annotate",
        ]
        for package in pruned:
            command.extend(["--prune", package])
        result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
        (ROOT / target).write_text(
            "# Generated from uv.lock by: uv run python scripts/export_requirements.py\n"
            + result.stdout
        )
        print("Exported", target)


if __name__ == "__main__":
    main()
