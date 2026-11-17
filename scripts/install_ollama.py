"""Install the official Linux x86_64 Ollama runtime locally, with SHA-256 verification.

The CPU libraries are sufficient for this WSL machine; optional GPU libraries are
not extracted. No sudo, shell-profile edits, system services, or home overrides.
"""

import argparse
import hashlib
import json
import platform
import shutil
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

import httpx
import zstandard

VERSION = "0.40.16"
ARCHIVE_SHA256 = "87bfafb2acbc89b2e6a1be0a9043de6aa75fb450696f86bc426e65fc448f788d"
URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-linux-amd64.tar.zst"
ARCHIVE_SIZE = 1545767640


def download_archive(archive: Path, workers: int):
    """Resume verified-size HTTP ranges; the COMPLETE SHA-256 is checked before extraction."""
    prefix = archive.with_suffix(".partial")
    prefix_size = prefix.stat().st_size if prefix.exists() else 0
    if prefix_size > ARCHIVE_SIZE:
        raise SystemExit(
            "Existing partial archive is larger than the official asset; download stopped."
        )
    chunk_dir = archive.parent / (archive.name + ".chunks")
    chunk_dir.mkdir(exist_ok=True)
    chunk_size = 32 * 1024 * 1024
    ranges = [
        (start, min(start + chunk_size, ARCHIVE_SIZE) - 1)
        for start in range(prefix_size, ARCHIVE_SIZE, chunk_size)
    ]

    with httpx.Client(follow_redirects=True, timeout=120, trust_env=False) as client:

        def fetch(part):
            start, end = part
            target = chunk_dir / f"{start}-{end}"
            expected_size = end - start + 1
            if target.exists() and target.stat().st_size == expected_size:
                return target
            temporary = target.with_suffix(".partial")
            for attempt in range(3):
                try:
                    with client.stream(
                        "GET", URL, headers={"Range": f"bytes={start}-{end}"}
                    ) as response:
                        response.raise_for_status()
                        expected_range = f"bytes {start}-{end}/{ARCHIVE_SIZE}"
                        if (
                            response.status_code != 206
                            or response.headers.get("content-range") != expected_range
                        ):
                            raise RuntimeError(
                                "Download server did not honor the requested byte range."
                            )
                        with temporary.open("wb") as output:
                            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                                output.write(chunk)
                    if temporary.stat().st_size != expected_size:
                        raise RuntimeError("Incomplete archive byte range.")
                    temporary.replace(target)
                    return target
                except (httpx.HTTPError, RuntimeError):
                    if attempt == 2:
                        raise RuntimeError(
                            f"Download failed for range {start}-{end}; completed ranges are retained for retry."
                        ) from None
                    time.sleep(attempt + 1)

        print(
            f"Resuming at {prefix_size / 1_000_000:.0f} MB using {workers} connections.", flush=True
        )
        completed = prefix_size
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch, part) for part in ranges]
            for future in as_completed(futures):
                completed += future.result().stat().st_size
                print(
                    f"Downloaded {completed / 1_000_000:.0f} / {ARCHIVE_SIZE / 1_000_000:.0f} MB",
                    flush=True,
                )
    assembled = archive.with_suffix(".assembled")
    with assembled.open("wb") as output:
        if prefix.exists():
            with prefix.open("rb") as source:
                shutil.copyfileobj(source, output)
        for start, end in ranges:
            with (chunk_dir / f"{start}-{end}").open("rb") as source:
                shutil.copyfileobj(source, output)
    assembled.replace(archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--download-workers", type=int, choices=range(1, 9), default=8)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("This installer is for Linux x86_64 (including Ubuntu on WSL2).")
    root = args.root.resolve()
    destination = root / ".local" / "ollama"
    download_dir = root / "data" / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    archive = download_dir / f"ollama-{VERSION}-linux-amd64.tar.zst"
    if not archive.exists():
        print(
            f"Downloading official Ollama {VERSION} (~1.55 GB); CPU files will be extracted.",
            flush=True,
        )
        download_archive(archive, args.download_workers)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != ARCHIVE_SHA256:
        raise SystemExit(
            "Ollama archive checksum mismatch. Installation stopped; no downloaded executable was run."
        )
    print("Archive SHA-256 verified.", flush=True)
    extracted = []
    destination.mkdir(parents=True, exist_ok=True)
    with archive.open("rb") as compressed:
        with zstandard.ZstdDecompressor().stream_reader(compressed) as decompressed:
            with tarfile.open(fileobj=decompressed, mode="r|") as package:
                for member in package:
                    path = PurePosixPath(member.name.removeprefix("./"))
                    if path.is_absolute() or ".." in path.parts:
                        raise SystemExit("Unsafe archive path; installation stopped.")
                    # Keep the binary and top-level CPU/base libraries. CUDA/ROCm/MLX
                    # bundles in subdirectories are unnecessary for this CPU setup.
                    selected = path == PurePosixPath("bin/ollama") or (
                        path.parent == PurePosixPath("lib/ollama")
                        and (
                            ".so" in path.name
                            or path.name in {"llama-server", "ollama_llama_server"}
                        )
                    )
                    if selected:
                        package.extract(member, destination, filter="data")
                        extracted.append(str(path))
    executable = destination / "bin" / "ollama"
    required = [executable, destination / "lib" / "ollama" / "llama-server"]
    if not all(path.is_file() for path in required):
        raise SystemExit(
            "The official archive did not provide both bin/ollama and lib/ollama/llama-server."
        )
    executable.chmod(executable.stat().st_mode | 0o100)
    (destination / "installation.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "source": URL,
                "sha256": digest,
                "files": extracted,
                "profile": "cpu",
                "installed_at": time.time(),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Installed CPU runtime in {destination}", flush=True)
    print("Start: bash scripts/ollama.sh serve")
    print("Pull model in another terminal: bash scripts/ollama.sh pull qwen3:4b-instruct")


if __name__ == "__main__":
    main()
