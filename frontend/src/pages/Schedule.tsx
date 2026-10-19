import { useMemo, useState } from "react";
import { BellRing, Camera, ChevronLeft, ChevronRight, GraduationCap, Pencil, Plus, Trash2, Wand2 } from "lucide-react";
import { api, type AgendaDay, type AgendaItem, type Recurring } from "../api";
import { useData, useLive } from "../live";
import { Badge, Button, Card, Empty, Field, IconButton, Input, Modal, Segmented, Select, Toggle } from "../components/ui";
import { Timeline } from "../components/widgets";
import { addDays, cx, fa, faDate, faTime, isoDate, parseLocal, serverNow, WEEKDAYS, weekStart } from "../util";

const START_H = 7;
const END_H = 24;
const HOUR_PX = 46;

const ITEM_STYLE: Record<string, string> = {
  class: "bg-violet-100 border-violet-300 text-violet-900 dark:bg-violet-500/20 dark:border-violet-500/40 dark:text-violet-100",
  event: "bg-amber-100 border-amber-300 text-amber-900 dark:bg-amber-500/20 dark:border-amber-500/40 dark:text-amber-100",
  block: "bg-emerald-50 border-emerald-300 border-dashed text-emerald-900 dark:bg-emerald-500/10 dark:border-emerald-500/40 dark:text-emerald-100",
  deadline: "bg-rose-500 border-rose-600 text-white",
};

function minutesOf(s: string): number {
  const d = parseLocal(s)!;
  return d.getHours() * 60 + d.getMinutes();
}

