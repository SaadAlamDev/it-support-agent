"""Repair a stalled, sparse qwen3:4b-instruct download using official byte ranges.

Stop Ollama first. Only missing filesystem extents (plus boundary blocks) are
fetched. The complete blob must match the official manifest SHA-256 before it
is promoted into Ollama's content-addressed store. No model response is mocked.
"""

import errno
import hashlib
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

MODEL = "qwen3:4b-instruct"
REGISTRY = "https://registry.ollama.ai/v2/library/qwen3"


def main():
    with socket.socket() as probe:
        probe.settimeout(2)
        status = probe.connect_ex(("127.0.0.1", 11434))
        if status != errno.ECONNREFUSED:
            raise SystemExit(
                "Stop Ollama before repairing its partial download; the local port must be closed."
            )
    root = Path(__file__).resolve().parents[1]
    with httpx.Client(timeout=120, trust_env=False, follow_redirects=True) as client:
        response = client.get(
            REGISTRY + "/manifests/4b-instruct",
            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
        )
        response.raise_for_status()
        layer = next(
            item
            for item in response.json()["layers"]
            if item["mediaType"] == "application/vnd.ollama.image.model"
        )
        digest = layer["digest"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise SystemExit("Unexpected official model digest.")
        expected_size = layer["size"]
        final = root / "data" / "ollama" / "models" / "blobs" / digest.replace(":", "-")
        partial = final.with_name(final.name + "-partial")
        if not partial.is_file() or partial.stat().st_size != expected_size:
            raise SystemExit(
                "Expected sparse partial blob is missing or has an unexpected size; use the normal Ollama pull command."
            )
        ranges = []
        with partial.open("rb") as source:
            position = 0
            while position < expected_size:
                try:
                    data = os.lseek(source.fileno(), position, os.SEEK_DATA)
                except OSError as exc:
                    if exc.errno != errno.ENXIO:
                        raise
                    data = expected_size
                if data > position:
                    # Filesystem extents are block-granular; include adjacent blocks
                    # so partially written boundary blocks are repaired as well.
                    start, end = max(0, position - 4096), min(expected_size, data + 4096)
                    for offset in range(start, end, 16 * 1024 * 1024):
                        ranges.append((offset, min(end, offset + 16 * 1024 * 1024) - 1))
                if data == expected_size:
                    break
                position = os.lseek(source.fileno(), data, os.SEEK_HOLE)
        needed = sum(end - start + 1 for start, end in ranges)
        print(
            f"Retained model data found. Repairing {needed / 1e6:.1f} MB instead of downloading {expected_size / 1e6:.1f} MB again.",
            flush=True,
        )
        descriptor = os.open(partial, os.O_RDWR)
        try:

            def fetch(part):
                start, end = part
                for attempt in range(3):
                    try:
                        with client.stream(
                            "GET",
                            REGISTRY + "/blobs/" + digest,
                            headers={"Range": f"bytes={start}-{end}"},
                        ) as incoming:
                            incoming.raise_for_status()
                            if (
                                incoming.status_code != 206
                                or incoming.headers.get("content-range")
                                != f"bytes {start}-{end}/{expected_size}"
                            ):
                                raise RuntimeError("Registry did not honor the byte range.")
                            offset = start
                            for chunk in incoming.iter_bytes(chunk_size=1024 * 1024):
                                if offset + len(chunk) > end + 1:
                                    raise RuntimeError(
                                        "Registry byte range exceeded its declared length."
                                    )
                                view = memoryview(chunk)
                                while view:
                                    written = os.pwrite(descriptor, view, offset)
                                    offset += written
                                    view = view[written:]
                            if offset != end + 1:
                                raise RuntimeError("Incomplete model range.")
                        return end - start + 1
                    except (httpx.HTTPError, RuntimeError):
                        if attempt == 2:
                            raise RuntimeError(
                                "Model range repair failed; the partial blob was not promoted."
                            ) from None

            completed = 0
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(fetch, item) for item in ranges]
                for future in as_completed(futures):
                    completed += future.result()
                    print(f"Repaired {completed / 1e6:.1f} / {needed / 1e6:.1f} MB", flush=True)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    with partial.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != digest.split(":")[1]:
        raise SystemExit(
            "Full model SHA-256 did not match; partial blob retained, no model promoted. Use the normal pull command to retry."
        )
    partial.replace(final)
    print("COMPLETE MODEL SHA-256 VERIFIED: " + actual, flush=True)
    print("Restart without pruning the recovered, not-yet-registered blob:")
    print("OLLAMA_NOPRUNE=1 bash scripts/ollama.sh serve")
    print("Then run: bash scripts/ollama.sh pull " + MODEL)


if __name__ == "__main__":
    main()
