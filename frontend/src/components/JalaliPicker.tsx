import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CalendarDays, ChevronLeft, ChevronRight, X } from "lucide-react";
import { toGregorian, toJalaali, jalaaliMonthLength } from "jalaali-js";
import { cx, fa, parseLocal, serverNow } from "../util";

export const JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"];
const WEEK_HEAD = ["ش", "ی", "د", "س", "چ", "پ", "ج"];

const pad = (n: number) => String(n).padStart(2, "0");

export function gregorianIso(jy: number, jm: number, jd: number): string {
  const g = toGregorian(jy, jm, jd);
  return `${g.gy}-${pad(g.gm)}-${pad(g.gd)}`;
}

/** Saturday-first weekday index (0..6) of a Jalali date. */
function satIndex(jy: number, jm: number, jd: number): number {
  const g = toGregorian(jy, jm, jd);
  return (new Date(g.gy, g.gm - 1, g.gd).getDay() + 1) % 7;
}

export function MonthGrid({
  jy,
  jm,
  selected,
  onPick,
  marks,
}: {
  jy: number;
  jm: number;
  selected?: string | null;
  onPick: (iso: string) => void;
  marks?: Record<string, number>;
}) {
  const first = satIndex(jy, jm, 1);
  const len = jalaaliMonthLength(jy, jm);
  const now = serverNow();
  const todayIso = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const cells: (number | null)[] = [...Array(first).fill(null), ...Array.from({ length: len }, (_, i) => i + 1)];
  while (cells.length % 7) cells.push(null);
  return (
    <div>
      <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-slate-400">
        {WEEK_HEAD.map((w) => (
          <div key={w} className="py-1">
            {w}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((d, i) => {
          if (!d) return <div key={i} />;
          const iso = gregorianIso(jy, jm, d);
          const isSel = selected?.slice(0, 10) === iso;
          const isToday = iso === todayIso;
          const mark = marks?.[iso] ?? 0;
          return (
            <button
              type="button"
              key={i}
              onClick={() => onPick(iso)}
              className={cx(
                "relative flex h-9 items-center justify-center rounded-xl text-sm transition",
                isSel ? "bg-brand-600 font-bold text-white" : "hover:bg-slate-100 dark:hover:bg-slate-800",
                isToday && !isSel && "ring-1 ring-brand-400",
                i % 7 === 6 && !isSel && "text-rose-500",
              )}
            >
              {fa(d)}
              {mark > 0 && <span className={cx("absolute bottom-1 size-1 rounded-full", isSel ? "bg-white" : "bg-brand-500")} />}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Jalali date (+ optional time) picker. Value/onChange use Gregorian "YYYY-MM-DD HH:MM" (or "YYYY-MM-DD"). */
export function JalaliInput({
  value,
  onChange,
  withTime = true,
  placeholder = "انتخاب تاریخ",
}: {
  value: string | null;
  onChange: (v: string | null) => void;
  withTime?: boolean;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const d = parseLocal(value) ?? serverNow();
  const j = toJalaali(d.getFullYear(), d.getMonth() + 1, d.getDate());
  const [view, setView] = useState({ jy: j.jy, jm: j.jm });
  const time = value && value.length > 10 ? value.slice(11, 16) : "23:59";

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current?.contains(t) || pop.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  // the popover is rendered in a portal with fixed positioning so modals/cards can't clip it
  useLayoutEffect(() => {
    if (!open || !ref.current) return;
    const place = () => {
      const r = ref.current!.getBoundingClientRect();
      const w = 288;
      const h = withTime ? 400 : 340;
      const top = r.bottom + h + 8 > window.innerHeight && r.top > h ? r.top - h - 4 : r.bottom + 4;
      const left = Math.min(Math.max(8, r.right - w), window.innerWidth - w - 8);
      setPos({ top, left });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, withTime]);

  const label = useMemo(() => {
    if (!value) return "";
    const p = parseLocal(value)!;
    const jj = toJalaali(p.getFullYear(), p.getMonth() + 1, p.getDate());
    return `${fa(jj.jd)} ${JALALI_MONTHS[jj.jm - 1]} ${fa(jj.jy)}${withTime && value.length > 10 ? ` · ${fa(value.slice(11, 16))}` : ""}`;
  }, [value, withTime]);

  const shift = (delta: number) =>
    setView((v) => {
      let m = v.jm + delta;
      let y = v.jy;
      if (m < 1) {
        m = 12;
        y -= 1;
      } else if (m > 12) {
        m = 1;
        y += 1;
      }
      return { jy: y, jm: m };
    });

  const pick = (iso: string) => {
    onChange(withTime ? `${iso} ${time}` : iso);
    if (!withTime) setOpen(false);
  };

  const quick = (days: number) => {
    const n = serverNow();
    n.setDate(n.getDate() + days);
    const iso = `${n.getFullYear()}-${pad(n.getMonth() + 1)}-${pad(n.getDate())}`;
    onChange(withTime ? `${iso} ${time}` : iso);
    const jj = toJalaali(n.getFullYear(), n.getMonth() + 1, n.getDate());
    setView({ jy: jj.jy, jm: jj.jm });
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex h-10 w-full items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-start text-sm dark:border-slate-700 dark:bg-slate-900"
      >
        <CalendarDays className="size-4 shrink-0 text-slate-400" />
        <span className={cx("flex-1 truncate", !value && "text-slate-400")}>{label || placeholder}</span>
        {value && (
          <span
            role="button"
            aria-label="پاک کردن"
            onClick={(e) => {
              e.stopPropagation();
              onChange(null);
            }}
            className="text-slate-400 hover:text-rose-500"
          >
            <X className="size-4" />
          </span>
        )}
      </button>
      {open && pos && createPortal(
        <div
          ref={pop}
          dir="rtl"
          style={{ position: "fixed", top: pos.top, left: pos.left }}
          className="pop z-[70] w-72 rounded-2xl border border-slate-200 bg-white p-3 font-sans text-slate-800 shadow-xl dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
        >
          <div className="mb-2 flex items-center justify-between">
            <button type="button" onClick={() => shift(-1)} className="rounded-lg p-1 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="ماه قبل">
              <ChevronRight className="size-4" />
            </button>
            <div className="text-sm font-bold">
              {JALALI_MONTHS[view.jm - 1]} {fa(view.jy)}
            </div>
            <button type="button" onClick={() => shift(1)} className="rounded-lg p-1 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="ماه بعد">
              <ChevronLeft className="size-4" />
            </button>
          </div>
          <MonthGrid jy={view.jy} jm={view.jm} selected={value} onPick={pick} />
          <div className="mt-2 flex flex-wrap gap-1">
            {[
              ["امروز", 0],
              ["فردا", 1],
              ["پس‌فردا", 2],
              ["هفته بعد", 7],
            ].map(([l, n]) => (
              <button key={l} type="button" onClick={() => quick(n as number)} className="rounded-lg bg-slate-100 px-2 py-1 text-xs dark:bg-slate-800">
                {l}
              </button>
            ))}
          </div>
          {withTime && (
            <div className="mt-3 flex items-center gap-2">
              <span className="text-xs text-slate-500">ساعت</span>
              <input
                type="time"
                value={time}
                onChange={(e) => value && onChange(`${value.slice(0, 10)} ${e.target.value}`)}
                className="h-9 flex-1 rounded-xl border border-slate-200 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
              />
              <button type="button" onClick={() => setOpen(false)} className="rounded-xl bg-brand-600 px-3 py-1.5 text-sm text-white">
                تأیید
              </button>
            </div>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
