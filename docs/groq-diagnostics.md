# Diagnosing Groq connectivity on Render

This guide applies to the OpenAI-compatible adapter, not the OpenAI SDK or Ollama. No successful live Groq request or public end-to-end test is claimed here. The deployed service's actual environment/logs cannot be inferred from the repository alone.

## What the original error establishes

The text “Cannot reach the model server. Start Ollama…” in commit `b67eda1` is emitted for **any `httpx.RequestError`**, regardless of configured provider. It does not prove Ollama was selected. Timeouts are handled separately as `model_timeout`, HTTP rejections as `model_provider_error`, and invalid/truncated responses as `invalid_model_output`.

It is an application notice from the backend, rather than the frontend's “Cannot reach the backend” HTTP error. An HTTP 200 `request_completed` log means FastAPI completed the request; it does not prove model inference succeeded. Also, a saved notice may refer to an earlier request. Retry with a new read-only message after configuration changes.

Local defaults are `LLM_PROVIDER=ollama` and `LLM_MODEL=qwen3:4b-instruct`. However, the current code requires `APP_MODE=cloud` on Render and rejects Ollama in that mode. A healthy service actually running this commit with its cloud profile cannot silently fall back to Ollama. An older commit, a different backend URL in Streamlit, or a manually configured service can explain apparent default-provider behavior; inspect the runtime first. There is no automatic hosted-to-Ollama fallback.

## 1. Check the deployed code and runtime

In Render, select the backend **Web Service**, then inspect its latest successful Deploy and commit. The previously pushed deployment commit was `b67eda10ce07dd3cc1ca9eb1cb9cc3debd7c3249`. A local patch is not automatically deployed or pushed. Confirm Streamlit's private `API_BASE_URL` points to that service's exact HTTPS origin.

The frontend sidebar displays the provider/model from authenticated `/health`. Expect **Provider: openai** and **openai/gpt-oss-20b**. Here `openai` means the compatible protocol; it is not the name of the company serving the model. Do not use `LLM_PROVIDER=groq`, which is not a supported configuration value.

`render.yaml` is not a runtime configuration file that Python reads. It configures a service only when applied as a Render Blueprint. Creating a Web Service manually requires setting the environment variables yourself.

## 2. Correct the Render environment, not Streamlit's model settings

In **Render → backend service → Environment**, verify:

| Variable | Required value |
| --- | --- |
| `APP_MODE` | `cloud` |
| `LLM_PROVIDER` | `openai` |
| `LLM_MODEL` | `openai/gpt-oss-20b` |
| `OPENAI_BASE_URL` | `https://api.groq.com/openai/v1` |
| `OPENAI_API_KEY` | Your Groq key, entered privately in Render |
| `HOSTED_REASONING_EFFORT` | `low` |
| `MAX_COMPLETION_TOKENS` | `1536` |
| `MODEL_TIMEOUT_SECONDS` | `30` |
| `LOG_LEVEL` | `INFO` |

Keep `SUPPORT_API_KEY`, isolation, approval, storage and reset settings as configured. These do not fix an outbound model connection. Do not change cloud mode to local, remove authentication, disable TLS verification, or bypass approval as troubleshooting shortcuts.

Use the base URL **without `/chat/completions`**: the adapter appends that path, producing `https://api.groq.com/openai/v1/chat/completions`. An incorrect path usually produces HTTP 404, whereas a wrong/unresolvable hostname or connection failure can produce the reported transport error.

The backend reads `OPENAI_API_KEY`. Setting only `GROQ_API_KEY` does not configure this adapter; that alias is recognized only by the standalone Groq smoke helper. Setting keys only in Streamlit does not configure Render. Keep the provider key solely in Render; Streamlit needs only its backend URL and shared access key. Do not print either key or paste it into chat.

