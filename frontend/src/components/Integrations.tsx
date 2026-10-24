import { useState } from "react";
import { CalendarSync, Copy, Mail, RefreshCw } from "lucide-react";
import { api } from "../api";
import { useData, useLive } from "../live";
import { cx, fa, faDateTime } from "../util";
import { Badge, Button, Card, Input, Toggle } from "./ui";

type CalendarData = {
  import_urls: string[];
  last_sync: { at: string; ok: boolean; errors: string[]; events: number; added?: number; updated?: number; removed?: number } | null;
  export_path: string;
  include: string[];
  parts: string[];
};
const PART_FA: Record<string, string> = {
  classes: "کلاس‌ها",
  events: "قرارها",
  blocks: "بلوک‌های کاری",
  deadlines: "ددلاین‌ها",
  trips: "زمان حاضر شدن و مسیر",
};

function Steps({ children }: { children: React.ReactNode }) {
  return <ol className="list-decimal space-y-1 ps-5 text-xs leading-6 text-slate-500 dark:text-slate-400">{children}</ol>;
}

export function CalendarCard() {
  const { toast, bump } = useLive();
  const { data, setData } = useData<CalendarData>("/api/calendar", ["calendar"]);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const exportUrl = `${window.location.origin}${data.export_path}`;
  const sync = data.last_sync;

  const saveUrls = async (urls: string[]) => {
    setBusy(true);
    try {
      const r = await api.put<{ settings: CalendarData; sync: CalendarData["last_sync"] }>("/api/calendar", { import_urls: urls });
      setData(r.settings);
      setUrl("");
      bump("schedule", "events");
      toast(r.sync && !r.sync.ok ? `خواندن تقویم ناموفق بود: ${r.sync.errors[0] ?? ""}` : "تقویم گوگل وصل شد", r.sync && !r.sync.ok ? "error" : "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const syncNow = async () => {
    setBusy(true);
    try {
      await api.post("/api/calendar/sync");
      bump("calendar", "schedule", "events");
      toast("همگام شد", "success");
    } finally {
      setBusy(false);
    }
  };
  const toggle = async (part: string, on: boolean) => {
    const include = on ? [...data.include, part] : data.include.filter((x) => x !== part);
    const r = await api.put<{ settings: CalendarData }>("/api/calendar", { include });
    setData(r.settings);
  };
  const copy = () => navigator.clipboard.writeText(exportUrl).then(() => toast("کپی شد", "success"));
  const newToken = async () => {
    if (!confirm("لینک فعلی از کار می‌افتد و باید لینک جدید را دوباره در گوگل اضافه کنی. ادامه؟")) return;
    setData(await api.post<CalendarData>("/api/calendar/token"));
  };

  return (
    <Card title="تقویم گوگل" icon={<CalendarSync className="size-4 text-sky-500" />}>
      <h3 className="mb-1 text-sm font-semibold">۱) جلسه‌های تقویم گوگل ← پیگیر</h3>
      <p className="mb-2 text-xs leading-6 text-slate-500 dark:text-slate-400">
        جلسه‌های شرکت خودکار در برنامه‌ات می‌آیند (هر ۱۵ دقیقه). برنامه‌ریز دورشان کار می‌چیند، و اگر جایشان مشخص باشد زمان مسیر هم حساب می‌شود.
      </p>
      <Steps>
        <li>در Google Calendar روی کامپیوتر: ⚙️ تنظیمات ← از ستون چپ تقویم خودت را انتخاب کن ← «Integrate calendar».</li>
        <li>«Secret address in iCal format» را کپی کن و اینجا بگذار (این لینک را به کسی نده).</li>
      </Steps>
      {data.import_urls.map((u, i) => (
        <div key={i} className="mt-2 flex items-center gap-2 text-xs">
          <span className="ltr flex-1 truncate rounded-lg bg-slate-50 px-2 py-1.5 text-slate-500 dark:bg-slate-800">{u}</span>
          <Button size="sm" variant="ghost" onClick={() => confirm("اتصال تقویم گوگل قطع شود؟") && saveUrls([])}>
            قطع
          </Button>
        </div>
      ))}
      <div className="mt-2 flex gap-2">
        <Input className="ltr" placeholder="https://calendar.google.com/calendar/ical/…/private-…/basic.ics" value={url} onChange={(e) => setUrl(e.target.value)} />
        <Button variant="soft" loading={busy} disabled={!url.trim()} onClick={() => saveUrls([url.trim()])}>
          وصل کن
        </Button>
      </div>
      {sync && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <Badge className={sync.ok ? "!bg-emerald-50 !text-emerald-700" : "!bg-rose-50 !text-rose-700"}>{sync.ok ? "همگام" : "خطا"}</Badge>
          <span>آخرین همگام‌سازی {faDateTime(sync.at)} · {fa(sync.events)} رویداد</span>
          {!sync.ok && sync.errors[0] && <span className="ltr text-rose-600">{sync.errors[0]}</span>}
          {data.import_urls.length > 0 && (
            <button onClick={syncNow} className="flex items-center gap-1 text-brand-600" disabled={busy}>
              <RefreshCw className={cx("size-3.5", busy && "animate-spin")} /> همین الان
            </button>
          )}
        </div>
      )}

      <h3 className="mb-1 mt-5 text-sm font-semibold">۲) برنامه پیگیر ← تقویم گوگل</h3>
      <p className="mb-2 text-xs leading-6 text-slate-500 dark:text-slate-400">
        کلاس‌ها، بلوک‌های کاری، ددلاین‌ها و زمان راه افتادن در تقویم گوگل (و گوشی) دیده می‌شوند. گوگل هر چند ساعت یک بار به‌روزش می‌کند.
      </p>
      <Steps>
        <li>لینک زیر را کپی کن.</li>
        <li>در Google Calendar: کنار «Other calendars» روی + ← «From URL» ← لینک را بگذار ← Add calendar.</li>
      </Steps>
      <div className="mt-2 flex gap-2">
        <span className="ltr flex-1 truncate rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-800">{exportUrl}</span>
        <Button variant="soft" onClick={copy}><Copy className="size-4" /> کپی</Button>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2">
        {data.parts.map((p) => (
          <label key={p} className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
            <Toggle checked={data.include.includes(p)} onChange={(v) => toggle(p, v)} label={PART_FA[p]} /> {PART_FA[p]}
          </label>
        ))}
      </div>
      <button onClick={newToken} className="mt-3 text-[11px] text-slate-400 underline">ساخت لینک جدید (اگر لینک فعلی لو رفته)</button>
    </Card>
  );
}

