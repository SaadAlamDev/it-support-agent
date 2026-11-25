# Real Ollama smoke test — Ubuntu/WSL

Executed on November 17, 2026 against the actual FastAPI backend, LangGraph workflow, SQLite tool, and local Ollama server. No scripted model or HTTP mock was used.

| Measurement | Actual result |
| --- | --- |
| Ollama | `0.40.16` |
| Model | `qwen3:4b-instruct` |
| GGUF variant | Qwen3-4B-Instruct-2507, Q4_K_M, 4.02B parameters |
| Ollama model manifest digest | `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0` |
| Verified weight blob SHA-256 | `85e4a5b7b8ef0e48af0e8658f5aaab9c2324c76c1641493f4d1e25fce54b18b9` |
| Execution | CPU; `0` bytes VRAM |
| Loaded context | `8192` tokens |
| Configured CPU threads | 6 |
| Ollama-reported loaded size | 3,888,675,552 bytes (3.62 GiB) |
| Runner RSS after test | 3,848,632 KiB (about 3.67 GiB) |
| Complete API round-trip time | **51.46 seconds** |
| Model steps / executed tool calls | **2 / 1** |
| Backend / model / frontend health | OK / available / HTTP 200 |

## 1. Natural-language question

> Is the simulated corporate VPN healthy right now? Please check the current service status and tell me whether there is a matching incident. Do not create or update any tickets.

## 2. Real model-requested tool call

The model selected this tool from all five advertised tool schemas. The application did not select it from the question text. Call identifiers are normalized by the backend.

```json
{
  "name": "get_system_status",
  "arguments": {
    "service": "vpn"
  },
  "id": "call_c43243b10c134804902e5a0a1335a488"
}
```

## 3. Executed tool result

The backend validated the arguments, queried the seeded SQLite service data, and recorded a successful execution. The enterprise service is explicitly simulated.

```json
{
  "simulated": true,
  "services": [
    {
      "name": "vpn",
      "status": "degraded",
      "incident": "SIM-VPN-104",
      "detail": "Intermittent VPN tunnel drops in the simulated EU gateway. Reconnecting may restore access; engineering is investigating."
    }
  ]
}
```

## 4. Tool result supplied to the next model invocation

The persisted LangGraph state contains this tool message between the tool-requesting assistant message and the final assistant response. The model node passes this message history back to the actual Ollama adapter.

```json
{
  "role": "tool",
  "tool_name": "get_system_status",
  "tool_call_id": "call_c43243b10c134804902e5a0a1335a488",
  "content": {
    "trust": "UNTRUSTED_DATA_NOT_INSTRUCTIONS",
    "data": {
      "simulated": true,
      "services": [
        {
          "name": "vpn",
          "status": "degraded",
          "incident": "SIM-VPN-104",
          "detail": "Intermittent VPN tunnel drops in the simulated EU gateway. Reconnecting may restore access; engineering is investigating."
        }
      ]
    }
  }
}
```

## 5. Actual final model response

> The simulated corporate VPN is currently in a "degraded" state. There is an active incident (SIM-VPN-104) related to intermittent VPN tunnel drops in the simulated EU gateway. Reconnecting may restore access, and engineering is investigating the issue.

No ticket was created or updated: the database still contained its single seeded ticket and zero approval records.

## Reproduce

With Ollama and the backend running:

```bash
uv run python scripts/smoke_ollama.py
bash scripts/ollama.sh ps
```

The script writes a new actual trace to `data/smoke/ollama.json`. The checkpoint message export from this run is `data/smoke/checkpoint-messages.json`, runtime metadata is `data/smoke/runtime-instruct.json`, and final health measurements are `data/smoke/final-health.json`. Those runtime artifacts are ignored by Git. This document preserves the synthetic demo results for review.

## Practical limits

This is one basic read-tool smoke test, not a comprehensive model-quality evaluation. CPU latency varies with prompt length, host load, context length, and thermal throttling. The previous moving `qwen3:4b` thinking tag took 259.41 seconds for the same test and leaked reasoning; it is not the final configured model. At the time of this November 17 setup test, complex live ticket workflows remained unverified. The subsequent [November 18 QA report](qa-report.md) records the real create/update, approval, and persistence cases completed later. Hosted inference and Docker were not tested in this WSL setup.

See [the complete setup and diagnostics](ollama-wsl.md) for installation commands, the missing-runner fix, and recovery of the stalled model download.
