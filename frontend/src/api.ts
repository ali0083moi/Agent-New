export type Action = { label: string; ok: boolean; tool: string };

export type ChatMessage = {
  id: number;
  session_id: number | null;
  role: "user" | "assistant";
  content: string;
  channel: "web" | "telegram" | "system";
  kind: string;
  created_at: string;
  meta: {
    actions?: Action[];
    quick_replies?: string[];
    attachments?: { type: string; url: string; name?: string }[];
    voice?: boolean;
    report_id?: number;
    part?: string;
    turn_id?: string;
    undoable?: boolean;
    undone?: boolean;
    followup_id?: number;
    snoozed_until?: string;
    forwarded_from?: string;
    document_name?: string;
  };
};

export type Task = {
  id: number;
  title: string;
  description: string;
  category: string;
  status: "todo" | "doing" | "done" | "cancelled";
  priority: number;
  due: string | null;
  due_fa: string | null;
  estimated_minutes: number | null;
  progress: number;
  notes: string;
  recurring_event_id: number | null;
  parent_id: number | null;
  project_id: number | null;
  project?: string | null;
  energy: "high" | "normal" | "low";
  spent_minutes: number;
  remaining_minutes: number;
  adjusted_estimate_minutes?: number;
  subtasks_total?: number;
  subtasks_done?: number;
  completed_at: string | null;
};

export type ChatSessionInfo = {
  id: number;
  title: string;
  created_at: string;
  last_message_at: string | null;
  messages: number;
  current: boolean;
  has_summary: boolean;
};

export type Place = {
  id: number;
  name: string;
  aliases: string[];
  kind: string;
  address: string;
  lat: number | null;
  lon: number | null;
  from_home_minutes: number | null;
};

export type TravelRow = {
  id: number;
  from: string;
  to: string;
  from_id: number;
  to_id: number;
  minutes: number;
  mode: string;
  both_ways: boolean;
  samples: number;
  estimated: boolean;
  distance_km: number | null;
};

export type PlacesData = {
  places: Place[];
  travel: TravelRow[];
  prep_minutes: number;
  prep_known: boolean;
  kinds: Record<string, string>;
  gaps: string[];
};

export type Project = {
  id: number;
  name: string;
  aliases: string[];
  category: string;
  client: string;
  description: string;
  status: "active" | "paused" | "done";
  open_tasks: number;
  minutes_this_week: number;
  next_deadline: { title: string; due: string } | null;
};

export type Contact = {
  id: number;
  name: string;
  organization: string;
  field: string;
  email: string;
  url: string;
  status: string;
  status_fa: string;
  last_contact: string | null;
  days_since_contact: number | null;
  next_followup: string | null;
  notes: string;
};

export type Habit = {
  id: number;
  title: string;
  unit: string;
  unit_fa: string;
  target: number;
  period: "daily" | "weekly";
  reminder_time: string | null;
  category: string;
  active: boolean;
  streak: number;
  best_streak: number;
  today: number;
  period_total: number;
  met_now: boolean;
  last7: { date: string; value: number; met: boolean }[];
};

export type Commitment = {
  id: number;
  day: string;
  text: string;
  target_minutes: number | null;
  logged_minutes: number | null;
  task_id: number | null;
  task_done: boolean;
  category: string | null;
  status: "pending" | "done" | "partial" | "missed";
  note: string;
};

export type Game = {
  level: number;
  title: string;
  points: number;
  level_start: number;
  next_level_at: number;
  progress: number;
  week_points: number;
  week_breakdown: Record<string, number>;
  weekly_score: { score: number; components: Record<string, number> };
  last_week_score: { score: number };
};

export type Budget = { spent_today: number; limit: number; over: boolean };

export type AgendaItem = {
  type: "class" | "event" | "block" | "deadline";
  title: string;
  start: string;
  end: string;
  ref_id: number;
  category: string;
  kind: string;
  meta: Record<string, unknown>;
};

export type AgendaDay = {
  date: string;
  jalali: string;
  weekday: string;
  weekday_fa: string;
  label: string;
  items: AgendaItem[];
};

export type Focus = {
  id: number;
  task_id: number | null;
  task: string | null;
  description: string;
  category: string;
  start: string;
  elapsed_minutes: number;
} | null;

export type Recurring = {
  id: number;
  title: string;
  kind: string;
  kind_fa: string;
  weekdays: string[];
  weekdays_fa: string[];
  start_time: string;
  end_time: string;
  location: string;
  instructor: string;
  week_parity: "all" | "odd" | "even";
  followup: boolean;
  notes: string;
  active: boolean;
};