type MailData = { connected: boolean; address: string; state: { last_check?: string; error?: string } };

export function GmailCard() {
  const { toast, bump } = useLive();
  const { data, setData } = useData<MailData>("/api/mail", ["mail"]);
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const connect = async () => {
    setBusy(true);
    try {
      const r = await api.put<MailData>("/api/mail", { address: address.trim(), app_password: password });
      setData(r);
      setPassword("");
      bump("contacts");
      toast("Gmail وصل شد", "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const disconnect = async () => {
    if (!confirm("اتصال Gmail قطع شود؟")) return;
    setData(await api.put<MailData>("/api/mail", {}));
  };
  const check = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ ok: boolean; replies?: number; sent?: number; error?: string }>("/api/mail/check");
      bump("contacts", "mail");
      toast(r.ok ? `بررسی شد: ${fa(r.replies ?? 0)} جواب، ${fa(r.sent ?? 0)} ایمیل فرستاده` : r.error ?? "خطا", r.ok ? "success" : "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Gmail (ایمیل استادها)" icon={<Mail className="size-4 text-rose-500" />}>
      <p className="mb-2 text-xs leading-6 text-slate-500 dark:text-slate-400">
        جواب استادهای لیستت را خودم می‌بینم و خبرت می‌کنم، ایمیل‌هایی که برایشان می‌فرستی را ثبت می‌کنم (و پیگیری‌اش را زمان‌بندی می‌کنم)، و متن ایمیل‌ها را
        مستقیم در Drafts جیمیلت می‌گذارم تا خودت بفرستی. فقط ایمیل‌های مربوط به استادهای لیست خوانده می‌شوند.
      </p>
      {data.connected ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge className="!bg-emerald-50 !text-emerald-700">وصل</Badge>
          <span className="ltr">{data.address}</span>
          {data.state.last_check && <span className="text-xs text-slate-500">آخرین بررسی {faDateTime(data.state.last_check)}</span>}
          {data.state.error && <span className="text-xs text-rose-600">{data.state.error}</span>}
          <div className="ms-auto flex gap-2">
            <Button size="sm" variant="soft" loading={busy} onClick={check}><RefreshCw className="size-3.5" /> بررسی الان</Button>
            <Button size="sm" variant="ghost" onClick={disconnect}>قطع</Button>
          </div>
        </div>
      ) : (
        <>
          <Steps>
            <li>تأیید دو مرحله‌ای حساب گوگلت باید روشن باشد.</li>
            <li>
              به <a className="text-brand-600 underline" href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">myaccount.google.com/apppasswords</a> برو، یک App Password بساز (اسمش مثلاً Peygir) و رمز ۱۶ حرفی را اینجا بگذار.
            </li>
          </Steps>
          <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <Input className="ltr" placeholder="you@gmail.com" value={address} onChange={(e) => setAddress(e.target.value)} />
            <Input className="ltr" type="password" placeholder="App Password (16 حرف)" value={password} onChange={(e) => setPassword(e.target.value)} />
            <Button variant="soft" loading={busy} disabled={!address.trim() || !password.trim()} onClick={connect}>وصل کن</Button>
          </div>
        </>
      )}
    </Card>
  );
}
