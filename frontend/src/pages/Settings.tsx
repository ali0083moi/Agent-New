import { useEffect, useState, type ReactNode } from "react";
import { Bell, Bot, Brain, CalendarRange, Copy, GraduationCap, Link2Off, Moon, Send, Sparkles, Trash2, UserRound, Wallet, Zap } from "lucide-react";
import { api, type Memory, type SettingsData, type Usage } from "../api";
import { JalaliInput } from "../components/JalaliPicker";
import { PlacesCard } from "../components/Places";
import { currentSubscription, disablePush, enablePush, pushSupported } from "../pwa";
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
            <Row label="جواب صوتی" hint="جواب‌ها را به‌صورت ویس هم بفرستم">
              <Segmented
                value={String(p.voice_replies ?? "when_voice")}
                onChange={(v) => save("voice_replies", v)}
                options={[{ value: "off", label: "خاموش" }, { value: "when_voice", label: "وقتی ویس فرستادی" }, { value: "always", label: "همیشه" }]}
              />
            </Row>
            {tg.outbox && (tg.outbox.pending > 0 || tg.outbox.failed > 0) && (
              <p className="text-xs text-amber-600">
                صف ارسال: {fa(tg.outbox.pending)} پیام در انتظار، {fa(tg.outbox.failed)} ناموفق
                {tg.outbox.last_error ? ` — ${tg.outbox.last_error}` : ""}
              </p>
            )}
          </div>
        )}
      </Card>

      <PushCard enabled={Boolean(p.push_enabled)} onToggle={(v) => save("push_enabled", v)} />

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
        <Row label="زمان حاضر شدن قبل از بیرون رفتن (دقیقه)" hint="به زمان مسیر اضافه می‌شود">{num("prep_minutes")}</Row>
        <Row label="یادآوری «وقت حاضر شدن / راه افتادن»" hint="برای کلاس‌ها و قرارهایی که مکان و زمان مسیرشان معلوم است">
          <Toggle checked={Boolean(p.leave_reminders)} onChange={(v) => save("leave_reminders", v)} />
        </Row>
      </Card>

      <PlacesCard />

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
        <Row label="جلسه برنامه‌ریزی هفته بعد">
          <div className="flex gap-2">
            <Select className="w-32" value={String(p.weekly_planning_day ?? "fri")} onChange={(v) => save("weekly_planning_day", v)} options={WEEKDAYS.map((w) => ({ value: w.key, label: w.label }))} />
            {time("weekly_planning_time")}
          </div>
        </Row>
        <Row label="حالت نجات" hint="وقتی با حجم کار به ددلاین‌ها نمی‌رسی خودم پیشنهاد راه‌حل بدهم">
          <Toggle checked={Boolean(p.rescue_mode)} onChange={(v) => save("rescue_mode", v)} />
        </Row>
        <Row label="گفتگوی جدید خودکار" hint="بعد از چند ساعت بی‌پیامی، تاریخچه چت از نو شروع شود (۰ = هرگز). حافظه بلندمدت می‌ماند و هزینه هر پیام کم می‌ماند">
          {num("session_idle_hours")}
        </Row>
        <Row label="یادآوری‌های تطبیقی" hint="یادآوری‌ها را از ساعت‌هایی که معمولاً جواب نمی‌دهی جابه‌جا کنم">
          <Toggle checked={Boolean(p.adaptive_reminders)} onChange={(v) => save("adaptive_reminders", v)} />
        </Row>
      </Card>

      <Card title="برنامه‌ریزی" icon={<CalendarRange className="size-4 text-emerald-500" />}>
        <Row label="هدف کار مفید روزانه (دقیقه)">{num("daily_focus_goal_minutes")}</Row>
        <Row label="حداکثر کار متمرکز در روز (دقیقه)">{num("max_focus_minutes_per_day")}</Row>
        <Row label="طول هر بلوک کاری (دقیقه)">{num("focus_block_minutes")}</Row>
        <Row label="استراحت بین بلوک‌ها (دقیقه)">{num("break_minutes")}</Row>
        <Row label="زمان رفت‌وآمد پیش‌فرض قبل/بعد کلاس (دقیقه)" hint="فقط وقتی مکان کلاس زمان مسیر ثبت‌شده ندارد">{num("class_buffer_minutes")}</Row>
        <Row label="برنامه‌ریزی دوباره خودکار" hint="وقتی کاری اضافه/عوض شد یا بلوکی از دست رفت">
          <Toggle checked={Boolean(p.auto_replan)} onChange={(v) => save("auto_replan", v)} />
        </Row>
        <Row label="هفته فرد/زوج دانشگاه" hint={p.semester_start ? `شروع ترم: ${faDate(String(p.semester_start))}` : "برای کلاس‌های یک هفته در میان"}>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => save("current_week_parity", "odd")}>این هفته فرد است</Button>
            <Button size="sm" variant="outline" onClick={() => save("current_week_parity", "even")}>این هفته زوج است</Button>
          </div>
        </Row>
      </Card>

      <Card title="آیلتس" icon={<GraduationCap className="size-4 text-sky-500" />}>
        <Row label="نمره هدف">{text("ielts_target_band", "7", "number", "w-24")}</Row>
        <Row label="تاریخ آزمون">
          <div className="w-56">
            <JalaliInput value={(p.ielts_exam_date as string) || null} onChange={(v) => save("ielts_exam_date", v)} withTime={false} placeholder="هنوز مشخص نیست" />
          </div>
        </Row>
        <Row label="ساعت مرور لغت روزانه">{time("vocab_review_time")}</Row>
        <Row label="تعداد لغت در هر مرور">{num("vocab_daily_count")}</Row>
      </Card>

      <Card title="هوش مصنوعی (GapGPT)" icon={<Bot className="size-4 text-fuchsia-500" />}>
        <Row label="وضعیت">
          <Badge className={data.llm.configured ? "!bg-emerald-50 !text-emerald-700 dark:!bg-emerald-500/15 dark:!text-emerald-300" : "!bg-rose-50 !text-rose-700"}>
            {data.llm.configured ? "کلید API تنظیم شده" : "کلید API تنظیم نشده"}
          </Badge>
        </Row>
        <ModelRow label="مدل گفتگو و ابزارها" k="llm_chat_model" current={data.llm.chat_model} envDefault={data.llm.env_defaults.chat} save={save} />
        <ModelRow label="مدل سریع (پیام‌های خودکار)" k="llm_fast_model" current={data.llm.fast_model} envDefault={data.llm.env_defaults.fast} save={save} />
        <ModelRow label="مدل تصویر" k="llm_vision_model" current={data.llm.vision_model} envDefault={data.llm.env_defaults.vision} save={save} />
        <ModelRow label="مدل تصحیح آیلتس" k="llm_ielts_model" current={data.llm.ielts_model} envDefault={data.llm.env_defaults.ielts} save={save} />
        <Row label="صدا / embedding / جایگزین‌ها">
          <code className="ltr text-xs">
            {data.llm.transcribe_model} · {data.llm.tts_model} · {data.llm.embedding_model} · fallback: {data.llm.fallback_models}
          </code>
        </Row>
        <Row label="حافظه معنایی" hint="جستجو در گفتگوهای قدیمی با embedding">
          <Toggle checked={Boolean(p.semantic_memory)} onChange={(v) => save("semantic_memory", v)} />
        </Row>
        <Row label="یادگیری خودکار درباره تو" hint="بعد از هر گفتگو نکته‌های ماندگار را به حافظه اضافه کنم">
          <Toggle checked={Boolean(p.auto_facts)} onChange={(v) => save("auto_facts", v)} />
        </Row>
        <div className="pt-3">
          <Button variant="soft" onClick={testLLM} loading={testing}>
            <Sparkles className="size-4" /> تست اتصال
          </Button>
        </div>
      </Card>

      <UsageCard budget={Number(p.daily_budget_usd ?? 0)} onBudget={(v) => save("daily_budget_usd", v)} />

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


