# Handoff — پیگیر (Peygir) — "make it smarter" phase

Last updated: 2026-11-02. Branch: `claude/charming-lovelace-b7blod`. Every push to this branch is deployed by
`.github/workflows/ci.yml` (tests → build → SSH → `deploy/docker-deploy.sh`, health check + rollback).
**Do not push while an `ops.yml` evals run is in progress:** the deploy rebuilds the container and kills it.

## What the user asked for (this phase)
1. **Confirmation buttons**: when the assistant needs approval, show ✅/❌ buttons (web + Telegram). Pressing
   approve must execute the action immediately — no more "asks for confirmation and never does it".
2. **Edit instead of duplicate**: "add X" then "change it" must update X, not create a second task.
3. **Human-like follow-ups and planning**: know where the user is during the day (home / company / university /
   on the way), learn how they study, plan backwards from deadlines, stop robotic "block started/ended" pings.
4. **Try other GapGPT models** for the best quality/price (see "Model comparison" below).
5. Read the user's real chats. The export is on the user's Mac at
   `/Users/ali/Documents/Projects/Agent-New/data/peygir-database-2026-11-02`. The cloud session could not reach the
   Mac (no Remote Control folder shared) and the web API needs `APP_PASSWORD`, so the real chats were NOT reviewed
   yet. **Next step locally:** open that SQLite file, read `messages` (role, content, meta.action_log) and look for:
   repeated "تأیید" loops, duplicate tasks (same title in `tasks`), proactive messages the user ignored or disliked,
   wrong dates. Add each failure as an eval scenario in `backend/app/evals.py`.

## What was built
### 1. Approval buttons — `backend/app/pending.py`, table `pending_actions`
- Delete tools (`delete_task`, `delete_event`, `delete_recurring_event`, `delete_habit`) call
  `journal.confirm_guard`; in a chat turn that returns `needs_confirmation`, and `tools.execute` turns it into a
  `PendingAction` (the exact tool + args) via `tools._propose`.
- New core tool `ask_approval(summary, actions=[{tool, args}])` (in `agent/tools_more.py`): the model bundles concrete
  tool calls for approval (bulk changes, a new plan, ambiguous requests). Validated up front (tool exists, not
  read-only, required args, ids exist).
- After the turn, `core.handle_user_message` puts `meta.confirm = {status, summary, items}` on the assistant
  message and links the pending rows to it (`pending.attach`).
- Answering: web `POST /api/messages/{id}/approve|reject` (ConfirmCard in `frontend/src/pages/Chat.tsx`), Telegram
  inline buttons `pa:<message_id>:y|n` (`outbox.buttons_for`, `telegram._handle_callback`), or a bare typed
  «آره/باشه/اوکی» / «نه/ولش کن» (`pending.quick_answer`, handled before the LLM in `handle_user_message`).
- `pending.resolve` runs the stored calls with `trusted=True` under the original message's turn id (so «برگردون»
  undoes them), stores a short "✅ انجام شد: …" message with an action_log, updates the card state. No LLM call.
- A new proposal expires older unanswered ones; proposals expire after 24h. Pending ones are listed in the
  context ("Waiting for the user's ✅/❌ button") so the model does not redo or re-ask them.
- Prompt rule (PERSONA): act, don't ask for normal adds/edits; never end with «ثبتش کنم؟»; use ask_approval for
  deletions / big changes / real ambiguity.

### 2. Edit instead of duplicate — `backend/app/match.py`
- `add_task` / `add_event` refuse a near-identical open item (similar title; same week for tasks, ±36h for
  events; differing numbers like «جلسه ۸»/«جلسه ۹» or each title having extra words = different) and return
  `existing_task`/`existing_event` + instruction to call update instead; `allow_duplicate=true` forces. UI calls
  (`ctx.trusted`) skip the guard.
- `update_task` / `complete_task` / `delete_task` accept `task=<title>`; `update_event` / `delete_event` accept
  `event=<title>` (fuzzy match, ambiguity → error listing candidates).
- History notes (`[actions done: …]`) now carry titles; context has "Recently added/changed" (journal, 48h).
- Prompt rule: corrections are edits.

### 3. Day-aware, human planning — `backend/app/daymap.py`, `planner.py`, `followups.py`, `agent/proactive.py`
- `daymap.day_map(s, date)`: segments of the day — getting ready, trips (from `places.plan_trips`), classes/work/
  events with places, planned work, free windows with where the user is. `where_now`, `busy_now`, `place_at`,
  `context_lines` (in every prompt: "The user's day").
- `tools.deadline_plan`: `add_task`/`update_task` (due or estimate changed) re-plan immediately (horizon up to 21
  days) and return `plan = {needs_minutes, planned_minutes, fits, sessions:["tue 10-20 20:00-21:30 @خانه"…],
  warning}`. `Ctx.plan_days` keeps the end-of-turn re-plan on the same horizon.
- Planner: block length = learned session length per category (`insights.study_profile` /
  `session_minutes`, from WorkLog, ≥6 sessions), preferred hours = pref `study_hours` ("20:00-23:30,…") over the
  learned peak hours. `update_preferences(study_hours=…)` parses Persian ranges (`timeutil.parse_ranges`).
- Follow-ups: `_session_nudges` — one `block_start` per work session (consecutive blocks), max 1/2/4 per day by
  intensity, skips short non-urgent sessions; no more `block_end` pings (pending ones are cancelled). Reminders in
  `DEFER_WHILE_BUSY` wait until the current class/meeting/getting-ready ends (`daymap.busy_now`).