export type Followup = { id: number; kind: string; due: string; status: string; note: string; task_id: number | null };
export type Goal = { id: number; title: string; description: string; target_date: string | null; status: string; progress_note: string };
export type Memory = { id: number; content: string; category: string; created_at: string };

export type DayStat = { date: string; weekday_fa: string; label: string; minutes: number; goal_minutes: number };
export type CatStat = { category: string; label: string; minutes: number };

export type Dashboard = {
  now: string;
  today_label: string;
  user_name: string;
  agenda: AgendaDay[];
  tasks: Task[];
  open_count: number;
  overdue_count: number;
  today: { productive_minutes: number; goal_minutes: number; completed: number };
  week: {
    days: DayStat[];
    by_category: CatStat[];
    total_productive_minutes: number;
    daily_goal_minutes: number;
    average_per_day_minutes: number;
    blocks: { planned: number; done: number; partial: number; skipped: number };
    completed: number;
  };
  focus: Focus;
  goals: Goal[];
  followups: Followup[];
  workload: {
    at_risk: { task_id: number; title: string; due: string; remaining_minutes: number; slack_minutes: number }[];
    min_slack: number | null;
    total_required: number;
    capacity_7d: number;
  };
  game: Game;
  habits: Habit[];
  commitments: Commitment[];
  budget: Budget | null;
};

export type Insights = {
  estimation_text: string[];
  peak_hours: number[];
  peak_hours_learned: boolean;
  hour_profile: number[];
  reminders: { kinds: Record<string, { sent: number; answered: number; rate: number }>; hours: Record<string, { sent: number; answered: number; rate: number }> };
  reminders_text: string[];
  game: Game;
};

export type Usage = {
  budget: Budget | null;
  days: { day: string; cost: number; prompt_tokens: number; completion_tokens: number; calls: number }[];
  models: { model: string; cost: number; prompt_tokens: number; completion_tokens: number; calls: number; failures: number }[];
  purposes: { purpose: string; cost: number; calls: number }[];
  total_cost: number;
};

export type IeltsPoint = { date: string; band: number; source: string; id: number };
export type Ielts = {
  target_band: number;
  exam_date: string | null;
  days_left: number | null;
  series: Record<string, IeltsPoint[]>;
  latest: Record<string, { last: number; avg_recent: number }>;
  gaps: Record<string, number>;
  vocab: { total: number; due_today: number; mastered: number; by_box: Record<string, number> };
};

export type Vocab = { id: number; word: string; meaning: string; example: string; box: number; due: string; reviews: number; correct: number };

export type Stats = {
  total_productive_minutes: number;
  average_per_day_minutes: number;
  daily_goal_minutes: number;
  goal_achievement_percent: number | null;
  days: DayStat[];
  by_category: CatStat[];
  by_task: { task: string; minutes: number }[];
  completed_tasks: { id: number; title: string; category: string; completed_at: string }[];
  blocks: { planned: number; done: number; partial: number; skipped: number };
};

export type Report = {
  id: number;
  project_id: number | null;
  week_start: string;
  week_end: string;
  label: string;
  work_report: string;
  personal_report: string;
  stats: Record<string, unknown>;
  created_at: string;
};

export type TelegramStatus = {
  configured: boolean;
  connected: boolean;
  bot_username: string | null;
  owner_bound: boolean;
  owner: { username?: string; name?: string } | null;
  pairing_code: string | null;
  last_error: string | null;
  outbox?: { pending: number; failed: number; last_error: string | null };
};

export type SettingsData = {
  prefs: Record<string, unknown>;
  telegram: TelegramStatus;
  llm: {
    configured: boolean;
    base_url: string;
    proxy: boolean;
    chat_model: string;
    fast_model: string;
    vision_model: string;
    ielts_model: string;
    strong_model: string;
    transcribe_model: string;
    tts_model: string;
    embedding_model: string;
    fallback_models: string;
    env_defaults: Record<string, string>;
  };
  budget: Budget | null;
  push: { subscriptions: number };
  timezone: string;
};

export type Me = {
  now?: string;
  version?: string | null;
  auth_required: boolean;
  authenticated: boolean;
  llm_configured?: boolean;
  user_name?: string;
  assistant_name?: string;
  telegram?: TelegramStatus;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const data = await res.json();
      msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* ignore */
    }
    if (res.status === 401) window.dispatchEvent(new Event("peygir:unauthorized"));
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  patch: <T>(url: string, body: unknown) => request<T>("PATCH", url, body),
  put: <T>(url: string, body: unknown) => request<T>("PUT", url, body),
  del: <T>(url: string) => request<T>("DELETE", url),
};
