import { useEffect, useState } from "react";
import { BookOpen, CalendarClock, Flag, GraduationCap, Pause, Play, Timer, Users } from "lucide-react";
import { api, type AgendaItem, type DayStat, type Focus, type Task } from "../api";
import { useLive } from "../live";
import { cx, duration, fa, faTime, parseLocal, serverNow, shortDuration, WEEKDAYS } from "../util";
import { Button, Select } from "./ui";

/* ------------------------------------------------------------------ focus timer */
export function FocusCard({ focus, tasks, compact }: { focus: Focus; tasks: Task[]; compact?: boolean }) {
  const { toast, bump } = useLive();
  const [taskId, setTaskId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!focus) return;
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [focus]);

  const start = async () => {
    setBusy(true);
    try {
      await api.post("/api/focus/start", taskId ? { task_id: Number(taskId) } : { description: "کار آزاد" });
      bump("stats", "tasks");
      toast("تایمر شروع شد ⏱", "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ logged_minutes: number }>("/api/focus/stop", {});
      bump("stats", "tasks");
      toast(`${duration(r.logged_minutes)} کار مفید ثبت شد ✅`, "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  if (focus) {
    const startD = parseLocal(focus.start);
    const secs = startD ? Math.max(0, Math.floor((serverNow().getTime() - startD.getTime()) / 1000)) : 0;
    const hh = Math.floor(secs / 3600);
    const mm = Math.floor((secs % 3600) / 60);
    const ss = secs % 60;
    const clock = `${hh ? fa(hh) + ":" : ""}${fa(String(mm).padStart(2, "0"))}:${fa(String(ss).padStart(2, "0"))}`;
    return (
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-brand-600 via-violet-600 to-fuchsia-600 p-4 text-white shadow-[var(--shadow-lift)]">
        <div className="absolute -start-6 -top-10 size-32 rounded-full bg-white/10 blur-2xl" />
        <div className="relative flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-xs text-white/75">
              <span className="relative flex size-2">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-300 opacity-75" />
                <span className="relative inline-flex size-2 rounded-full bg-emerald-300" />
              </span>
              در حال تمرکز
            </div>
            <div className="mt-1 truncate font-bold">{focus.task ?? focus.description}</div>
            <div className="ltr mt-1 text-3xl font-black tracking-wider">{clock}</div>
          </div>
          <Button variant="outline" className="!border-white/30 !bg-white/15 !text-white hover:!bg-white/25" onClick={stop} loading={busy}>
            <Pause className="size-4" /> پایان
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className={cx("rounded-2xl border border-dashed border-slate-300 p-4 dark:border-slate-700", compact && "p-3")}>
      <div className="mb-2 flex items-center gap-2 text-sm font-bold">
        <Timer className="size-4 text-brand-500" /> تایمر تمرکز
      </div>
      <div className="flex gap-2">
        <Select
          className="min-w-0 flex-1"
          value={taskId}
          onChange={setTaskId}
          options={[{ value: "", label: "کار آزاد (بدون تسک)" }, ...tasks.map((t) => ({ value: String(t.id), label: t.title }))]}
        />
        <Button onClick={start} loading={busy}>
          <Play className="size-4" /> شروع
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ timeline */
const TYPE_STYLE: Record<string, { label: string; dot: string; bg: string; icon: typeof GraduationCap }> = {
  class: { label: "کلاس", dot: "bg-violet-500", bg: "bg-violet-50 dark:bg-violet-500/10", icon: GraduationCap },
  event: { label: "رویداد", dot: "bg-amber-500", bg: "bg-amber-50 dark:bg-amber-500/10", icon: Users },
  block: { label: "بلوک کاری", dot: "bg-emerald-500", bg: "bg-emerald-50 dark:bg-emerald-500/10", icon: BookOpen },
  deadline: { label: "ددلاین", dot: "bg-rose-500", bg: "bg-rose-50 dark:bg-rose-500/10", icon: Flag },
};

export function Timeline({ items, empty = "برنامه‌ای ثبت نشده" }: { items: AgendaItem[]; empty?: string }) {
  if (!items.length)
    return (
      <div className="flex items-center gap-2 rounded-xl bg-slate-50 px-3 py-4 text-sm text-slate-500 dark:bg-slate-800/40">
        <CalendarClock className="size-4" /> {empty}
      </div>
    );
  const now = serverNow().getTime();
  return (
    <ol className="relative space-y-2 ps-4 before:absolute before:inset-y-1 before:start-[5px] before:w-px before:bg-slate-200 dark:before:bg-slate-800">
      {items.map((it, i) => {
        const st = TYPE_STYLE[it.type] ?? TYPE_STYLE.event;
        const s = parseLocal(it.start)?.getTime() ?? 0;
        const e = parseLocal(it.end)?.getTime() ?? 0;
        const live = s <= now && now < e;
        const past = e < now && it.type !== "deadline";
        const Icon = st.icon;
        return (
          <li key={`${it.type}-${it.ref_id}-${i}`} className={cx("relative", past && "opacity-55")}>
            <span className={cx("absolute -start-4 top-3.5 size-2.5 rounded-full ring-4 ring-white dark:ring-slate-900", st.dot)} />
            <div className={cx("flex items-center gap-3 rounded-xl px-3 py-2", st.bg, live && "ring-2 ring-brand-400")}>
              <Icon className="size-4 shrink-0 opacity-70" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold">{it.title}</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  {st.label} · {it.type === "deadline" ? faTime(it.start) : `${faTime(it.start)} تا ${faTime(it.end)}`}
                  {typeof it.meta?.location === "string" && it.meta.location ? ` · ${it.meta.location}` : ""}
                </div>
              </div>
              {live && <span className="rounded-lg bg-brand-600 px-2 py-0.5 text-[11px] font-bold text-white">الان</span>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------------ weekly bars */
export function WeekBars({ days, goal }: { days: DayStat[]; goal: number }) {
  const max = Math.max(goal, ...days.map((d) => d.minutes), 60);
  const todayIso = (() => {
    const n = serverNow();
    return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}-${String(n.getDate()).padStart(2, "0")}`;
  })();
  return (
    <div>
      <div className="relative flex h-40 items-end gap-2 sm:gap-3">
        <div className="pointer-events-none absolute inset-x-0 border-t border-dashed border-brand-300 dark:border-brand-700" style={{ bottom: `${(goal / max) * 100}%` }}>
          <span className="absolute -top-5 end-0 text-[10px] text-brand-500">هدف {shortDuration(goal)}</span>
        </div>
        {days.map((d) => {
          const h = (d.minutes / max) * 100;
          const isToday = d.date === todayIso;
          const reached = d.minutes >= goal;
          return (
            <div key={d.date} className="group flex h-full flex-1 flex-col items-center justify-end gap-1">
              <span className="text-[10px] text-slate-400 opacity-0 transition group-hover:opacity-100">{shortDuration(d.minutes)}</span>
              <div
                title={`${d.weekday_fa}: ${duration(d.minutes)}`}
                className={cx(
                  "w-full max-w-10 rounded-t-lg transition-all",
                  d.minutes === 0 ? "bg-slate-100 dark:bg-slate-800" : reached ? "bg-gradient-to-t from-emerald-500 to-teal-400" : "bg-gradient-to-t from-brand-600 to-violet-400",
                  isToday && "ring-2 ring-brand-300 ring-offset-2 ring-offset-white dark:ring-offset-slate-900",
                )}
                style={{ height: `${Math.max(h, 3)}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex gap-2 sm:gap-3">
        {days.map((d) => (
          <div key={d.date} className={cx("flex-1 text-center text-[11px]", d.date === todayIso ? "font-bold text-brand-600 dark:text-brand-300" : "text-slate-500")}>
            {WEEKDAYS.find((w) => w.label === d.weekday_fa)?.short ?? d.weekday_fa}
          </div>
        ))}
      </div>
    </div>
  );
}

export function CategoryBars({ items }: { items: { category: string; label: string; minutes: number }[] }) {
  const total = items.reduce((a, b) => a + b.minutes, 0) || 1;
  const colors: Record<string, string> = {
    work: "bg-sky-500",
    university: "bg-violet-500",
    language: "bg-emerald-500",
    personal: "bg-amber-500",
    health: "bg-rose-500",
    other: "bg-slate-400",
  };
  if (!items.length) return <p className="text-sm text-slate-500">هنوز زمانی ثبت نشده.</p>;
  return (
    <div className="space-y-3">
      <div className="flex h-3 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        {items.map((c) => (
          <div key={c.category} className={colors[c.category] ?? "bg-slate-400"} style={{ width: `${(c.minutes / total) * 100}%` }} />
        ))}
      </div>
      <ul className="grid grid-cols-2 gap-2 text-sm">
        {items.map((c) => (
          <li key={c.category} className="flex items-center gap-2">
            <span className={cx("size-2.5 rounded-full", colors[c.category] ?? "bg-slate-400")} />
            <span className="text-slate-600 dark:text-slate-300">{c.label}</span>
            <span className="ms-auto font-semibold">{shortDuration(c.minutes)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
