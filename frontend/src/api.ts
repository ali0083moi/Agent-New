export type Action = { label: string; ok: boolean; tool: string };

export type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  channel: "web" | "telegram" | "system";
  kind: string;
  created_at: string;
  meta: {
    actions?: Action[];
    quick_replies?: string[];
    attachments?: { type: string; url: string }[];
    voice?: boolean;
    report_id?: number;
    part?: string;
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
  spent_minutes: number;
  remaining_minutes: number;
  completed_at: string | null;
};

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
};

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
};

export type SettingsData = {
  prefs: Record<string, unknown>;
  telegram: TelegramStatus;
  llm: { configured: boolean; base_url: string; chat_model: string; fast_model: string; vision_model: string; transcribe_model: string };
  timezone: string;
};

export type Me = {
  now?: string;
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
  del: <T>(url: string) => request<T>("DELETE", url),
};