- Proactive texts: session nudge says what/why now/until when (with day map) and never says «بلوک»; morning brief
  uses the day map; evening review lists today's planned work and asks how much got done (to log it).

### 4. Evals — `backend/app/evals.py` (16 scenarios)
New: `edit`, `correction`, `change`, `deadline`, `bulk`, `act`; `delete` now checks the buttons. Run on the server:
GitHub → Actions → Ops → task `evals`, models comma-separated, scenarios optional. Results appear in the run summary.

2026-11-04: +10 scenarios from the real chats (26 total): `deadline_list` (Jalali list, 23:59), `midterm_fix`,
`delete_wrong`, `done_fuzzy`, `dnd` («تا ۱:۳۰ مزاحمم نشو»), `quiet_now` («دهنتو ببند دارم … مینویسم»),
`skip_class`, `project_task`, `wellbeing`, `ask_tonight`. `_clean` now also fails replies that show tool names,
`{"tool_names"…}` or raw DB errors. `dnd` and `quiet_now` check `followups.due_now`.
+2 (28 total): `remind_in` («یک ساعت دیگه یادم بنداز…» → exactly 11:00), `past_quiz` (a quiz that's over is not
mentioned or planned).

### 5. Reminder-first (2026-11-04, after the user said it felt dumb and noisy)
- `prefs.followup_style`: `reminder` (default) | `coach`. `followups.COACH_ONLY` (block_start/end, idle_checkin,
  task_checkin, rescue, daily_review) are neither generated nor sent in `reminder`; tests for them set `coach`.
- The past is past: `planner.retire_expired` (run in `followups.generate`) closes open tasks whose deadline passed
  and that are pointless afterwards (`Task.expires`, or a title with کوییز/امتحان/میانترم/ارائه/مصاحبه…; note
  `planner.EXPIRED_NOTE`). `planner.schedulable_tasks` leaves out overdue tasks unless status=doing, so they are not
  planned, not in rescue/feasibility, and the morning brief only lists future deadlines. The prompt marks them OVERDUE.
- Overdue question and the user's own reminders are fixed texts (no model call) with one-tap buttons
  (`actions.reminder_action`: done / tomorrow / drop / snooze10 / snooze60; Telegram `ra:<msg>:<id>`, web
  `POST /api/messages/{id}/reminder/{action}`). Overdue is no longer batched.
- Tools: `schedule_followup(in_minutes=…)` (the server computes the time; live it once said 16:45 for «نیم ساعت
  دیگه» at 16:00), `do_not_disturb(until|minutes|off)` → state `quiet_until` (`followups.due_now` holds everything
  but custom/leave; DROP_WHEN_QUIET are cancelled), `skip_class` (cancels that session's post_class / leave).
- Local tests on the Mac: PyPI wheels can't be downloaded here; `uv pip install --offline` plus packages symlinked
  into `.venv/borrowed` run 154 tests. The ~19 that need itsdangerous/icalendar/pywebpush/pypdf only run in CI.

## Model comparison (ops evals, 2026-11-02, 14 scenarios / 40 checks, on the server)
| model | score | cost | time |
|---|---|---|---|
| deepseek-v4-flash (**new default: chat + fast**) | 39/40 | $0.038 | 168 s |
| gpt-4.1-mini (fallback 1) | 39/40 | $0.070 | 183 s |
| gpt-5.6-luna | 39/40 | $0.055 | 326 s |
| gpt-5-mini | 39/40 | $0.107 | 339 s |
| gemini-3.5-flash | 39/40 | $0.506 | 210 s |
| gemini-3.1-flash-lite (old default, fallback 2) | 38/40 | $0.077 | 220 s |
| claude-haiku-5-5 | 38/40 | $0.052 | 304 s |
| qwen3-235b-a22b | 35/40 | $0.046 | 412 s |
| gpt-6-luna | invalid: refused tools (fixed since: `reasoning_effort=none` for gpt-6*) | | |
| deepseek-v4-pro | 403 — not enabled on this GapGPT account | | |

Most common miss: "bulk" — several models move many tasks directly instead of calling ask_approval.
`prefs.apply_model_defaults` (MODEL_DEFAULTS_VERSION 3) switches installs that still use an earlier recommended
model; a model the user picked in Settings is kept. The eval report now prints the tools and resulting
tasks/events of every failed scenario (synthetic data only).

## How to run locally
```
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt
pytest -q                       # 148+ tests
cd ../frontend && npm ci && npm run build
python -m app.evals --models gemini-3.1-flash-lite,deepseek-v4-flash --scenarios edit,correction,deadline
```

## Open ideas / next steps
- Real chats reviewed 2026-11-04 (done → scenarios above). Do-not-disturb, skip class, exact reminders: built (5.).
- Run the 28 evals on the server with deepseek-v4-flash and look at `dnd`, `quiet_now`, `skip_class`, `remind_in`.
- Commute time as light-task time (vocab on the metro) — day map already knows the trips and their mode.
- Per-weekday "light days" from `study_profile` could lower the daily focus cap on those days.
- Quick-reply chips for questions with options (beyond yes/no).

## Progress log
- 2026-11-02: everything below is committed and pushed (deployed by CI).
- 7a2cc98 approval buttons + duplicate guard (deployed)
- (next) day map, deadline plans, study pattern, session nudges (deployed)
- d088ffe evals for the new behaviour (deployed)
- e869fa0 approval card polish + README
- then: human deadline reminders / check-ins, gpt-6 tool fix, eval report details, deepseek-v4-flash default
