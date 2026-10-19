import { useEffect, useMemo, useState } from "react";
import { Briefcase, Copy, Download, FileText, Sparkles, Trash2, User } from "lucide-react";
import { api, type Report, type Stats } from "../api";
import { useData, useLive } from "../live";
import Markdown from "../components/Markdown";
import { Button, Card, Empty, IconButton, Segmented, Spinner, Stat } from "../components/ui";
import { CategoryBars, WeekBars } from "../components/widgets";
import { addDays, cx, fa, isoDate, parseLocal, serverNow, shortDuration, weekStart } from "../util";

export default function Reports() {
  const { toast, bump } = useLive();
  const { data } = useData<{ reports: Report[] }>("/api/reports", ["reports"]);
  const [selected, setSelected] = useState<number | null>(null);
  const [part, setPart] = useState<"work" | "personal">("work");
  const [busy, setBusy] = useState<number | null>(null);
  const reports = useMemo(() => data?.reports ?? [], [data]);
  const current = reports.find((r) => r.id === selected) ?? reports[0];
  useEffect(() => {
    if (current && selected === null) setSelected(current.id);
  }, [current, selected]);

  const ws = current ? current.week_start : isoDate(weekStart(serverNow()));
  const stats = useData<Stats>(`/api/stats?start=${ws}&end=${isoDate(addDays(parseLocal(ws)!, 6))}`, ["stats", "reports"]);

  const generate = async (offset: number) => {
    setBusy(offset);
    try {
      const r = await api.post<{ report: Report }>("/api/reports/generate", { week_offset: offset });
      bump("reports");
      setSelected(r.report.id);
      toast("گزارش آماده شد ✨", "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };
  const text = current ? (part === "work" ? current.work_report : current.personal_report) : "";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast("کپی شد", "success");
    } catch {
      toast("کپی نشد", "error");
    }
  };
  const download = () => {
    const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `report-${current?.week_start}-${part}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const remove = async () => {
    if (!current || !confirm("این گزارش حذف شود؟")) return;
    await api.del(`/api/reports/${current.id}`);
    setSelected(null);
    bump("reports");
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-black">گزارش‌های هفتگی</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">یک نسخه رسمی برای کارفرما + یک مرور صادقانه برای خودت.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => generate(-1)} loading={busy === -1}>
            هفته قبل
          </Button>
          <Button onClick={() => generate(0)} loading={busy === 0}>
            <Sparkles className="size-4" /> گزارش این هفته
          </Button>
        </div>
      </div>

      {stats.data && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat label="کار مفید هفته" value={shortDuration(stats.data.total_productive_minutes)} sub={`میانگین روزانه ${shortDuration(stats.data.average_per_day_minutes)}`} icon={<Sparkles className="size-5" />} />
          <Stat label="کارهای انجام‌شده" value={fa(stats.data.completed_tasks.length)} icon={<FileText className="size-5" />} tone="emerald" />
          <Stat
            label="بلوک‌های برنامه"
            value={`${fa(stats.data.blocks.done)} / ${fa(stats.data.blocks.planned)}`}
            sub={stats.data.goal_achievement_percent !== null ? `تحقق هدف: ${fa(stats.data.goal_achievement_percent)}٪` : undefined}
            icon={<Briefcase className="size-5" />}
            tone="amber"
          />
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[260px_1fr]">
        <Card title="آرشیو" className="h-fit">
          {data ? (
            reports.length ? (
              <ul className="space-y-1">
                {reports.map((r) => (
                  <li key={r.id}>
                    <button
                      onClick={() => setSelected(r.id)}
                      className={cx(
                        "w-full rounded-xl px-3 py-2 text-start text-sm transition",
                        current?.id === r.id ? "bg-brand-50 font-semibold text-brand-700 dark:bg-brand-500/15 dark:text-brand-200" : "hover:bg-slate-50 dark:hover:bg-slate-800",
                      )}
                    >
                      {r.label}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-500">هنوز گزارشی ساخته نشده. هر هفته خودکار ساخته و در تلگرام ارسال می‌شود.</p>
            )
          ) : (
            <Spinner />
          )}
        </Card>

        <div className="space-y-5">
          {current ? (
            <Card>
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <Segmented
                  value={part}
                  onChange={setPart}
                  options={[
                    { value: "work", label: "💼 برای کارفرما" },
                    { value: "personal", label: "🙋 مرور شخصی" },
                  ]}
                />
                <div className="flex items-center gap-1">
                  <IconButton title="کپی" onClick={copy}>
                    <Copy className="size-4" />
                  </IconButton>
                  <IconButton title="دانلود Markdown" onClick={download}>
                    <Download className="size-4" />
                  </IconButton>
                  <IconButton title="حذف" onClick={remove}>
                    <Trash2 className="size-4" />
                  </IconButton>
                </div>
              </div>
              <div className="rounded-2xl bg-slate-50/70 p-4 sm:p-6 dark:bg-slate-800/30">
                {part === "work" ? <Briefcase className="mb-2 size-5 text-sky-500" /> : <User className="mb-2 size-5 text-violet-500" />}
                <Markdown>{text || "_خالی_"}</Markdown>
              </div>
            </Card>
          ) : (
            <Card>
              <Empty icon={<FileText className="size-6" />} title="گزارشی برای نمایش نیست">
                دکمه «گزارش این هفته» رو بزن تا از روی کارهای ثبت‌شده گزارش بسازم.
              </Empty>
            </Card>
          )}
          {stats.data && (
            <div className="grid gap-5 md:grid-cols-2">
              <Card title="روزبه‌روز">
                <WeekBars days={stats.data.days} goal={stats.data.daily_goal_minutes} />
              </Card>
              <Card title="به تفکیک حوزه">
                <CategoryBars items={stats.data.by_category} />
                {stats.data.by_task.length > 0 && (
                  <ul className="mt-4 space-y-1.5 text-sm">
                    {stats.data.by_task.slice(0, 6).map((t) => (
                      <li key={t.task} className="flex justify-between gap-2">
                        <span className="truncate text-slate-600 dark:text-slate-300">{t.task}</span>
                        <span className="font-semibold">{shortDuration(t.minutes)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
