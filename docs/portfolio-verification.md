# Portfolio preparation verification

Verified locally on **November 18, 2026**. This record supplements the earlier [QA report](qa-report.md); it does not represent hosted deployment, remote CI, or a comprehensive model evaluation.

## Changes reviewed

- Extracted Streamlit HTTP handling, evidence components, and static styling into `frontend/api_client.py`, `components.py`, and `theme.py`. The app remains an API client; backend approval and orchestration behavior was preserved.
- Included the frontend package in the wheel and moved its Docker copy before package installation. The wheel was built successfully and inspected for all five frontend module files. Container execution remains unverified.
- Refined the header, spacing, activity count wording, saved-investigation reference, and configured-model label. The label does not imply an inference health check has already succeeded.
- Added portfolio documentation, actual screenshots, a reviewed synthetic smoke trace, a timed demo guide, and a repeatable repository audit.
- Retained the previous QA fixes and regression tests. No backend code was changed during the portfolio refactor. Its SHA-256 source fingerprint remains `ae5db094b307ee00a90bbadb39879cb5d6f581bb59de815f24967de6990c79fc`, matching the prior full real-model QA runs.

## Verification results

| Check | Actual result | Scope |
| --- | --- | --- |
| Dependency setup | `uv sync --frozen --extra dev` succeeded | Existing Python 3.13.17 environment; committed lockfile |
| Final automated suite | **82 passed, 0 failed**, 9.59 seconds | Real temporary SQLite/API/UI execution; scripted model or mocked HTTP provider |
| Backend line coverage | **96.30%**, 624/648 statements | `support_agent` only; not model-quality/browser coverage |
| Ruff and whitespace | Passed | Source lint, format, `git diff --check` |
| Wheel build | Passed | `uv build --wheel --out-dir data/portfolio/dist`; backend and frontend modules present |
| Fresh real-model smoke | Passed | Actual Ollama → requested status tool → SQLite result → model final response |
| Real browser chat/proposal | Passed within this narrow scope | Chrome submitted a synthetic request through Streamlit/FastAPI; actual model registered a create proposal and paused |
| Ticket count after screenshot preparation | **1** | Seeded ticket only; the pending proposal was not approved and wrote no ticket |
| Repository audit | No findings | 48 candidate paths, 46 text files, common credential patterns, and 21 private/runtime ignore probes; not historical scanning or DLP |

The audit was also checked with a temporary synthetic token: it failed as expected without printing the token value; the fixture was then removed.

No failed checks remain in this portfolio pass. Earlier reproducible failures and their fixes are documented separately in the QA report. During screenshot work the backend process received SIGTERM and shut down cleanly; the browser correctly displayed its unavailable-backend error. The source of that external signal was not established. The backend was restarted against the same demo files; the saved conversation and pending proposal remained intact. No code change was made for that process interruption.

## Fresh real-model trace

Provider: actual local Ollama 0.40.16. Model: `qwen3:4b-instruct`, CPU inference. Backend: Uvicorn/FastAPI with fresh, ignored `data/portfolio/support.db` and `checkpoints.db`.

```bash
uv run python scripts/smoke_ollama.py --output data/portfolio/ollama-smoke.json
```

The actual response used **2 model steps and 1 completed `get_system_status` tool call**, with `service=vpn`. Its returned record was `degraded`, incident `SIM-VPN-104`. The final answer described that incident and matched the tool evidence. The measured end-to-end run took **108.56 seconds**; this single CPU observation is not a benchmark or latency promise. A reviewed non-sensitive export is committed as [portfolio-smoke.json](portfolio-smoke.json); raw runtime databases and model artifacts remain ignored.

The earlier full live QA remains **9 real-model workflow/protocol cases plus 2 explicit provider failure checks**. The full live QA suite was not rerun solely for the presentation refactor. The fresh smoke and actual browser request are separate additional evidence, not substitutes for a full model-quality evaluation.

## Screenshot provenance

[`screenshots/investigation.png`](screenshots/investigation.png) and [`screenshots/approval.png`](screenshots/approval.png) were captured from the running Streamlit application using a dedicated headless Windows Chrome connected to the WSL-local application. They contain real saved model-generated state and synthetic records, not generated images, mock UI, or substituted API responses.

The investigation screenshot shows the fresh smoke above. The approval screenshot follows this actual browser-submitted prompt:

> ExpenseHub shows HTTP 503. Prepare a high priority support ticket describing the failed expense submission. Register the create_ticket proposal for my review.

The model selected `create_ticket` and proposed a high-priority `application` ticket titled “ExpenseHub Service Unavailable - HTTP 503 Error.” Backend activity is `awaiting_approval`; the screenshot shows the immutable arguments and real approval controls. No approval click was performed for screenshot preparation. Browser tooling and its profile are ignored under `.local/`.

These captures verify rendering of two desktop states and one actual chat submission. They do **not** certify all browser interactions, accessibility, keyboard navigation, responsive/mobile layouts, or browser-based approval completion. Those remain separate work; the broader approval checks used AppTest/API execution as described in the QA report.

## Reproduce the local checks

```bash
uv sync --frozen --extra dev
uv run pytest --cov=support_agent --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run python scripts/audit_repository.py
git diff --check
# Requires a running real Ollama server and API:
uv run python scripts/smoke_ollama.py
```

Raw final automated results are saved locally in ignored `data/qa/portfolio-final.xml`. The initial post-extraction run is `data/qa/portfolio-automated.xml`; both passed all 82 tests.

The project still has no authentication/RBAC, real enterprise connector, encryption at rest, multi-user production concurrency, or verified remote deployment. Hosted inference, GPU execution, Docker runtime, and remote CI execution were not verified in this pass. Model answers and tool selection can still be wrong outside the tested prompts. See [limitations in the README](../README.md#limitations-and-future-improvements).
