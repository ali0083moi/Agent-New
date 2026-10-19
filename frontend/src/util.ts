const FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";

export function fa(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[0-9]/g, (d) => FA_DIGITS[Number(d)]);
}

/** Parse the backend's local wall-clock "YYYY-MM-DD HH:MM" (or ISO) as a local Date. */
export function parseLocal(s: string | null | undefined): Date | null {
  if (!s) return null;
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] ?? 0), +(m[5] ?? 0), +(m[6] ?? 0));
}

const dateFmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { weekday: "long", day: "numeric", month: "long" });
const shortDateFmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { day: "numeric", month: "long" });
const timeFmt = new Intl.DateTimeFormat("fa-IR", { hour: "2-digit", minute: "2-digit", hour12: false });

export function faDate(s: string | Date | null | undefined, short = false): string {
  const d = s instanceof Date ? s : parseLocal(s ?? null);
  if (!d) return "";
  return (short ? shortDateFmt : dateFmt).format(d);
}

export function faTime(s: string | Date | null | undefined): string {
  const d = s instanceof Date ? s : parseLocal(s ?? null);
  if (!d) return "";
  return timeFmt.format(d);
}

export function faDateTime(s: string | null | undefined): string {
  const d = parseLocal(s);
  if (!d) return "";
  return `${dateFmt.format(d)}، ${timeFmt.format(d)}`;
}

export function duration(min: number | null | undefined): string {
  const m = Math.max(0, Math.round(min ?? 0));
  const h = Math.floor(m / 60);
  const r = m % 60;
  if (h && r) return `${fa(h)} ساعت و ${fa(r)} دقیقه`;
  if (h) return `${fa(h)} ساعت`;
  return `${fa(r)} دقیقه`;
}

export function shortDuration(min: number | null | undefined): string {
  const m = Math.max(0, Math.round(min ?? 0));
  const h = Math.floor(m / 60);
  const r = m % 60;
  if (h) return `${fa(h)}:${fa(String(r).padStart(2, "0"))}`;
  return `${fa(r)}د`;
}

/** Offset between server wall clock and the browser clock (ms). */
let serverOffset = 0;
export function syncServerClock(serverNow: string | null | undefined) {
  const d = parseLocal(serverNow);
  if (d) serverOffset = d.getTime() - Date.now();
}
export function serverNow(): Date {
  return new Date(Date.now() + serverOffset);
}

export function relative(s: string | null | undefined): { text: string; tone: "late" | "soon" | "ok" } {
  const d = parseLocal(s);
  if (!d) return { text: "", tone: "ok" };
  const diff = d.getTime() - serverNow().getTime();
  const abs = Math.abs(diff);
  const min = Math.round(abs / 60000);
  const hours = Math.round(abs / 3600000);
  const days = Math.round(abs / 86400000);
  let t: string;
  if (min < 60) t = `${fa(min)} دقیقه`;
  else if (hours < 36) t = `${fa(hours)} ساعت`;
  else t = `${fa(days)} روز`;
  if (diff < 0) return { text: `${t} گذشته`, tone: "late" };
  return { text: `${t} دیگر`, tone: hours < 24 ? "soon" : "ok" };
}

export function toInputDateTime(s: string | null | undefined): string {
  if (!s) return "";
  return s.replace(" ", "T").slice(0, 16);
}

export function fromInputDateTime(s: string): string | null {
  if (!s) return null;
  return s.replace("T", " ").slice(0, 16);
}

export function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

/** Saturday of the week containing d (Iranian week). */
export function weekStart(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const offset = (x.getDay() + 1) % 7; // Sat=6 -> 0
  return addDays(x, -offset);
}

export const CATEGORY: Record<string, { label: string; cls: string; dot: string }> = {
  work: { label: "کار", cls: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300", dot: "bg-sky-500" },
  university: {
    label: "دانشگاه",
    cls: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
    dot: "bg-violet-500",
  },
  language: {
    label: "زبان",
    cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
    dot: "bg-emerald-500",
  },
  personal: {
    label: "شخصی",
    cls: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
    dot: "bg-amber-500",
  },
  health: { label: "سلامتی", cls: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300", dot: "bg-rose-500" },
  other: { label: "سایر", cls: "bg-slate-100 text-slate-700 dark:bg-slate-500/15 dark:text-slate-300", dot: "bg-slate-400" },
};

export const FOLLOWUP_KIND: Record<string, string> = {
  post_class: "بعد از کلاس",
  deadline: "یادآوری ددلاین",
  task_checkin: "پیگیری پیشرفت",
  overdue: "ددلاین گذشته",
  block_start: "شروع بلوک کاری",
  block_end: "پایان بلوک کاری",
  daily_brief: "برنامه صبح",
  daily_review: "جمع‌بندی شب",
  weekly_report: "گزارش هفتگی",
  idle_checkin: "احوال‌پرسی",
  focus_long: "تایمر طولانی",
  custom: "یادآوری",
  rescue: "حالت نجات",
  weekly_planning: "برنامه‌ریزی هفتگی",
  habit_reminder: "یادآوری عادت",
  vocab_review: "مرور لغت",
};

export const WEEKDAYS: { key: string; label: string; short: string }[] = [
  { key: "sat", label: "شنبه", short: "ش" },
  { key: "sun", label: "یکشنبه", short: "ی" },
  { key: "mon", label: "دوشنبه", short: "د" },
  { key: "tue", label: "سه‌شنبه", short: "س" },
  { key: "wed", label: "چهارشنبه", short: "چ" },
  { key: "thu", label: "پنجشنبه", short: "پ" },
  { key: "fri", label: "جمعه", short: "ج" },
];

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function storageGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
export function storageSet(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}
