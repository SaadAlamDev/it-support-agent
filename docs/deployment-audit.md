# Deployment readiness audit and local verification

Initial audit: November 20, 2026; refreshed November 25, 2026. Scope: the current working tree, including the existing uncommitted portfolio and QA changes, plus the cloud changes described here. No remote GitHub state or public deployment success is assumed.

## November 25 refresh

The current repository already contained the November 20 cloud access controls, scoped tickets/conversations, usage limits, hosted adapter, dependency split, Render port launcher, health check, and approval tests. They were inspected and retained rather than reimplemented.

New changes stay within that architecture:

- `RESET_DEMO_ON_START=true` in the free blueprint creates a fresh paired application/checkpoint sandbox each boot. It is rejected for local or persistent modes. Existing configured database files are not deleted, opened, or overwritten by a reset.
- An opaque non-secret boot ID lets the frontend discard stale conversation state and display an explicit reset notice. Old proposal IDs cannot mutate the new generation. There is no public reset endpoint.
- Isolated repository listing itself now fails closed if no owner is provided, supplementing the existing API ownership check.
- The optional `scripts/smoke_groq.py` runs a bounded real read-only Groq check only in an approved runtime with provider-managed secrets. It never loads a key file, approves a ticket, or substitutes a model. Both smoke scripts are included in the backend image for authorized operator use; Free Render has no shell, so the public Streamlit workflow remains the free acceptance path.
- Secrets instructions consistently keep real keys in hosting-provider secret settings. No real credential was configured, printed, or used during this refresh.

Groq's official model/tool/rate-limit documentation was checked again on November 25: `openai/gpt-oss-20b` remains the configured sequential-tool candidate. No Groq credential or existing public deployment URL was available in this session. **No real Groq inference or public end-to-end success is claimed.** Documented free-plan limits are not an account entitlement or a guarantee of zero charges. [Models](https://console.groq.com/docs/models), [tool support](https://console.groq.com/docs/tool-use/overview), [limits](https://console.groq.com/docs/rate-limits).

The November 20 table below is historical. Updated November 25 local test evidence is recorded in the refresh results section.

## Repository inspection

Reviewed the Python package, Streamlit entrypoint/client/components/theme, complete repository/schema/seed implementation, both model message formats, graph nodes/interrupt/replay, API routes and logging, tests, local installers and QA/demo scripts, packaging and lockfile, Docker/Compose, CI, ignore rules, and existing documentation/evidence. Local setup/model-download utilities are not cloud service entrypoints.

| Finding before changes | Evidence / impact | Prepared change |
| --- | --- | --- |
| Frontend already called FastAPI | `frontend/api_client.py`, default localhost URL; no separate agent in UI | Environment URL retained; server-only API/session headers added |
| Backend lacked authentication and ownership | Every conversation could be listed/read; tickets shared | Shared server credential plus HMAC session capability; conversation/ticket isolation in protected mode |
| Hosted adapter already existed | `HTTPChatModel`, native Ollama vs Chat Completions; five function schemas | Retained both; configurable completion budget/reasoning; selected-model probe and context limit |
| LangGraph recovery depended on local files | `SqliteSaver`, app `Repository`, separate WAL databases | Explicit ephemeral/persistent declaration; visible reset warning; paid-mount instructions |
| Docker command fixed port 8000 | Render supplies PORT | New validated `support_agent.serve` launcher; same launcher in both Dockerfiles |
| Root dependency set included frontend and backend | Community Cloud could select root uv.lock | Entry-directory frontend requirements and backend-only export; source import bootstrap |
| Existing approval boundary was substantive | Immutable ledger, strict decision body, expiry, version, atomic write+receipt | Preserved; cloud owner checks added before proposing and applying a ticket update |
| Existing graph limits were only per turn | Public sessions could repeatedly incur inference | Persistent global daily model-call reservations, request/capacity limits, bounded session/global conversations |
| Health route included model/config/DB check | Not appropriate as an anonymous LLM readiness test | `/healthz` public constant liveness; existing detailed probes authenticated in protected mode |
| Logs deliberately excluded sensitive payloads | Allow-listed JSON logger and provider error sanitization | Retained; access logs disabled in cloud command; keys never placed in URLs |

No RAG, vector database, MCP, external ticket service, shell tool, or arbitrary model-generated SQL was introduced. The only SQL identifier interpolation in the new migration iterates a fixed application-owned tuple of table names. User/model values remain bound parameters.

