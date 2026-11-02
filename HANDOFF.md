# Handoff — پیگیر (Peygir) — "make it smarter" phase

Last updated: 2026-11-02. Branch: `claude/charming-lovelace-b7blod`. Every push to this branch is deployed by
`.github/workflows/ci.yml` (tests → build → SSH → `deploy/docker-deploy.sh`, health check + rollback).

## What the user asked for (this phase)
1. **Confirmation buttons**: when the assistant needs approval, show ✅/❌ buttons (web + Telegram). Pressing
   approve must execute the action immediately — no more "asks for confirmation and never does it".
2. **Edit instead of duplicate**: "add X" then "change it" must update X, not create a second task.
3. **Human-like follow-ups and planning**: know where the user is during the day (home / company / university /
   on the way), learn how they study, plan backwards from deadlines, and stop robotic "block started/ended" pings.
4. **Try other GapGPT models** for the best quality/price.
5. Read the user's real chats (exported DB at `/Users/ali/Documents/Projects/Agent-New/data/peygir-database-2026-11-02`
   on the user's Mac). The cloud session could not reach the Mac (no Remote Control folder shared), and the web
   API needs `APP_PASSWORD`. Locally: open that SQLite file and review `messages` to find more failure cases.

## Design (see code for details)
- `backend/app/pending.py` + `PendingAction` table: proposals that wait for a button press.
  - Delete tools (`delete_task`, `delete_event`, `delete_recurring_event`, …) no longer ask verbally: the call
    becomes a pending action, the reply shows buttons; approve runs the stored call with `trusted=True` under the
    original turn id (so "برگردون" still works).
  - `ask_approval(summary, actions=[{tool, args}])` lets the model bundle concrete tool calls for approval.
  - A plain "آره/باشه/اوکی/بله" (or "نه/ولش کن") reply to a message with pending actions is handled by the server
    without the LLM (`pending.quick_answer`).
  - Web: `POST /api/pending/{id}/approve|reject`; Telegram callback `pa:<id>:y|n`.
- Duplicates: `add_task` / `add_event` refuse a near-identical open item and point at it (`allow_duplicate=true`
  to force); `update_task` / `complete_task` / `delete_task` accept `task` (title) instead of `task_id`;
  action logs in history carry titles; the context lists recently created/changed items.
- Planning: see `backend/app/daymap.py` (where the user is during the day, free windows with location) and the
  deadline plan returned by `add_task`/`update_task` (`plan` field: scheduled sessions before the deadline, fits?).
  Block start/end pings are replaced by "session" nudges (first block of a work session only, with day context).

## How to run locally
```
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r ../requirements.txt -r requirements-dev.txt
pytest -q                       # all tests
cd ../frontend && npm ci && npm run build
```
Evals against real models: `python -m app.evals --models gemini-3.1-flash-lite,deepseek-v4-flash` (needs
`LLM_API_KEY`), or the manual GitHub workflow `ops.yml` (task `evals`). Logs are public: print numbers only.

## Status
See the bottom of this file ("Progress log") — updated as work lands.

## Progress log
- (in progress) confirm buttons, duplicate guard, planning/daymap, evals.