Choose **Save and deploy** or **Save, rebuild, and deploy**. **Save only** leaves the running service on its previous environment until the next deploy. Use a fresh investigation after rollout; the existing resettable free profile deliberately discards synthetic state on boot. [Render environment settings](https://render.com/docs/configure-environment-variables), [Groq base URL](https://console.groq.com/docs/openai).

## 3. Request compatibility

The hosted request includes `model`, `messages`, `stream=false`, five function `tools`, `max_completion_tokens`, and optional `reasoning_effort`. The Groq documentation supports `max_completion_tokens` and `low` reasoning for GPT OSS. The adapter does not send Ollama's `options`/`think`, unsupported `logprobs`/`logit_bias`/`top_logprobs`, or `messages[].name`. Hosted tool results use `tool_call_id`, matching the preceding assistant tool-call ID.

Groq documents sequential function calling for `openai/gpt-oss-20b`, not parallel tool calling. The existing graph supports sequential model → tool → model execution. No request-format incompatibility was identified for the documented configuration. Account/model availability and actual tool generation still require a live test. [Groq API](https://console.groq.com/docs/api-reference), [tool matrix](https://console.groq.com/docs/tool-use/overview).

## 4. Interpret safe Render logs

The original commit logs `model_failed` with `error_type=model_unavailable`, but suppresses the underlying transport type. Those original logs alone cannot distinguish DNS, TLS, refusal, or protocol errors.

The diagnostics patch adds `model_configured`, `model_transport_failed`, and `model_http_failed`. It logs fixed endpoint labels and typed/classified failures only. It does not log configured URLs, key values, prompts, headers, response bodies, or raw exception text. Deploy the reviewed patch before expecting these events.

An **illustrative format, not an observed live failure**, is:

```json
{"event":"model_transport_failed","provider":"openai","model_endpoint":"groq","key_configured":true,"transport_error":"ConnectError","failure_reason":"dns_error"}
```

Search Render Logs for `model_configured`, `model_transport_failed`, `model_http_failed`, then the adjacent `model_failed` event. `key_configured=true` means present, not valid.

| Safe field | Interpretation / next check |
| --- | --- |
| `model_endpoint=ollama_loopback` | Ollama is selected; check deployed commit/profile/provider. Do not start a personal-computer Ollama server to fix a Groq deployment. |
| `model_endpoint=openai` or `hosted_other` | Target does not match the intended Groq endpoint. Recheck `OPENAI_BASE_URL` privately. |
| `model_endpoint=groq_wrong_base_path` | Groq hostname matches, but the base path is incorrect. |
| `failure_reason=dns_error` | The typed cause is DNS resolution failure. Verify the hostname, then investigate Render's outbound resolver/networking. |
| `tls_certificate_error` / `tls_error` | Certificate, hostname, trust-store, or TLS handshake failure. Investigate the platform/CA configuration; never set `verify=False`. |
| `connection_refused` / `network_unreachable` | Wrong destination/port or network reachability problem. A loopback Ollama endpoint is a common configuration mistake; logs are needed to distinguish it. |
| `connection_error` | HTTPX reported connection failure but exposed no more specific typed cause. The code deliberately does not parse or print raw exception messages. |
| `proxy_error` / `protocol_error` | Investigate proxy/protocol connectivity. The client intentionally uses `trust_env=False`; do not blindly enable proxy environment handling. |
| `timeout` | A separate network-operation timeout, not this original `model_unavailable` message. |
| `provider_status_code=401` | Provider rejected authentication; recheck the key privately. |
| `403` | Provider denied access; key/model permissions, account eligibility, or network/region policy may be involved. |
| `404` | Recheck base path and model ID. |
| `400` / `422` | Provider reached but rejected the request or generated tool call. Check its API/model contract and private provider dashboard. This is not a DNS/connection failure. |
| `429` | Provider request/token quota exhausted; wait/check limits rather than disabling application safeguards. |

Do not use `printenv`, `env`, full `Settings.model_dump()`, HTTP debug traces, `curl -v` with authentication, or raw provider-error dumps to collect diagnostics. Share only the safe fields above, the deployed commit, and public service URLs.

## 5. Safe read-only checks

### On Render Free, use the existing public frontend

Free Render does not provide an interactive shell or one-off jobs. Do not upgrade merely to run a command. [Render Free restrictions](https://render.com/docs/free).

1. Confirm the sidebar provider/model, then click **Check model server**. This performs an authenticated model-list/account-visibility probe, not generation. Inspect the sanitized backend logs if it fails.
2. Start a new investigation and submit:

   > Is the simulated corporate VPN healthy? Check its current service status and matching incident, then explain the result. Do not create or update tickets.

3. Require a completed **`get_system_status`** activity, an actual result containing VPN `degraded` / `SIM-VPN-104`, and a subsequent model answer reflecting that evidence. A text-only answer without the tool trace is not a successful tool-calling smoke test. Do not approve any mutation if the model unexpectedly proposes one.
4. Save the actual result and sanitized failure fields if it fails. A successful model-list probe does not replace this test.

### Optional commands in an already authorized server runtime

Only where a shell/runtime is already available and secrets are already injected by the hosting provider:

```bash
# Account/model-list visibility only: no chat generation and no tool execution.
python -m scripts.smoke_groq --probe-only --output data/smoke/groq-probe.json
# Actual Groq -> LangGraph -> SQLite status tool -> Groq, at most 3 model calls.
python -m scripts.smoke_groq --output data/smoke/groq.json
```

Both scripts are in the backend image. The helper uses the existing compatible adapter and a separate temporary synthetic database/checkpoint pair. It never approves tickets and verifies that only the seeded ticket remains. The probe result is explicitly `probe_passed_generation_unverified`; it must not be reported as real generation/tool-call success.

An optional configuration check prints only validated modes and booleans, never a key or configured URL:

```bash
python - <<'PY'
import json
from support_agent.config import Settings
s = Settings()
print(json.dumps({
    "APP_MODE": s.app_mode,
    "LLM_PROVIDER": s.llm_provider,
    "groq_base_exact": s.openai_base_url.rstrip("/") == "https://api.groq.com/openai/v1",
    "model_is_gpt_oss_20b": s.llm_model == "openai/gpt-oss-20b",
    "OPENAI_API_KEY_configured": bool(s.openai_api_key.get_secret_value()),
    "timeout_seconds": s.model_timeout_seconds,
}))
PY
```

No Render environment values, account keys, public URLs, or live logs were provided during this code review. The reproducible code defects were misleading hosted-provider guidance and insufficient sanitized transport diagnostics. They were fixed and tested with mocked transports; the actual deployed connection failure remains undiagnosed until runtime evidence is obtained. No authentication, approval, database, or provider-routing semantics were changed.

## Local verification of this patch

The original misleading hosted-provider message was reproduced by a failing mocked DNS regression before the fix. After the fix, 44 focused model/logging/diagnostic tests passed; the full suite passed **136 tests, zero failures**. The tests cover DNS, TLS certificate/TLS errors, refusal, unreachable networks, timeouts, proxy/protocol failures, sanitized HTTP status logging, supported Groq wire fields, and probe-only behavior without generation. Secret canaries are absent from logs, messages, and probe evidence. Providers in these tests are mocked/scripted or intentionally unavailable; none is a successful live Groq test.

Ruff lint/format and the repository pattern audit passed. These checks do not establish that the patch is deployed or that Render settings have been changed. No authentication, approval, configuration defaults, provider routing, or storage semantics changed.