## Deployment decision

- **Preferred zero-cost trial:** Streamlit Community Cloud frontend, Render Free backend, hosted provider access actually available to the owner, and explicitly disposable SQLite state. It is a bounded synthetic demo, not durable free storage.
- **Smallest persistence change:** a paid Render instance with a disk holding both SQLite databases and their WAL files. Billing/mount/restart verification is still required.
- **Not implemented:** Postgres repository/checkpointer migration. External database URLs cannot be substituted for SQLite paths.
- **Hosted candidate:** Groq `openai/gpt-oss-20b`, selected from current official local-tool, model, API, and rate-limit documentation. Five schemas, sequential calls, returned tool messages, and approval interrupts fit the existing graph. Documented capability is not verified account access or observed real generation.
- The initial Llama candidate was replaced during documentation verification because the current model list marks it enterprise-only. No free inference entitlement is claimed.

Sources and deployment instructions: [deployment guide](deployment.md). Provider/platform information was checked against official documentation on the audit date and can change.

## Verification scopes

- Deterministic tests use scripted `AIMessage` responses or mocked HTTP transports; they are not real-LLM results.
- Process smoke tests launch actual HTTP Uvicorn/Streamlit processes. One runs the exact cloud launcher with an unreachable HTTPS model endpoint and verifies an honest model-unavailable notice and restart persistence.
- The full local workflow process test uses a **scripted loopback HTTP model fixture**, not a generative model. It uses APP_MODE=local solely to allow an HTTP fixture URL; the shared API credential enables the same session isolation, ticket ownership, request and daily-budget guards. Actual cloud settings require HTTPS. This scope is deliberately not described as successful real hosted inference.
- Process/UI tests submit chat through Streamlit AppTest to the real HTTP backend, execute the status tool, return its result to the scripted provider, reject a ticket, restart with a pending proposal, explicitly click approval, and confirm one SQLite ticket write/receipt. Browser pixels and public WebSocket/proxy behavior remain unverified for this deployment profile.
- The older real Ollama tests remain historical evidence for the local provider. They were not rerun or relabeled as Groq/cloud success.

## November 25 refresh results

| Check | Actual result | Scope |
| --- | --- | --- |
| Complete regression suite | **116 passed; 0 failed; 0 errors; 0 skipped**, no warnings, 37.30 seconds | Existing regression suite plus cloud, fail-closed listing, paired reset, frontend recovery and process tests; models scripted/mocked or deliberately unavailable |
| Backend line coverage | **96.75%**, 833 of 861 statements | `support_agent`, not model quality or public browser coverage |
| Isolated-environment HTTP process tests | **3 passed** | Exact launcher, server auth/outage, normal restart recovery, paired reset/reseed, real HTTP scripted-provider approval, and CLI workflow |
| Groq real smoke | **Blocked: no configured runtime key/public app** | The no-key command exits 2 without making a provider request; no real-model pass is reported |
| Repository audit | **62 candidate paths; 60 text files; 0 findings**, 21 ignore probes | Current working tree only; not an exhaustive secret or history audit |
| Ruff / format / whitespace / relative documentation links | Passed | Local checks |
| Public frontend/backend, real Groq tool calling, live approval, Docker build | **Unverified** | Owner-managed hosting secrets, account access, publication approval and public acceptance still needed; Docker daemon unavailable |

The reset tests prove that application and checkpoint files share one generation, existing configured files remain untouched, stale conversation/proposal IDs fail after a boot, and an approved proposal in the fresh sandbox commits normally. Frontend tests prove stale history is cleared with a visible reset notice. Existing retained-state tests still verify recovery when reset-on-start is disabled.

Evidence remains ignored under `data/qa/deployment-refresh-final.xml`, `deployment-refresh-cloud.xml`, `deployment-refresh-isolated.xml`, `deployment-refresh-coverage.json`, and `deployment-refresh-source.json`. The no-key Groq command does not produce a successful inference artifact. The actual hosted model and public deployment require independent acceptance; previous local Ollama records and scripted tests are not substitutes.

## November 20 local results