function ModelRow({ label, k, current, envDefault, save }: { label: string; k: string; current: string; envDefault: string; save: (k: string, v: unknown) => void }) {
  const models = useData<{ models: string[] }>("/api/llm/models", ["none"]);
  const [value, setValue] = useState(current === envDefault ? "" : current);
  return (
    <Row label={label} hint={`پیش‌فرض: ${envDefault}`}>
      <input
        list={`models-${k}`}
        dir="ltr"
        value={value}
        placeholder={envDefault}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => value !== (current === envDefault ? "" : current) && save(k, value.trim())}
        className="h-10 w-56 rounded-xl border border-slate-200 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900"
      />
      <datalist id={`models-${k}`}>
        {(models.data?.models ?? []).map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
    </Row>
  );
}

function UsageCard({ budget, onBudget }: { budget: number; onBudget: (v: number) => void }) {
  const { data } = useData<Usage>("/api/usage?days=30", ["messages", "settings", "reports"]);
  const [b, setB] = useState(String(budget || ""));
  const max = Math.max(0.0001, ...(data?.days.map((d) => d.cost) ?? [0]));
  const PURPOSE: Record<string, string> = {
    chat: "گفتگو", compose: "پیام خودکار", vision: "تصویر", transcribe: "ویس", tts: "صدا", embed: "حافظه",
    summary: "خلاصه", facts: "یادگیری", ielts: "آیلتس", report: "گزارش",
  };
  return (
    <Card title="مصرف و هزینه هوش مصنوعی" icon={<Wallet className="size-4 text-emerald-500" />}>
      {data ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <span>
              امروز: <b className="ltr">${(data.budget?.spent_today ?? 0).toFixed(3)}</b>
            </span>
            <span>
              ۳۰ روز اخیر: <b className="ltr">${data.total_cost.toFixed(2)}</b>
            </span>
            {data.budget?.over && <Badge className="!bg-rose-50 !text-rose-700">سقف امروز پر شده — مدل سریع‌تر استفاده می‌شود</Badge>}
          </div>
          <div className="flex h-24 items-end gap-1" dir="ltr">
            {data.days.map((d) => (
              <div key={d.day} title={`${d.day}: $${d.cost.toFixed(3)} · ${d.calls} calls`} className="flex-1 rounded-t bg-gradient-to-t from-emerald-500 to-teal-400" style={{ height: `${Math.max(3, (d.cost / max) * 100)}%` }} />
            ))}
            {!data.days.length && <p className="w-full text-center text-sm text-slate-500">هنوز مصرفی ثبت نشده.</p>}
          </div>
          {data.models.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500">
                  <th className="py-1 text-start font-medium">مدل</th>
                  <th className="text-start font-medium">فراخوانی</th>
                  <th className="text-start font-medium">توکن</th>
                  <th className="text-start font-medium">هزینه</th>
                </tr>
              </thead>
              <tbody>
                {data.models.map((m) => (
                  <tr key={m.model} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="ltr py-1.5 text-xs">{m.model}</td>
                    <td>{fa(m.calls)}{m.failures ? <span className="text-rose-500"> ({fa(m.failures)}✗)</span> : null}</td>
                    <td className="text-xs">{fa(Math.round((m.prompt_tokens + m.completion_tokens) / 1000))}k</td>
                    <td className="ltr text-xs">${m.cost.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {data.purposes.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {data.purposes.map((x) => (
                <Badge key={x.purpose}>
                  {PURPOSE[x.purpose] ?? x.purpose}: <span className="ltr">${x.cost.toFixed(3)}</span>
                </Badge>
              ))}
            </div>
          )}
          <Row label="سقف هزینه روزانه (دلار)" hint="۰ = بدون سقف. بعد از رسیدن به سقف، مدل سریع استفاده و کارهای پس‌زمینه متوقف می‌شوند">
            <input
              type="number"
              min={0}
              step={0.1}
              dir="ltr"
              value={b}
              onChange={(e) => setB(e.target.value)}
              onBlur={() => Number(b || 0) !== budget && onBudget(Number(b || 0))}
              className="h-10 w-24 rounded-xl border border-slate-200 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900"
            />
          </Row>
          <p className="text-[11px] text-slate-400">هزینه‌ها تخمینی و بر اساس قیمت‌های GapGPT هستند (ویس و صدا تقریبی).</p>
        </div>
      ) : (
        <Spinner />
      )}
    </Card>
  );
}

function PushCard({ enabled, onToggle }: { enabled: boolean; onToggle: (v: boolean) => void }) {
  const { toast } = useLive();
  const [subscribed, setSubscribed] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    currentSubscription().then((s) => setSubscribed(Boolean(s))).catch(() => setSubscribed(false));
  }, []);
  const toggle = async () => {
    setBusy(true);
    try {
      if (subscribed) {
        await disablePush();
        setSubscribed(false);
      } else {
        await enablePush();
        setSubscribed(true);
        if (!enabled) onToggle(true);
        toast("نوتیفیکیشن این مرورگر فعال شد 🔔", "success");
      }
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="نوتیفیکیشن مرورگر و نصب اپ" icon={<Bell className="size-4 text-amber-500" />}>
      <p className="mb-2 text-sm leading-7 text-slate-600 dark:text-slate-300">
        اگر تلگرام در دسترس نبود، یادآوری‌ها را به‌صورت نوتیفیکیشن همین مرورگر/گوشی هم می‌فرستم. برای نصب روی گوشی، از منوی مرورگر «افزودن به صفحه اصلی» را بزن.
      </p>
      <Row label="این دستگاه" hint={pushSupported() ? undefined : "این مرورگر پشتیبانی نمی‌کند یا سایت روی HTTPS نیست"}>
        <div className="flex gap-2">
          <Button size="sm" variant={subscribed ? "outline" : "primary"} onClick={toggle} loading={busy} disabled={!pushSupported() || subscribed === null}>
            {subscribed ? "غیرفعال کردن" : "فعال کردن نوتیفیکیشن"}
          </Button>
          {subscribed && (
            <Button size="sm" variant="ghost" onClick={() => api.post("/api/push/test").then(() => toast("ارسال شد", "success")).catch((e) => toast(e.message, "error"))}>
              تست
            </Button>
          )}
        </div>
      </Row>
      <Row label="ارسال نوتیفیکیشن برای پیگیری‌ها">
        <Toggle checked={enabled} onChange={onToggle} />
      </Row>
    </Card>
  );
}
