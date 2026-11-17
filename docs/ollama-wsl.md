# Ollama on this Ubuntu/WSL machine

Environment inspected on November 17, 2026:

| Item | Observed |
| --- | --- |
| OS | Ubuntu 26.04.1 LTS on WSL2, x86_64 |
| Kernel | `6.18.33.2-microsoft-standard-WSL2` |
| CPU | Intel Core i5-1245U, 12 logical CPUs visible |
| WSL memory | 7.6 GiB total; about 6.3 GiB available before loading a model |
| Swap | 2 GiB |
| GPU | Host exposes `/dev/dxg`; no `nvidia-smi` or NVIDIA runtime detected |
| Initial Ollama state | No binary on PATH and no server at `127.0.0.1:11434` |
| Model selection | `qwen3:4b-instruct`, Q4_K_M, about 2.5 GB of weights |
| Runtime | Official Ollama `0.40.16`, portable CPU installation |

`qwen3:4b-instruct` is the non-thinking instruct variant and supports native tool calls. It leaves more memory for the OS, Streamlit, FastAPI, and context than the previous 8B default. The WSL GPU bridge alone does not establish that a supported acceleration runtime is available; this installation uses CPU libraries. Model memory usage and latency are measured by the real smoke test below, not inferred from the download size. [Ollama model details](https://ollama.com/library/qwen3:4b-instruct), [hardware support](https://docs.ollama.com/gpu).

## Local installation without sudo

Run from the repository root:

```bash
uv sync --frozen --extra dev
uv run python scripts/install_ollama.py
```

The installer downloads the official Linux x86_64 archive with resumable parallel byte ranges, verifies its complete SHA-256 digest, and extracts the CLI, `llama-server` executable, and CPU libraries into `.local/ollama` (about 77 MB installed). It does not change shell profiles, system services, or global PATH. The archive is approximately 1.55 GB because the upstream archive also includes GPU libraries; this installer only extracts the CPU runtime. It uses the ignored `data/downloads` directory for the download and `.local/ollama/installation.json` for provenance. Partial ranges are reusable across retries; no executable runs before the complete archive checksum passes.

Start Ollama in terminal 1 and leave it running:

```bash
bash scripts/ollama.sh serve
```

In terminal 2, download the selected model:

```bash
bash scripts/ollama.sh pull qwen3:4b-instruct
bash scripts/ollama.sh list
```

The wrapper binds to `127.0.0.1:11434`, stores model weights in ignored `data/ollama/models`, disables Ollama cloud inference, and limits the server to one loaded model and one parallel inference request. The official runtime may create its normal identity key under `~/.ollama`; never commit that private key. Use the wrapper for this portable installation; plain `ollama` will still be absent from PATH.

If you prefer a system installation instead, Ollama's [official Linux instructions](https://docs.ollama.com/linux) provide:

```bash
curl -fsSL https://ollama.com/install.sh -o /tmp/ollama-install.sh
less /tmp/ollama-install.sh
sh /tmp/ollama-install.sh
```

That alternative can request sudo and create a system service. Do not run a second server on port 11434 while the portable server is running. A system installation uses a different default model directory; set `OLLAMA_MODELS` deliberately if you want it to reuse this project's weights.

## Application configuration

The repository's ignored `.env` is configured as follows:

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:4b-instruct
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_NUM_CTX=8192
OLLAMA_NUM_PREDICT=1024
OLLAMA_NUM_THREAD=6
OLLAMA_THINK=auto
MODEL_TIMEOUT_SECONDS=180
MAX_HISTORY_MESSAGES=40
API_BASE_URL=http://127.0.0.1:8000
```

Other database and approval settings retain their existing values. Python settings, `.env.example`, and the Ollama request adapter now expose these resource controls. Temperature zero is passed to native `/api/chat`. With `OLLAMA_THINK=auto`, the thinking option is omitted because the instruct variant does not support thinking; hybrid models can use `true` or `false` explicitly. Hosted-provider settings are not used in this configuration.

In terminal 2, start the backend after the model download finishes:

```bash
uv run uvicorn support_agent.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

In terminal 3:

```bash
uv run streamlit run frontend/app.py --server.address 127.0.0.1
```

Open http://localhost:8501. These processes run inside the same WSL distribution, so the app uses WSL loopback. This configuration does not require exposing Ollama on `0.0.0.0` or connecting to a Windows gateway address.

## Real smoke test

With Ollama and FastAPI running:

```bash
curl -fsS http://127.0.0.1:11434/api/version
curl -fsS http://127.0.0.1:8000/health/model
uv run python scripts/smoke_ollama.py
bash scripts/ollama.sh ps
```

The model receives this natural-language question via the actual FastAPI message endpoint:

> Is the simulated corporate VPN healthy right now? Please check the current service status and tell me whether there is a matching incident. Do not create or update any tickets.

The model has all five tool schemas available and chooses the tool and arguments. LangGraph validates and executes the call, appends its result as a tool message, invokes Ollama again, and persists the final response. The smoke script fails for an unavailable provider, a model error, a missing status tool call, fewer than two model steps, an unexpected ticket proposal, or a missing final model response. It prints actual results and writes the complete API response to ignored `data/smoke/ollama.json`.

## Verified result

The final real smoke test passed with `qwen3:4b-instruct`: **two model invocations, one executed `get_system_status({"service":"vpn"})` tool call, and a clean final answer in 51.46 seconds**. It reported degraded VPN health and incident `SIM-VPN-104`. Ollama reported an 8K context, CPU execution, zero VRAM, and about 3.62 GiB of loaded model/context size. The runner RSS was about 3.67 GiB. Backend and model health checks passed and Streamlit returned HTTP 200. [Full actual trace and response](ollama-smoke.md).

## Diagnostics and limitations

```bash
free -h
bash scripts/ollama.sh list
bash scripts/ollama.sh ps
curl -fsS http://127.0.0.1:11434/api/tags
curl -fsS http://127.0.0.1:8000/health/model
uv run pytest
```

- `Connection refused`: start `bash scripts/ollama.sh serve` and leave that terminal open. WSL shutdown stops a foreground server; start it again afterwards.
- `llama-server binary not found`: current Ollama needs this executable in addition to `bin/ollama` and its shared libraries. The initial portable extractor omitted it; the corrected installer includes it, verifies the cached archive again, and installs it without a second download. `ldd .local/ollama/lib/ollama/llama-server` confirms its libraries resolve.
- Stalled model pull or a falling progress counter: the initial instruct-model pull retried stalled 156 MB parts and reset its progress, although most bytes remained in the sparse partial file. First try interrupting and repeating the normal pull. On this WSL ext4 model store, `scripts/repair_ollama_download.py` can recover missing ranges for the selected instruct model. Stop Ollama with Ctrl+C, run `uv run python scripts/repair_ollama_download.py`, restart with `OLLAMA_NOPRUNE=1 bash scripts/ollama.sh serve` so the recovered blob is not pruned before registration, and repeat `bash scripts/ollama.sh pull qwen3:4b-instruct` to finish registering its small manifest/template layers. The repair refuses to run while the local server port is open and only promotes a blob after its complete digest matches the official registry manifest. This avoids modifying an active download or accepting unverified model bytes.
- Avoid the moving `qwen3:4b` tag for this profile. The actual artifact downloaded during setup identified itself as **Qwen3 4B Thinking 2507** and its template always opened a reasoning block, even with `think=false`. It made a real status tool call, but the full round trip took **259.41 seconds** and leaked reasoning into final content. The final configuration uses the explicit `qwen3:4b-instruct` tag instead. The adapter now omits unsupported thinking options by default and removes leaked reasoning blocks before persisting/displaying an answer.
- Model missing/404: pull the exact `qwen3:4b-instruct` tag using this project's wrapper, so the server and pull command share the same model store.
- Slow cold requests: the first call loads model weights and processes the system prompt/tool schemas. The application allows 180 seconds per model request; a tool round trip contains at least two requests.
- Memory pressure: close other large WSL applications. Lower `OLLAMA_NUM_CTX` to `4096` if necessary and restart FastAPI. A smaller `qwen3:1.7b` is another resource tradeoff, but must pass the same real smoke test before being considered verified.
- Long histories can exceed an 8K model context before the message-count guard is reached. Start a new investigation when answers lose context; there is no token-aware history compaction yet.
- This 4B CPU model can choose tools or arguments incorrectly and needs more verification for complex workflows. Backend approval, validation, and loop limits still apply.
- The selected installation does not enable GPU acceleration. Adding WSL GPU drivers or libraries is a separate configuration change; a Windows-visible GPU does not by itself prove Ollama can use it.
- Service status, troubleshooting records, and tickets are synthetic. The model and tool-calling round trip are real. Hosted inference and Docker are separate from this local WSL configuration.
