import { AlertTriangle, BellRing, CalendarDays, CheckCircle2, Flame, HandHeart, ListTodo, Target, Trophy, Wallet, Wand2 } from "lucide-react";
import { useState } from "react";
import { api, type Dashboard as D } from "../api";
import { useData, useLive } from "../live";
import { Button, Card, Empty, Ring, Spinner, Stat } from "../components/ui";
import { CategoryBars, FocusCard, Timeline, WeekBars } from "../components/widgets";
import { TaskItem } from "../components/TaskItem";
import { cx, duration, fa, faDateTime, FOLLOWUP_KIND, serverNow, shortDuration, syncServerClock } from "../util";

function greeting(): string {
  const h = serverNow().getHours();
  if (h < 5) return "شب بخیر";
  if (h < 11) return "صبح بخیر";
  if (h < 15) return "ظهر بخیر";
  if (h < 19) return "عصر بخیر";
  return "شب بخیر";
}

export default function Dashboard({ go }: { go: (page: string) => void }) {
  const { data, loading } = useData<D>("/api/dashboard", ["tasks", "stats", "schedule", "followups", "goals", "messages"]);
  const { toast, bump } = useLive();
  const [planning, setPlanning] = useState(false);
  if (data) syncServerClock(data.now);

  if (!data) return <div className="flex h-64 items-center justify-center">{loading ? <Spinner /> : "خطا در دریافت اطلاعات"}</div>;

  const [today, tomorrow] = data.agenda;
  const pct = data.today.goal_minutes ? data.today.productive_minutes / data.today.goal_minutes : 0;

  const replan = async () => {
    setPlanning(true);
    try {
      const r = await api.post<{ blocks: unknown[]; warnings: string[] }>("/api/plan", { days: 7 });
      bump("schedule", "followups");
      toast(r.warnings.length ? `برنامه چیده شد، ولی ${fa(r.warnings.length)} هشدار داریم` : `برنامه ${fa(r.blocks.length)} بلوک کاری چیده شد ✨`, r.warnings.length ? "info" : "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setPlanning(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-slate-500 dark:text-slate-400">{data.today_label}</p>
          <h1 className="text-2xl font-black sm:text-3xl">
            {greeting()}
            {data.user_name ? `، ${data.user_name}` : ""} 👋
          </h1>
        </div>
        <div className="flex gap-2">
          <Button variant="soft" onClick={replan} loading={planning}>
            <Wand2 className="size-4" /> برنامه‌ریزی هوشمند
          </Button>
        </div>
      </div>

      {data.workload.at_risk.length > 0 && (
        <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
          <AlertTriangle className="mt-0.5 size-5 shrink-0" />
          <div className="text-sm leading-7">
            <b>با حجم کار فعلی به این ددلاین‌ها نمی‌رسی:</b>
            <ul className="mt-1 list-disc ps-5">
              {data.workload.at_risk.map((t) => (
                <li key={t.task_id}>
                  {t.title} — {faDateTime(t.due)} (کمبود {duration(-t.slack_minutes)})
                </li>
              ))}
            </ul>
            <button onClick={() => go("chat")} className="mt-1 font-semibold underline">
              با پیگیر درباره‌اش حرف بزن
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="card col-span-2 flex items-center gap-4 p-4 lg:col-span-1">
          <Ring value={pct}>
            <span className="text-lg font-black">{fa(Math.round(pct * 100))}٪</span>
          </Ring>
          <div>
            <div className="text-xs text-slate-500 dark:text-slate-400">کار مفید امروز</div>
            <div className="text-lg font-extrabold">{shortDuration(data.today.productive_minutes)}</div>
            <div className="text-xs text-slate-400">از هدف {shortDuration(data.today.goal_minutes)}</div>
          </div>
        </div>
        <Stat label="کارهای باز" value={fa(data.open_count)} sub={data.overdue_count ? `${fa(data.overdue_count)} مورد عقب‌افتاده` : "همه سر وقت"} icon={<ListTodo className="size-5" />} tone={data.overdue_count ? "rose" : "brand"} />
        <Stat label="این هفته" value={shortDuration(data.week.total_productive_minutes)} sub={`میانگین روزانه ${shortDuration(data.week.average_per_day_minutes)}`} icon={<Flame className="size-5" />} tone="amber" />
        <Stat label="انجام‌شده این هفته" value={fa(data.week.completed)} sub={`بلوک‌ها: ${fa(data.week.blocks.done)} از ${fa(data.week.blocks.planned)}`} icon={<CheckCircle2 className="size-5" />} tone="emerald" />
      </div>

      <button onClick={() => go("progress")} className="card flex w-full flex-wrap items-center gap-x-6 gap-y-3 p-4 text-start transition hover:border-brand-300">
        <div className="flex items-center gap-3">
          <div className="rounded-xl bg-gradient-to-br from-brand-500 to-fuchsia-500 p-2.5 text-white">
            <Trophy className="size-5" />
          </div>
          <div>
            <div className="text-xs text-slate-500">سطح {fa(data.game.level)}</div>
            <div className="font-extrabold">{data.game.title}</div>
          </div>
        </div>
        <div className="text-sm">
          <span className="text-slate-500">نمره هفته: </span>
          <b>{fa(data.game.weekly_score.score)}</b>
          <span className="text-slate-400"> / ۱۰۰</span>
          <span className="ms-3 text-slate-500">امتیاز هفته: </span>
          <b>{fa(data.game.week_points)}</b>
        </div>
        {data.habits.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {data.habits.slice(0, 5).map((h) => (
              <span key={h.id} className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs", h.met_now ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300")}>
                <Flame className={cx("size-3", h.streak ? "text-orange-500" : "text-slate-400")} />
                {h.title} {h.streak ? fa(h.streak) : ""}
              </span>
            ))}
          </div>
        )}
        {data.commitments.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
            <HandHeart className="size-4 text-rose-500" />
            قول‌های امروز: {fa(data.commitments.filter((c) => c.status === "done").length)} از {fa(data.commitments.length)}
          </div>
        )}
        {data.budget && data.budget.limit > 0 && (
          <div className={cx("ms-auto flex items-center gap-1.5 text-xs", data.budget.over ? "text-rose-600" : "text-slate-500")}>
            <Wallet className="size-4" /> هزینه امروز: ${data.budget.spent_today.toFixed(2)} از ${data.budget.limit}
          </div>
        )}
      </button>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          <FocusCard focus={data.focus} tasks={data.tasks} />
          <Card title="برنامه امروز" icon={<CalendarDays className="size-4 text-brand-500" />} action={<button onClick={() => go("schedule")} className="text-xs text-brand-600 dark:text-brand-300">هفته کامل</button>}>
            <Timeline items={today?.items ?? []} empty="امروز برنامه ثابتی نداری" />
            {tomorrow && tomorrow.items.length > 0 && (
              <>
                <div className="mb-2 mt-5 text-sm font-bold text-slate-500">فردا · {tomorrow.label}</div>
                <Timeline items={tomorrow.items} />
              </>
            )}
          </Card>
          <Card title="کار مفید این هفته" icon={<Flame className="size-4 text-amber-500" />}>
            <WeekBars days={data.week.days} goal={data.week.daily_goal_minutes} />
            <div className="mt-5">
              <CategoryBars items={data.week.by_category} />
            </div>
          </Card>
        </div>
        <div className="space-y-5">
          <Card title="کارهای پیش رو" icon={<ListTodo className="size-4 text-brand-500" />} action={<button onClick={() => go("tasks")} className="text-xs text-brand-600 dark:text-brand-300">همه</button>}>
            {data.tasks.length ? (
              <div className="-mx-2 space-y-0.5">
                {data.tasks.slice(0, 7).map((t) => (
                  <TaskItem key={t.id} task={t} compact />
                ))}
              </div>
            ) : (
              <Empty title="کار بازی نداری 🎉">هر کاری داری توی گفتگو بگو تا ثبتش کنم.</Empty>
            )}
          </Card>
          {data.goals.length > 0 && (
            <Card title="اهداف" icon={<Target className="size-4 text-emerald-500" />}>
              <ul className="space-y-2">
                {data.goals.map((g) => (
                  <li key={g.id} className="rounded-xl bg-emerald-50/70 p-3 dark:bg-emerald-500/10">
                    <div className="font-semibold">{g.title}</div>
                    {g.target_date && <div className="text-xs text-slate-500">تا {faDateTime(g.target_date).split("،")[0]}</div>}
                    {g.progress_note && <div className="mt-1 text-xs text-slate-600 dark:text-slate-300">{g.progress_note}</div>}
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <Card title="پیگیری‌های بعدی" icon={<BellRing className="size-4 text-amber-500" />}>
            {data.followups.length ? (
              <ul className="space-y-2 text-sm">
                {data.followups.map((f) => (
                  <li key={f.id} className="flex items-center justify-between gap-2">
                    <span className="truncate">{f.note || FOLLOWUP_KIND[f.kind] || f.kind}</span>
                    <span className="shrink-0 text-xs text-slate-500">{faDateTime(f.due)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-500">فعلاً پیگیری زمان‌بندی‌شده‌ای نیست.</p>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
