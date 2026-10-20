import { useState } from "react";
import { Brain, Check, Flame, GraduationCap, HandHeart, Lightbulb, Plus, Repeat, Sparkles, Trophy, X } from "lucide-react";
import { api, type Commitment, type Habit, type Ielts, type Insights, type Vocab } from "../api";
import { useData, useLive } from "../live";
import { Heatmap, HourBars, IeltsChart, SKILL_FA } from "../components/charts";
import { JalaliInput } from "../components/JalaliPicker";
import { ContactsCard } from "../components/Contacts";
import { Badge, Button, Card, Empty, Field, Input, Modal, ProgressBar, Ring, Segmented, Select, Spinner } from "../components/ui";
import { cx, fa, faDate, isoDate, serverNow, shortDuration } from "../util";

const COMPONENT_FA: Record<string, string> = { focus: "کار مفید", deadlines: "ددلاین‌ها", commitments: "قول‌ها", habits: "عادت‌ها" };

function LevelCard({ ins }: { ins: Insights }) {
  const g = ins.game;
  return (
    <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-brand-600 via-violet-600 to-fuchsia-600 p-5 text-white shadow-[var(--shadow-lift)]">
      <div className="absolute -start-10 -top-16 size-48 rounded-full bg-white/10 blur-2xl" />
      <div className="relative flex flex-wrap items-center justify-between gap-5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-sm text-white/80">
            <Trophy className="size-4" /> سطح {fa(g.level)}
          </div>
          <div className="mt-1 text-3xl font-black">{g.title}</div>
          <div className="mt-3 h-2 w-full max-w-sm overflow-hidden rounded-full bg-white/20">
            <div className="h-full rounded-full bg-white" style={{ width: `${Math.round(g.progress * 100)}%` }} />
          </div>
          <div className="mt-1.5 text-xs text-white/80">
            {fa(g.points)} امتیاز · {fa(g.next_level_at - g.points)} امتیاز تا سطح بعد · این هفته {fa(g.week_points)} امتیاز
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="rounded-2xl bg-white/15 p-3 text-center backdrop-blur">
            <div className="text-xs text-white/80">نمره این هفته</div>
            <div className="text-3xl font-black">{fa(g.weekly_score.score)}</div>
            <div className="text-[11px] text-white/70">هفته قبل: {fa(g.last_week_score.score)}</div>
          </div>
          <div className="hidden space-y-1 text-xs sm:block">
            {Object.entries(g.weekly_score.components).map(([k, v]) => (
              <div key={k} className="flex items-center gap-2">
                <span className="w-16 text-white/80">{COMPONENT_FA[k] ?? k}</span>
                <div className="h-1.5 w-20 overflow-hidden rounded-full bg-white/20">
                  <div className="h-full bg-white" style={{ width: `${v}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function HabitRow({ h }: { h: Habit }) {
  const { toast, bump } = useLive();
  const [busy, setBusy] = useState(false);
  const log = async () => {
    const v = h.unit === "times" ? 1 : Number(prompt(`چند ${h.unit_fa}؟`, String(h.target)) || 0);
    if (!v) return;
    setBusy(true);
    try {
      const r = await api.post<{ habit: Habit }>(`/api/habits/${h.id}/log`, { value: v });
      bump("habits", "stats");
      toast(r.habit.streak ? `🔥 ${fa(r.habit.streak)} روز پشت‌سرهم!` : "ثبت شد", "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-slate-100 p-3 dark:border-slate-800">
      <div className={cx("flex size-11 shrink-0 flex-col items-center justify-center rounded-xl", h.streak ? "bg-orange-50 text-orange-600 dark:bg-orange-500/15 dark:text-orange-300" : "bg-slate-100 text-slate-400 dark:bg-slate-800")}>
        <Flame className="size-4" />
        <span className="text-xs font-black">{fa(h.streak)}</span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">{h.title}</span>
          <span className="text-xs text-slate-500">
            {fa(h.target)} {h.unit_fa} / {h.period === "daily" ? "روز" : "هفته"}
          </span>
          {!h.active && <Badge>متوقف</Badge>}
        </div>
        <div className="mt-1.5 flex items-center gap-1">
          {h.last7.map((d) => (
            <span key={d.date} title={`${faDate(d.date, true)}: ${fa(d.value)}`} className={cx("size-3 rounded-full", d.met ? "bg-emerald-500" : d.value > 0 ? "bg-emerald-200 dark:bg-emerald-800" : "bg-slate-200 dark:bg-slate-700")} />
          ))}
          <span className="ms-2 text-[11px] text-slate-400">بهترین: {fa(h.best_streak)}</span>
        </div>
      </div>
      <Button size="sm" variant={h.met_now ? "soft" : "primary"} onClick={log} loading={busy}>
        {h.met_now ? <Check className="size-4" /> : <Plus className="size-4" />} {h.met_now ? "انجام شد" : "ثبت"}
      </Button>
    </div>
  );
}

function NewHabit({ onClose }: { onClose: () => void }) {
  const { toast, bump } = useLive();
  const [title, setTitle] = useState("");
  const [unit, setUnit] = useState("minutes");
  const [target, setTarget] = useState("30");
  const [period, setPeriod] = useState<"daily" | "weekly">("daily");
  const [reminder, setReminder] = useState("");
  const save = async () => {
    try {
      await api.post("/api/habits", { title, unit, target: Number(target), period, reminder_time: reminder || undefined });
      bump("habits", "followups");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <Modal open onClose={onClose} title="عادت جدید" footer={<Button onClick={save} disabled={!title.trim()}>ذخیره</Button>}>
      <div className="space-y-3">
        <Field label="عنوان">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="مثلاً: ورزش، کتاب‌خوانی، خواب ۷ ساعت" autoFocus />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="هدف">
            <Input type="number" min={1} value={target} onChange={(e) => setTarget(e.target.value)} />
          </Field>
          <Field label="واحد">
            <Select value={unit} onChange={setUnit} options={[{ value: "minutes", label: "دقیقه" }, { value: "hours", label: "ساعت" }, { value: "times", label: "بار" }]} />
          </Field>
        </div>
        <Field label="دوره">
          <Segmented value={period} onChange={setPeriod} options={[{ value: "daily", label: "روزانه" }, { value: "weekly", label: "هفتگی" }]} />
        </Field>
        <Field label="یادآوری (اختیاری)" hint="اگر تا این ساعت انجام نشده باشد یادآوری می‌کنم">
          <Input type="time" value={reminder} onChange={(e) => setReminder(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

function Commitments() {
  const { toast, bump } = useLive();
  const { data } = useData<{ commitments: Commitment[] }>("/api/commitments", ["commitments", "stats"]);
  const [text, setText] = useState("");
  const add = async () => {
    if (!text.trim()) return;
    await api.post("/api/commitments", { text });
    setText("");
    bump("commitments");
  };
  const set = async (c: Commitment, status: string) => {
    try {
      await api.patch(`/api/commitments/${c.id}`, { status });
      bump("commitments");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const STATUS: Record<string, string> = { pending: "در انتظار", done: "انجام شد ✅", partial: "نصفه", missed: "انجام نشد" };
  return (
    <Card title="قول‌های امروز" icon={<HandHeart className="size-4 text-rose-500" />}>
      <div className="space-y-2">
        {data?.commitments.length ? (
          data.commitments.map((c) => (
            <div key={c.id} className="rounded-2xl border border-slate-100 p-3 dark:border-slate-800">
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium">{c.text}</span>
                <Badge className={c.status === "done" ? "!bg-emerald-50 !text-emerald-700" : c.status === "missed" ? "!bg-rose-50 !text-rose-700" : ""}>{STATUS[c.status]}</Badge>
              </div>
              {c.target_minutes ? (
                <div className="mt-2 flex items-center gap-2">
                  <ProgressBar value={(100 * (c.logged_minutes ?? 0)) / c.target_minutes} tone="emerald" className="max-w-56" />
                  <span className="text-xs text-slate-500">
                    {shortDuration(c.logged_minutes ?? 0)} از {shortDuration(c.target_minutes)}
                  </span>
                </div>
              ) : null}
              {c.status === "pending" && (
                <div className="mt-2 flex gap-1">
                  <Button size="sm" variant="soft" onClick={() => set(c, "done")}>انجام شد</Button>
                  <Button size="sm" variant="ghost" onClick={() => set(c, "partial")}>نصفه</Button>
                  <Button size="sm" variant="ghost" onClick={() => set(c, "missed")}>نشد</Button>
                </div>
              )}
            </div>
          ))
        ) : (
          <p className="text-sm text-slate-500">امروز قولی ثبت نشده. صبح‌ها ازت می‌پرسم؛ یا همین‌جا بنویس.</p>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
          className="flex gap-2"
        >
          <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="امروز قول می‌دم…" />
          <Button type="submit" variant="soft">
            <Plus className="size-4" />
          </Button>
        </form>
      </div>
    </Card>
  );
}

function VocabTrainer() {
  const { toast, bump } = useLive();
  const { data } = useData<{ words: Vocab[]; stats: Ielts["vocab"] }>("/api/vocab?due=true", ["ielts"]);
  const [reveal, setReveal] = useState(false);
  const [newWord, setNewWord] = useState("");
  const word = data?.words[0];
  const answer = async (correct: boolean) => {
    if (!word) return;
    await api.post(`/api/vocab/${word.id}/review`, { correct });
    setReveal(false);
    bump("ielts");
  };
  const add = async () => {
    if (!newWord.trim()) return;
    try {
      await api.post("/api/vocab", { words: [{ word: newWord.trim() }] });
      setNewWord("");
      bump("ielts");
      toast("اضافه شد — معنی‌اش رو در گفتگو هم می‌تونی بپرسی", "success");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div className="space-y-3">
      {word ? (
        <div className="rounded-2xl bg-gradient-to-br from-sky-50 to-emerald-50 p-5 text-center dark:from-sky-500/10 dark:to-emerald-500/10">
          <div className="text-xs text-slate-500">
            مرور امروز · {fa(data!.words.length)} لغت مانده · جعبه {fa(word.box)}
          </div>
          <div className="ltr mt-2 text-2xl font-black">{word.word}</div>
          {reveal ? (
            <>
              <div className="mt-2 text-lg">{word.meaning || "—"}</div>
              {word.example && <div className="ltr mt-1 text-sm italic text-slate-500">{word.example}</div>}
              <div className="mt-4 flex justify-center gap-2">
                <Button variant="soft" onClick={() => answer(true)}>
                  <Check className="size-4" /> بلد بودم
                </Button>
                <Button variant="danger" onClick={() => answer(false)}>
                  <X className="size-4" /> بلد نبودم
                </Button>
              </div>
            </>
          ) : (
            <Button className="mt-4" variant="outline" onClick={() => setReveal(true)}>
              نشونم بده
            </Button>
          )}
        </div>
      ) : (
        <p className="text-sm text-slate-500">لغتی برای مرور امروز نمانده 🎉 ({fa(data?.stats.total ?? 0)} لغت، {fa(data?.stats.mastered ?? 0)} مسلط)</p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          add();
        }}
        className="flex gap-2"
      >
        <Input className="ltr" value={newWord} onChange={(e) => setNewWord(e.target.value)} placeholder="new word…" />
        <Button type="submit" variant="soft">
          <Plus className="size-4" />
        </Button>
      </form>
    </div>
  );
}

function IeltsCard() {
  const { toast, bump } = useLive();
  const { data } = useData<Ielts>("/api/ielts", ["ielts"]);
  const [adding, setAdding] = useState(false);
  const [skill, setSkill] = useState("listening");
  const [band, setBand] = useState("6.5");
  const [day, setDay] = useState<string | null>(isoDate(serverNow()));
  if (!data) return <Spinner />;
  const save = async () => {
    try {
      await api.post("/api/ielts/scores", { skill, band: Number(band), day: day ?? undefined, source: "mock" });
      bump("ielts");
      setAdding(false);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <Card
      title="آیلتس"
      icon={<GraduationCap className="size-4 text-sky-500" />}
      action={
        <Button size="sm" variant="soft" onClick={() => setAdding(true)}>
          <Plus className="size-4" /> ثبت نمره
        </Button>
      }
    >
      <div className="mb-4 flex flex-wrap gap-2">
        <Badge className="!bg-rose-50 !text-rose-700 dark:!bg-rose-500/15 dark:!text-rose-300">هدف: {fa(data.target_band)}</Badge>
        {data.days_left !== null && <Badge>⏳ {fa(data.days_left)} روز تا آزمون</Badge>}
        {Object.entries(data.latest).map(([k, v]) => (
          <Badge key={k}>
            {SKILL_FA[k] ?? k}: {fa(v.last)}
          </Badge>
        ))}
      </div>
      <IeltsChart series={data.series} target={data.target_band} />
      <div className="mt-5 border-t border-slate-100 pt-4 dark:border-slate-800">
        <div className="mb-2 flex items-center gap-2 text-sm font-bold">
          <Repeat className="size-4 text-emerald-500" /> مرور لغت (جعبه لایتنر)
        </div>
        <VocabTrainer />
      </div>
      {adding && (
        <Modal open onClose={() => setAdding(false)} title="ثبت نمره آیلتس" footer={<Button onClick={save}>ذخیره</Button>}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="مهارت">
              <Select value={skill} onChange={setSkill} options={Object.entries(SKILL_FA).map(([value, label]) => ({ value, label }))} />
            </Field>
            <Field label="نمره (band)">
              <Input type="number" min={0} max={9} step={0.5} value={band} onChange={(e) => setBand(e.target.value)} />
            </Field>
            <div className="col-span-2">
              <Field label="تاریخ">
                <JalaliInput value={day} onChange={setDay} withTime={false} />
              </Field>
            </div>
          </div>
        </Modal>
      )}
    </Card>
  );
}

export default function Progress() {
  const ins = useData<Insights>("/api/insights", ["stats", "tasks", "habits", "commitments", "ielts"]);
  const heat = useData<{ days: { date: string; minutes: number }[]; goal_minutes: number }>("/api/stats/heatmap?days=371", ["stats"]);
  const habits = useData<{ habits: Habit[] }>("/api/habits", ["habits", "stats"]);
  const [newHabit, setNewHabit] = useState(false);

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-4 sm:p-6">
      <div>
        <h1 className="text-2xl font-black">پیشرفت</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">امتیاز، عادت‌ها، آیلتس و چیزهایی که از رفتارت یاد گرفته‌ام.</p>
      </div>
      {ins.data ? <LevelCard ins={ins.data} /> : <Spinner />}

      <Card title="یک سال کار مفید" icon={<Sparkles className="size-4 text-brand-500" />}>
        {heat.data ? <Heatmap days={heat.data.days} goal={heat.data.goal_minutes} /> : <Spinner />}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card
          title="عادت‌ها"
          icon={<Flame className="size-4 text-orange-500" />}
          action={
            <Button size="sm" variant="soft" onClick={() => setNewHabit(true)}>
              <Plus className="size-4" /> عادت جدید
            </Button>
          }
        >
          {habits.data?.habits.length ? (
            <div className="space-y-2">
              {habits.data.habits.map((h) => (
                <HabitRow key={h.id} h={h} />
              ))}
            </div>
          ) : (
            <Empty icon={<Flame className="size-6" />} title="هنوز عادتی نداری">
              مثلاً بگو «می‌خوام هر روز ۲۰ دقیقه کتاب بخونم» یا از دکمه بالا اضافه کن.
            </Empty>
          )}
        </Card>
        <Commitments />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <ContactsCard />
        <IeltsCard />
      </div>

      {ins.data && (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card title="ساعت‌های اوج تمرکز" icon={<Brain className="size-4 text-amber-500" />}>
            <HourBars profile={ins.data.hour_profile} peaks={ins.data.peak_hours} />
            <p className="mt-3 text-xs text-slate-500">
              {ins.data.peak_hours_learned ? "کارهای عمیق (⚡) را در ساعت‌های نارنجی می‌چینم." : "هنوز داده کافی نیست؛ فعلاً صبح‌ها را ساعت اوج فرض کرده‌ام."}
            </p>
          </Card>
          <Card title="چیزهایی که یاد گرفته‌ام" icon={<Lightbulb className="size-4 text-yellow-500" />}>
            {[...ins.data.estimation_text, ...ins.data.reminders_text].length ? (
              <ul className="space-y-2 text-sm leading-7">
                {[...ins.data.estimation_text, ...ins.data.reminders_text].map((t) => (
                  <li key={t} className="flex gap-2">
                    <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-yellow-500" />
                    {t}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-500">هر چه بیشتر کار ثبت کنی، دقیق‌تر می‌فهمم کارهات واقعاً چقدر طول می‌کشن و کدوم یادآوری‌ها به دردت می‌خورن.</p>
            )}
            <div className="mt-4 flex items-center gap-4 border-t border-slate-100 pt-4 dark:border-slate-800">
              <Ring value={ins.data.game.weekly_score.score / 100} size={64} stroke={7}>
                <span className="text-sm font-black">{fa(ins.data.game.weekly_score.score)}</span>
              </Ring>
              <p className="text-xs leading-6 text-slate-500">
                نمره هفته از کار مفید نسبت به هدف، ددلاین‌های به‌موقع، قول‌های عمل‌شده و عادت‌ها حساب می‌شود.
              </p>
            </div>
          </Card>
        </div>
      )}
      {newHabit && <NewHabit onClose={() => setNewHabit(false)} />}
    </div>
  );
}