| Check | Verified result | Evidence / scope |
| --- | --- | --- |
| Full automated suite | **109 passed; 0 failed; 0 errors; 0 skipped**, 16.70 seconds, no warnings in the final run | Includes the existing 82 tests, 25 new cloud regression cases and 2 process smoke tests; all model responses are scripted, mocked, or intentionally unavailable |
| Backend package line coverage | **96.65%**, 808 of 836 statements | `support_agent` only; not hosted model or browser quality coverage |
| Isolated frontend installation | Passed | 37 active locked requirements installed; FastAPI and LangGraph absent |
| Isolated backend installation | Passed | 49 active locked requirements installed; Streamlit absent; API imports successfully |
| Isolated-environment process smoke | **2 passed** | Actual backend and Streamlit HTTP processes, exact port launcher, auth/outage, persisted state, scripted provider, explicit synthetic approval, CLI checks |
| Scripted HTTP workflow | Passed | Status evidence passed back to the provider; rejected ticket unchanged; pending run survives backend restart; approval commits once; smoke CLI also exercises its exact-field decision prompt |
| SQLite integrity in process workflow | `ok` | Tested on the temporary synthetic demo database |
| Ruff / formatting / whitespace | Passed | `ruff check`, `ruff format --check`, `git diff --check` |
| Render configuration | Local YAML/static checks passed | Free plan, Dockerfile path, health path and secret placeholders inspected; not authenticated Render-side validation |
| Repository audit | **60 candidate paths; 58 text files; 0 findings**, 21 ignore probes | Includes common Groq/GitHub/AWS/OpenAI key patterns; not history scanning or DLP |
| Real hosted LLM / public frontend / cloud deployment / Docker image build | **Unverified** | No owner key/login or cloud resource approval; Docker daemon absent |

The new tests found one invalid scripted fixture: a reused tool-call ID correctly triggered replay protection. The fixture was corrected to use a distinct ID. An intermediate full run passed with three SQLite ResourceWarnings from new test connections; those connections now close explicitly and the final run has no warnings. Fresh frontend installation also exposed a shared transitive Uvicorn dependency; its pin is now retained in the frontend export. None of these results is counted as a real hosted-model success.

Local evidence is ignored under `data/qa/`: `cloud-first.xml`, `cloud-after.xml`, `cloud-process.xml`, `cloud-isolated-process.xml`, `cloud-isolated-final.xml`, `cloud-final.xml`, `cloud-coverage.json`, and `cloud-source.json`. Public documentation contains synthetic findings, not private headers or visitor capabilities.

Reproduce:

```bash
uv run pytest --cov=support_agent --cov-report=term-missing
uv run pytest tests/test_cloud_deployment.py tests/test_cloud_process.py
uv run ruff check .
uv run ruff format --check .
uv run python scripts/audit_repository.py
# Optional: after installing the two requirement sets into these local venvs:
CLOUD_BACKEND_PYTHON=.local/cloud-backend/bin/python CLOUD_FRONTEND_PYTHON=.local/cloud-frontend/bin/python uv run pytest tests/test_cloud_process.py
```

## Pending external acceptance

The public frontend URL, Render runtime/build, actual hosted model calls, public browser interactions, and a real model-generated approved synthetic ticket have not been verified. Docker Engine/daemon is absent in this workspace, so local process execution is not a Docker build test. No provider key was supplied, no account was logged into, no service was published, and no billing/cloud resource operation was performed.

The user must authorize account connections, secret provisioning, any paid plan/disk, and public service creation. After that, use `scripts/smoke_cloud.py --ticket-workflow` and the public-frontend acceptance steps in the deployment guide. The CLI pauses to review the exact proposed fields before the human decides.

## Limits that remain

Shared API authentication and opaque session capabilities are not employee identity/RBAC. A fresh Streamlit session has a fresh capability and cannot recover an old sandbox through an account login. Keep the shared key stable to retain ownership mappings; rotation invalidates them. Local mode without an API key remains a shared loopback demo.

An attacker can consume the finite demo capacity. Application counters and rate buckets are not provider-account spending controls; free backend file loss resets local counters. Request/token quotas, optional reasoning behavior, account access, and model quality require real-provider verification. One worker serializes investigations, with busy requests rejected rather than queued in protected mode. Context is bounded by characters, not exact tokens; network timeouts do not impose a strict whole-turn deadline.

Data remains synthetic and plaintext in SQLite. Do not upload private local history. Cloud model calls send conversation and tool data to the selected provider. Use provider-account limits and review provider retention policies before entering anything beyond synthetic examples.
