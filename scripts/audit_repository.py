"""Check tracked/non-ignored portfolio files without displaying potential secrets.

This is a limited working-tree audit, not DLP or a Git history scanner.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {
    ".aws",
    ".codex",
    ".agents",
    ".ssh",
    ".ollama",
    ".local",
    ".venv",
    "data",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".mypy_cache",
    ".pyright",
    ".tox",
    ".nox",
    ".hypothesis",
    "logs",
    ".idea",
    ".vscode",
    "htmlcov",
    "build",
    "dist",
}
PRIVATE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".pyc",
    ".log",
    ".gguf",
    ".safetensors",
}
PATTERNS = {
    "Groq-style key": re.compile(rb"\bgsk_[A-Za-z0-9]{40,}\b"),
    "private key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "OpenAI-style key": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b"),
    "credential URL": re.compile(rb"https?://[^\s/:@]+:[^\s/@]+@[^\s/]+"),
}
IGNORE_PROBES = [
    ".env",
    ".env.production",
    ".aws/credentials",
    ".codex/config.toml",
    ".agents/private.txt",
    ".venv/bin/python",
    ".local/ollama/bin/ollama",
    ".ollama/id_ed25519",
    "data/support.db",
    "data/checkpoints.db-wal",
    "example.sqlite3",
    "private.pem",
    "private.key",
    ".ssh/id_ed25519",
    ".streamlit/secrets.toml",
    "model.gguf",
    "__pycache__/app.pyc",
    ".pytest_cache/state",
    ".coverage",
    "coverage.json",
    "app.log",
]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def forbidden_path(name):
    path = Path(name)
    if name == ".env.example":
        return False
    return (
        bool(set(path.parts) & PRIVATE_DIRS)
        or path.name == ".env"
        or path.name.startswith(".env.")
        or path.suffix in PRIVATE_SUFFIXES
        or ".db-" in path.name
        or path.name
        in {
            "id_rsa",
            "id_ed25519",
            "id_ecdsa",
            ".coverage",
            "coverage.json",
            "coverage.xml",
            ".DS_Store",
            "Thumbs.db",
        }
        or path.name.startswith(("credentials", "secrets", ".coverage."))
        or path.name.endswith((".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm"))
    )


def main():
    candidates = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    names = sorted({p.decode() for p in candidates.split(b"\0") if p})
    findings = []
    scanned = 0
    for name in names:
        if forbidden_path(name):
            findings.append((name, "private/runtime path is tracked or not ignored"))
            continue
        path = ROOT / name
        # Symlinks must never cause the scanner to follow files outside the repo.
        if path.is_symlink():
            findings.append((name, "symlink requires manual review"))
            continue
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue  # Actual images/assets are reviewed visually.
        scanned += 1
        for label, pattern in PATTERNS.items():
            if pattern.search(raw):
                findings.append((name, label))
    for probe in IGNORE_PROBES:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", probe], cwd=ROOT, check=False
        )
        if result.returncode != 0:
            findings.append((probe, "expected ignore rule is missing"))
    for name, reason in findings:
        print(f"REVIEW: {name}: {reason}")
    print(
        f"Checked {len(names)} candidate paths; scanned {scanned} text files; {len(findings)} findings."
    )
    print(
        f"Checked {len(IGNORE_PROBES)} private/runtime ignore probes. Binary assets require visual review."
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