function WeekGrid({ days }: { days: AgendaDay[] }) {
  const todayIso = isoDate(serverNow());
  const now = serverNow();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const hours = Array.from({ length: END_H - START_H }, (_, i) => START_H + i);
  return (
    <div className="card overflow-x-auto p-0">
      <div className="min-w-[760px]">
        <div className="grid grid-cols-[52px_repeat(7,1fr)] border-b border-slate-200 dark:border-slate-800">
          <div />
          {days.map((d) => (
            <div key={d.date} className={cx("px-2 py-2.5 text-center", d.date === todayIso && "bg-brand-50/70 dark:bg-brand-500/10")}>
              <div className={cx("text-sm font-bold", d.date === todayIso && "text-brand-600 dark:text-brand-300")}>{d.weekday_fa}</div>
              <div className="text-xs text-slate-500">{faDate(d.date, true)}</div>
            </div>
          ))}
        </div>
        <div className="relative grid grid-cols-[52px_repeat(7,1fr)]" style={{ height: (END_H - START_H) * HOUR_PX }}>
          <div className="relative">
            {hours.map((h) => (
              <div key={h} className="absolute end-2 -translate-y-2 text-[11px] text-slate-400" style={{ top: (h - START_H) * HOUR_PX }}>
                {fa(String(h).padStart(2, "0"))}:۰۰
              </div>
            ))}
          </div>
          {days.map((d) => (
            <div key={d.date} className={cx("relative border-s border-slate-100 dark:border-slate-800/80", d.date === todayIso && "bg-brand-50/40 dark:bg-brand-500/5")}>
              {hours.map((h) => (
                <div key={h} className="absolute inset-x-0 border-t border-slate-100 dark:border-slate-800/60" style={{ top: (h - START_H) * HOUR_PX }} />
              ))}
              {d.date === todayIso && nowMin >= START_H * 60 && (
                <div className="absolute inset-x-0 z-20 h-0.5 bg-rose-500" style={{ top: ((nowMin - START_H * 60) / 60) * HOUR_PX }}>
                  <span className="absolute -start-1 -top-1 size-2.5 rounded-full bg-rose-500" />
                </div>
              )}
              {d.items.map((it: AgendaItem, i) => {
                const s = Math.max(minutesOf(it.start), START_H * 60);
                const sameDay = it.end.slice(0, 10) === d.date;
                const e = it.type === "deadline" ? s + 22 : sameDay ? Math.min(minutesOf(it.end), END_H * 60) : END_H * 60;
                if (e <= START_H * 60) return null;
                const top = ((s - START_H * 60) / 60) * HOUR_PX;
                const height = Math.max(((e - s) / 60) * HOUR_PX - 2, 20);
                return (
                  <div
                    key={`${it.type}-${it.ref_id}-${i}`}
                    title={`${it.title} · ${faTime(it.start)}${it.type !== "deadline" ? " تا " + faTime(it.end) : ""}`}
                    className={cx("absolute inset-x-1 z-10 overflow-hidden rounded-lg border px-1.5 py-1 text-[11px] leading-4", ITEM_STYLE[it.type])}
                    style={{ top, height }}
                  >
                    <div className="truncate font-semibold">{it.type === "deadline" ? "⏰ " : ""}{it.title}</div>
                    {height > 32 && <div className="truncate opacity-75">{faTime(it.start)}{it.type !== "deadline" && ` - ${faTime(it.end)}`}</div>}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const KINDS = [
  { value: "university_class", label: "کلاس دانشگاه" },
  { value: "language_class", label: "کلاس زبان" },
  { value: "work", label: "کار" },
  { value: "exam", label: "امتحان" },
  { value: "sport", label: "ورزش" },
  { value: "other", label: "سایر" },
];

function RecurringEditor({ rec, onClose }: { rec?: Recurring; onClose: () => void }) {
  const { toast, bump } = useLive();
  const [title, setTitle] = useState(rec?.title ?? "");
  const [kind, setKind] = useState(rec?.kind ?? "university_class");
  const [days, setDays] = useState<string[]>(rec?.weekdays ?? []);
  const [start, setStart] = useState(rec?.start_time ?? "08:00");
  const [end, setEnd] = useState(rec?.end_time ?? "10:00");
  const [location, setLocation] = useState(rec?.location ?? "");
  const [instructor, setInstructor] = useState(rec?.instructor ?? "");
  const [parity, setParity] = useState<"all" | "odd" | "even">(rec?.week_parity ?? "all");
  const [followup, setFollowup] = useState(rec?.followup ?? true);
  const [active, setActive] = useState(rec?.active ?? true);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!title.trim() || !days.length) return toast("عنوان و روزهای هفته لازم است", "error");
    setBusy(true);
    const body = { title, kind, weekdays: days, start_time: start, end_time: end, location, instructor, week_parity: parity, followup, active };
    try {
      if (rec) await api.patch(`/api/recurring/${rec.id}`, body);
      else await api.post("/api/recurring", body);
      bump("schedule", "followups");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!rec || !confirm("این کلاس حذف شود؟")) return;
    await api.del(`/api/recurring/${rec.id}`);
    bump("schedule");
    onClose();
  };
  return (
    <Modal
      open
      onClose={onClose}
      title={rec ? "ویرایش کلاس / برنامه ثابت" : "کلاس / برنامه ثابت جدید"}
      footer={
        <>
          {rec && (
            <Button variant="danger" className="me-auto" onClick={remove}>
              <Trash2 className="size-4" /> حذف
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            انصراف
          </Button>
          <Button onClick={save} loading={busy}>
            ذخیره
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <Field label="عنوان">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="مثلاً: پایگاه داده" autoFocus />
            </Field>
          </div>
          <Field label="نوع">
            <Select value={kind} onChange={setKind} options={KINDS} />
          </Field>
        </div>
        <Field label="روزها">
          <div className="flex flex-wrap gap-1.5">
            {WEEKDAYS.map((w) => (
              <button
                key={w.key}
                type="button"
                onClick={() => setDays((d) => (d.includes(w.key) ? d.filter((x) => x !== w.key) : [...d, w.key]))}
                className={cx("rounded-xl px-3 py-1.5 text-sm transition", days.includes(w.key) ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300")}
              >
                {w.label}
              </button>
            ))}
          </div>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="شروع">
            <Input type="time" value={start} onChange={(e) => setStart(e.target.value)} />
          </Field>
          <Field label="پایان">
            <Input type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
          </Field>
          <Field label="مکان">
            <Input value={location} onChange={(e) => setLocation(e.target.value)} />
          </Field>
          <Field label="استاد">
            <Input value={instructor} onChange={(e) => setInstructor(e.target.value)} />
          </Field>
        </div>
        <Field label="تکرار">
          <Segmented value={parity} onChange={setParity} options={[{ value: "all", label: "هر هفته" }, { value: "odd", label: "هفته‌های فرد" }, { value: "even", label: "هفته‌های زوج" }]} />
        </Field>
        <div className="flex items-center justify-between rounded-xl bg-slate-50 p-3 dark:bg-slate-800/50">
          <div>
            <div className="text-sm font-medium">بعد از هر جلسه درباره تکلیف بپرس</div>
            <div className="text-xs text-slate-500">و تا جلسه بعد پیگیر انجامش باش</div>
          </div>
          <Toggle checked={followup} onChange={setFollowup} />
        </div>
        {rec && (
          <div className="flex items-center justify-between rounded-xl bg-slate-50 p-3 dark:bg-slate-800/50">
            <div className="text-sm font-medium">فعال</div>
            <Toggle checked={active} onChange={setActive} />
          </div>
        )}
      </div>
    </Modal>
  );
}

export default function Schedule({ go }: { go: (page: string) => void }) {
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<Recurring | null | "new">(null);
  const [planning, setPlanning] = useState(false);
  const { toast, bump } = useLive();
  const start = useMemo(() => addDays(weekStart(serverNow()), offset * 7), [offset]);
  const { data } = useData<{ days: AgendaDay[] }>(`/api/agenda?start=${isoDate(start)}&days=7`, ["schedule", "tasks"]);
  const recs = useData<{ recurring: Recurring[] }>("/api/recurring", ["schedule"]);
  const end = addDays(start, 6);

  const replan = async () => {
    setPlanning(true);
    try {
      const r = await api.post<{ blocks: unknown[]; warnings: string[] }>("/api/plan", { days: 7 });
      bump("schedule", "followups");
      if (r.warnings.length) r.warnings.forEach((w) => toast(w, "error"));
      else toast(`${fa(r.blocks.length)} بلوک کاری برنامه‌ریزی شد`, "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setPlanning(false);
    }
  };
  const pickImage = () => {
    go("chat");
    setTimeout(() => window.dispatchEvent(new Event("peygir:pick-image")), 150);
  };

  return (
    <div className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-black">برنامه هفتگی</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {faDate(start, true)} تا {faDate(end, true)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center rounded-xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
            <IconButton title="هفته قبل" onClick={() => setOffset((o) => o - 1)}>
              <ChevronRight className="size-5" />
            </IconButton>
            <button onClick={() => setOffset(0)} className="px-2 text-sm font-medium">
              این هفته
            </button>
            <IconButton title="هفته بعد" onClick={() => setOffset((o) => o + 1)}>
              <ChevronLeft className="size-5" />
            </IconButton>
          </div>
          <Button variant="soft" onClick={replan} loading={planning}>
            <Wand2 className="size-4" /> برنامه‌ریزی دوباره
          </Button>
          <Button variant="outline" onClick={pickImage}>
            <Camera className="size-4" /> عکس برنامه کلاسی
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-3 text-xs text-slate-500">
        <span className="flex items-center gap-1.5"><span className="size-3 rounded bg-violet-300" /> کلاس / برنامه ثابت</span>
        <span className="flex items-center gap-1.5"><span className="size-3 rounded bg-amber-300" /> رویداد</span>
        <span className="flex items-center gap-1.5"><span className="size-3 rounded border border-dashed border-emerald-400 bg-emerald-50" /> بلوک کاری پیشنهادی</span>
        <span className="flex items-center gap-1.5"><span className="size-3 rounded bg-rose-500" /> ددلاین</span>
      </div>

      {data && (
        <>
          <div className="hidden md:block">
            <WeekGrid days={data.days} />
          </div>
          <div className="space-y-3 md:hidden">
            {data.days.map((d) => (
              <Card key={d.date} title={`${d.weekday_fa} · ${faDate(d.date, true)}`}>
                <Timeline items={d.items} empty="آزاد" />
              </Card>
            ))}
          </div>
        </>
      )}

      <Card
        title="کلاس‌ها و برنامه‌های ثابت"
        icon={<GraduationCap className="size-4 text-violet-500" />}
        action={
          <Button size="sm" variant="soft" onClick={() => setEditing("new")}>
            <Plus className="size-4" /> افزودن
          </Button>
        }
      >
        {recs.data?.recurring.length ? (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {recs.data.recurring.map((r) => (
              <div key={r.id} className={cx("group flex items-start gap-3 rounded-2xl border border-slate-200 p-3 dark:border-slate-800", !r.active && "opacity-50")}>
                <div className="rounded-xl bg-violet-50 p-2 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300">
                  <GraduationCap className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-semibold">{r.title}</div>
                  <div className="mt-0.5 text-xs text-slate-500">
                    {r.weekdays_fa.join("، ")} · {fa(r.start_time)} تا {fa(r.end_time)}
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    <Badge>{r.kind_fa}</Badge>
                    {r.week_parity !== "all" && <Badge>{r.week_parity === "odd" ? "فرد" : "زوج"}</Badge>}
                    {r.location && <Badge>{r.location}</Badge>}
                    {r.followup && (
                      <Badge className="!bg-amber-50 !text-amber-700 dark:!bg-amber-500/15 dark:!text-amber-300">
                        <BellRing className="size-3" /> پیگیری تکلیف
                      </Badge>
                    )}
                  </div>
                </div>
                <IconButton title="ویرایش" onClick={() => setEditing(r)}>
                  <Pencil className="size-4" />
                </IconButton>
              </div>
            ))}
          </div>
        ) : (
          <Empty icon={<GraduationCap className="size-6" />} title="هنوز کلاسی ثبت نشده">
            عکس برنامه کلاسی‌ات رو توی گفتگو بفرست یا بگو مثلاً «سه‌شنبه و پنجشنبه ۶ تا ۸ شب کلاس زبان دارم».
          </Empty>
        )}
      </Card>
      {editing && <RecurringEditor rec={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}
