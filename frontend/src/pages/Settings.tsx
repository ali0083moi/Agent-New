import { useEffect, useState, type ReactNode } from "react";
import { Bot, Brain, CalendarRange, Copy, Link2Off, Moon, Send, Sparkles, Trash2, UserRound, Zap } from "lucide-react";
import { api, type Memory, type SettingsData } from "../api";
import { useData, useLive } from "../live";
import { Badge, Button, Card, Input, Segmented, Select, Spinner, Toggle } from "../components/ui";
import { fa, faDate, WEEKDAYS } from "../util";

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 py-3 last:border-0 dark:border-slate-800">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {hint && <div className="text-xs text-slate-500 dark:text-slate-400">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export default function Settings({ theme, setTheme }: { theme: string; setTheme: (t: string) => void }) {
  const { toast, bump } = useLive();
  const { data, setData } = useData<SettingsData>("/api/settings", ["settings"]);
  const mems = useData<{ memories: Memory[] }>("/api/memories", ["memories"]);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [testing, setTesting] = useState(false);
  useEffect(() => {
    if (data) setDraft(data.prefs);
  }, [data]);

  if (!data) return <div className="flex h-64 items-center justify-center"><Spinner /></div>;
  const p = draft;

  const save = async (key: string, value: unknown) => {
    setDraft((d) => ({ ...d, [key]: value }));
    try {
      const r = await api.patch<{ applied: Record<string, unknown> }>("/api/settings", { [key]: value });
      setData({ ...data, prefs: { ...data.prefs, ...r.applied } });
      bump("followups", "schedule");
      toast("ذخیره شد", "success");
    } catch (e) {
      toast((e as Error).message, "error");
      setDraft(data.prefs);
    }
  };
  const text = (key: string, placeholder = "", type = "text", width = "w-44") => (
    <Input
      type={type}
      className={width}
      defaultValue={String(p[key] ?? "")}
      key={`${key}-${String(data.prefs[key] ?? "")}`}
      placeholder={placeholder}
      onBlur={(e) => {
        const v = type === "number" ? Number(e.target.value) : e.target.value;
        if (String(v) !== String(data.prefs[key] ?? "")) save(key, v);
      }}
    />
  );
  const time = (key: string) => text(key, "", "time", "w-32");
  const num = (key: string) => text(key, "", "number", "w-24");

  const tg = data.telegram;
  const testLLM = async () => {
    setTesting(true);
    try {
      const r = await api.post<{ reply: string }>("/api/llm/test");
      toast(`اتصال برقرار است: ${r.reply}`, "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-4 sm:p-6">
      <h1 className="text-2xl font-black">تنظیمات</h1>

      <Card title="تلگرام" icon={<Send className="size-4 text-sky-500" />}>
        {!tg.configured ? (
          <div className="space-y-2 text-sm leading-7 text-slate-600 dark:text-slate-300">
            <p>توکن ربات تنظیم نشده است. از <b>@BotFather</b> یک ربات بساز و توکن را در فایل <code className="ltr">.env</code> بگذار:</p>
            <pre className="ltr rounded-xl bg-slate-900 p-3 text-xs text-slate-100">TELEGRAM_BOT_TOKEN=123456:ABC...</pre>
            <p>سپس سرور را دوباره اجرا کن.</p>
          </div>
        ) : (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge className={tg.connected ? "!bg-emerald-50 !text-emerald-700 dark:!bg-emerald-500/15 dark:!text-emerald-300" : "!bg-rose-50 !text-rose-700"}>
                {tg.connected ? "ربات متصل است" : "ربات قطع است"}
              </Badge>
              {tg.bot_username && <Badge className="ltr">@{tg.bot_username}</Badge>}
              {tg.owner_bound && <Badge>متصل به: {tg.owner?.name || tg.owner?.username || "حساب شما"}</Badge>}
            </div>
            {tg.last_error && <p className="text-xs text-rose-600">خطا: {tg.last_error}</p>}
            {!tg.owner_bound && tg.pairing_code && (
              <div className="rounded-2xl bg-sky-50 p-4 dark:bg-sky-500/10">
                <p className="mb-2">برای اتصال، این پیام را به ربات{tg.bot_username ? <b className="ltr"> @{tg.bot_username}</b> : ""} بفرست:</p>
                <div className="flex items-center gap-2">
                  <code className="ltr rounded-xl bg-white px-3 py-2 text-base font-bold dark:bg-slate-900">/start {tg.pairing_code}</code>
                  <Button size="sm" variant="ghost" onClick={() => navigator.clipboard.writeText(`/start ${tg.pairing_code}`).then(() => toast("کپی شد", "success"))}>
                    <Copy className="size-4" />
                  </Button>
                  {tg.bot_username && (
                    <a className="text-sky-600 underline" href={`https://t.me/${tg.bot_username}?start=${tg.pairing_code}`} target="_blank" rel="noreferrer">
                      باز کردن ربات
                    </a>
                  )}
                </div>
              </div>
            )}
            {tg.owner_bound && (
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="soft" onClick={() => api.post("/api/telegram/test").then(() => toast("ارسال شد", "success")).catch((e) => toast(e.message, "error"))}>
                  ارسال پیام آزمایشی
                </Button>
                <Button size="sm" variant="danger" onClick={() => confirm("اتصال تلگرام قطع شود؟") && api.post("/api/telegram/unlink").then(() => bump("settings"))}>
                  <Link2Off className="size-4" /> قطع اتصال
                </Button>
              </div>
            )}
            <Row label="همگام‌سازی گفتگوی وب با تلگرام" hint="پیام‌هایی که در وب می‌نویسی و جواب‌ها در تلگرام هم نمایش داده شوند">
              <Toggle checked={Boolean(p.mirror_web_to_telegram)} onChange={(v) => save("mirror_web_to_telegram", v)} />
            </Row>
          </div>
        )}
      </Card>

      <Card title="درباره تو" icon={<UserRound className="size-4 text-brand-500" />}>
        <Row label="اسم تو">{text("user_name", "مثلاً علی")}</Row>
        <Row label="اسم دستیار">{text("assistant_name", "پیگیر")}</Row>
        <Row label="کارفرما / شرکت" hint="در سربرگ گزارش کاری هفتگی">{text("employer_name", "اختیاری")}</Row>
        <Row label="ظاهر">
          <Segmented value={theme} onChange={setTheme} options={[{ value: "light", label: "روشن" }, { value: "dark", label: "تیره" }, { value: "system", label: "خودکار" }]} />
        </Row>
      </Card>

      <Card title="ریتم روزانه" icon={<Moon className="size-4 text-violet-500" />}>
        <Row label="ساعت بیدار شدن" hint="قبل از این ساعت پیامی نمی‌فرستم">{time("wake_time")}</Row>
        <Row label="ساعت خواب">{time("sleep_time")}</Row>
        <Row label="شروع بازه کاری" hint="برنامه‌ریز، کارها را در این بازه می‌چیند">{time("work_start")}</Row>
        <Row label="پایان بازه کاری">{time("work_end")}</Row>
      </Card>

      <Card title="پیگیری‌ها" icon={<Zap className="size-4 text-amber-500" />}>
        <Row label="شدت پیگیری" hint="تعداد یادآوری‌ها و احوال‌پرسی‌ها">
          <Segmented
            value={String(p.followup_intensity ?? "normal")}
            onChange={(v) => save("followup_intensity", v)}
            options={[{ value: "low", label: "کم" }, { value: "normal", label: "معمولی" }, { value: "high", label: "زیاد" }]}
          />
        </Row>
        <Row label="برنامه صبحگاهی">{time("daily_brief_time")}</Row>
        <Row label="جمع‌بندی شبانه">{time("daily_review_time")}</Row>
        <Row label="گزارش هفتگی">
          <div className="flex gap-2">
            <Select className="w-32" value={String(p.weekly_report_day ?? "thu")} onChange={(v) => save("weekly_report_day", v)} options={WEEKDAYS.map((w) => ({ value: w.key, label: w.label }))} />
            {time("weekly_report_time")}
          </div>
        </Row>
        <Row label="سؤال درباره تکلیف بعد از کلاس" hint="چند دقیقه بعد از پایان کلاس">{num("post_class_delay_minutes")}</Row>
        <Row label="احوال‌پرسی وقتی خبری ازت نیست" hint="بعد از چند ساعت بی‌خبری (۰ = خاموش)">{num("idle_checkin_hours")}</Row>
        <Row label="یادآوری شروع و پایان بلوک‌های کاری">
          <Toggle checked={Boolean(p.block_nudges)} onChange={(v) => save("block_nudges", v)} />
        </Row>
        <Row label="برنامه‌ریزی خودکار هر صبح">
          <Toggle checked={Boolean(p.auto_plan_daily)} onChange={(v) => save("auto_plan_daily", v)} />
        </Row>
      </Card>

      <Card title="برنامه‌ریزی" icon={<CalendarRange className="size-4 text-emerald-500" />}>
        <Row label="هدف کار مفید روزانه (دقیقه)">{num("daily_focus_goal_minutes")}</Row>
        <Row label="حداکثر کار متمرکز در روز (دقیقه)">{num("max_focus_minutes_per_day")}</Row>
        <Row label="طول هر بلوک کاری (دقیقه)">{num("focus_block_minutes")}</Row>
        <Row label="استراحت بین بلوک‌ها (دقیقه)">{num("break_minutes")}</Row>
        <Row label="زمان رفت‌وآمد قبل/بعد کلاس (دقیقه)">{num("class_buffer_minutes")}</Row>
        <Row label="هفته فرد/زوج دانشگاه" hint={p.semester_start ? `شروع ترم: ${faDate(String(p.semester_start))}` : "برای کلاس‌های یک هفته در میان"}>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => save("current_week_parity", "odd")}>این هفته فرد است</Button>
            <Button size="sm" variant="outline" onClick={() => save("current_week_parity", "even")}>این هفته زوج است</Button>
          </div>
        </Row>
      </Card>

      <Card title="هوش مصنوعی (GapGPT)" icon={<Bot className="size-4 text-fuchsia-500" />}>
        <Row label="وضعیت">
          <Badge className={data.llm.configured ? "!bg-emerald-50 !text-emerald-700 dark:!bg-emerald-500/15 dark:!text-emerald-300" : "!bg-rose-50 !text-rose-700"}>
            {data.llm.configured ? "کلید API تنظیم شده" : "کلید API تنظیم نشده"}
          </Badge>
        </Row>
        <Row label="مدل گفتگو"><code className="ltr text-xs">{data.llm.chat_model}</code></Row>
        <Row label="مدل سریع / تصویر / صدا">
          <code className="ltr text-xs">{data.llm.fast_model} · {data.llm.vision_model} · {data.llm.transcribe_model}</code>
        </Row>
        <div className="pt-3">
          <Button variant="soft" onClick={testLLM} loading={testing}>
            <Sparkles className="size-4" /> تست اتصال
          </Button>
        </div>
      </Card>

      <Card title="چیزهایی که درباره‌ات می‌دانم" icon={<Brain className="size-4 text-brand-500" />}>
        {mems.data?.memories.length ? (
          <ul className="space-y-1.5">
            {mems.data.memories.map((m) => (
              <li key={m.id} className="group flex items-start gap-2 rounded-xl px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-800/50">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-brand-400" />
                <span className="flex-1 text-sm leading-7">{m.content}</span>
                <button
                  className="text-slate-400 opacity-0 transition hover:text-rose-500 group-hover:opacity-100"
                  title="فراموش کن"
                  onClick={() => api.del(`/api/memories/${m.id}`).then(() => bump("memories"))}
                >
                  <Trash2 className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">هنوز چیزی یاد نگرفته‌ام. در گفتگو از خودت بگو (کارت، اهدافت، عادت‌هات…).</p>
        )}
        <p className="mt-3 text-xs text-slate-400">{fa(mems.data?.memories.length ?? 0)} مورد</p>
      </Card>
    </div>
  );
}
