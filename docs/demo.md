# GitHub publishing and a two-minute demonstration

## Prepare the repository for GitHub

The configured remote is `git@github.com:SaadAlamDev/it-support-agent.git`. Run from the repository root:

```bash
git remote -v
git status --short
uv run python scripts/audit_repository.py
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=support_agent --cov-report=term-missing
git diff --check
git diff --stat
```

Review source changes and the actual screenshot files. `.gitignore` excludes private `.env` files, keys, model weights, runtime databases, logs, caches, and generated QA evidence; `.env.example` is intentionally public and contains no key. The audit checks tracked and non-ignored candidate files without printing matched secrets. It is a limited pattern scan, not a complete secret-detection service or historical audit.

After review:

```bash
git add README.md docs frontend support_agent scripts tests pyproject.toml Dockerfile .gitignore
git diff --cached --check
git diff --cached --stat
git diff --cached
git commit -m "Polish portfolio documentation and demo interface; include QA fixes"
git push origin main
```

`uv.lock`, `compose.yaml`, and the existing CI workflow are already tracked; stage them explicitly if you change them. If this is a fresh checkout with no remote, first run:

```bash
git remote add origin git@github.com:SaadAlamDev/it-support-agent.git
```

If a push is rejected because GitHub has newer commits, inspect and reconcile them; do not force-push over unfamiliar work. GitHub SSH authentication must be configured for your account. On GitHub, set the description to “Local LLM IT support agent with durable LangGraph workflows and human-approved ticket operations.” Suggested topics: `python`, `langgraph`, `fastapi`, `streamlit`, `ollama`, `tool-calling`, `human-in-the-loop`.

This preparation does not itself commit or push changes. Review and run the commands above when ready.

## Demo preflight

1. Start Ollama, FastAPI, and Streamlit using the README commands. Keep the app on loopback and use only the seeded synthetic environment.
2. Verify health and real tool calling:

   ```bash
   curl -fsS http://127.0.0.1:8000/health/model
   uv run python scripts/smoke_ollama.py
   ```

3. In the UI, open the saved smoke investigation through **Recent investigations**. Expand its status tool so the validated input, actual result, and final answer are visible. The query begins “Is the simulated corporate VPN healthy right now?”.
4. Start a second investigation: “ExpenseHub shows HTTP 503. Prepare a high priority support ticket describing the failed expense submission. Register the create_ticket proposal for my review.” Wait for the actual pending proposal and verify its fields. **Do not approve it merely to prepare the pending screen.** It expires after 30 minutes by default, so prepare it shortly before recording.
5. For a strict two-minute recording, prepare a third real investigation and explicitly approve or reject its synthetic proposal yourself. Keep that completed trace available. This is saved real execution, not a live-inference timing demonstration.
6. Choose a 1440×1000 or larger window. Hide desktop notifications, unrelated browser tabs, private terminal paths, and any personal information. Keep the activity panel visible.

CPU model requests can take tens of seconds or longer. Preparing real saved investigations allows a short review without pretending inference is instant. If you record live generation/approval, allow extra time or label any edited waiting period clearly.

## Two-minute run of show

| Time | Show | Suggested narration |
| --- | --- | --- |
| 0:00–0:15 | App header, simulated-systems label, configured model | “ServiceDesk is a local LLM support agent. These enterprise systems are simulated; the model and tool calls are real.” |
| 0:15–0:40 | Saved VPN question, expanded `get_system_status`, final answer | “This investigation was run before recording. The model chose the status tool, received incident SIM-VPN-104, and answered from that evidence.” |
| 0:40–1:00 | Second saved investigation, exact pending create arguments | “Ticket operations pause here. The backend stores an immutable proposal. A chat message saying yes cannot authorize this write.” |
| 1:00–1:20 | Prepared completed decision trace; alternatively a live explicit click with enough time | “Approval is a separate API decision. The write and receipt commit together; rejection leaves the ticket unchanged.” Explain whether you are showing saved or live execution. |
| 1:20–1:40 | Switch back to VPN via Recent investigations; briefly show README Mermaid | “Conversations and graph checkpoints persist. FastAPI owns the agent; Streamlit is only a client. The graph returns tool results to the model.” |
| 1:40–2:00 | QA table and limitations | “The deterministic tests and real-model cases are reported separately. This is a single-user local demo; authentication, real connectors, and production scaling are future work.” |

For an uninterrupted live demo, use the same sequence but reserve more than two minutes. Do not claim a saved trace was generated during the recording. Do not edit a tool result or model answer to make the model appear more capable.

## Screenshots

Capture only the running application with synthetic data. Suggested assets:

- `docs/screenshots/investigation.png`: a real completed status investigation with expanded tool evidence.
- `docs/screenshots/approval.png`: exact pending fields and visible Approve/Reject controls.

Preserve the “simulated” label, readable evidence, and configured model. Do not include raw environment files, credentials, or private employee details. A screenshot shows UI state, not proof of all safety properties; use automated tests and actual trace records for those claims.

Browser screenshot provenance and any scope limits are recorded in [portfolio verification](portfolio-verification.md).

## Useful talking points

Explain the engineering boundary: model autonomy chooses actions; application code validates and authorizes them. Discuss transactional approval receipts, replay after a committed write, optimistic ticket versions, and the distinction between model failure and tool failure. Show where scripted tests stop and actual Ollama verification begins. These are defensible technical skills; avoid claims of autonomous enterprise remediation or production readiness.
