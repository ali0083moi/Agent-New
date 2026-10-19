import { useEffect, useRef } from "react";
import { toJalaali } from "jalaali-js";
import type { IeltsPoint } from "../api";
import { cx, fa, parseLocal, shortDuration } from "../util";
import { JALALI_MONTHS } from "./JalaliPicker";

/* ------------------------------------------------------------------ yearly heatmap */
export function Heatmap({ days, goal }: { days: { date: string; minutes: number }[]; goal: number }) {
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // RTL: the newest weeks are at the far (left) end — start scrolled there
    const el = scroller.current;
    if (el) el.scrollLeft = -el.scrollWidth;
  }, [days.length]);
  if (!days.length) return null;
  // columns = weeks (Sat..Fri), first column padded so rows align with weekdays
  const first = parseLocal(days[0].date)!;
  const pad = (first.getDay() + 1) % 7;
  const cells: ({ date: string; minutes: number } | null)[] = [...Array(pad).fill(null), ...days];
  const weeks: (typeof cells)[] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  const level = (m: number) => (m <= 0 ? 0 : m < goal * 0.25 ? 1 : m < goal * 0.5 ? 2 : m < goal ? 3 : 4);
  const colors = [
    "bg-slate-100 dark:bg-slate-800",
    "bg-brand-200 dark:bg-brand-900",
    "bg-brand-300 dark:bg-brand-700",
    "bg-brand-500 dark:bg-brand-500",
    "bg-emerald-500 dark:bg-emerald-400",
  ];
  const monthLabels: { col: number; label: string }[] = [];
  let lastMonth = -1;
  weeks.forEach((w, i) => {
    const d = w.find(Boolean);
    if (!d) return;
    const p = parseLocal(d.date)!;
    const jm = toJalaali(p.getFullYear(), p.getMonth() + 1, p.getDate()).jm;
    if (jm !== lastMonth) {
      monthLabels.push({ col: i, label: JALALI_MONTHS[jm - 1] });
      lastMonth = jm;
    }
  });
  const total = days.reduce((a, b) => a + b.minutes, 0);
  const active = days.filter((d) => d.minutes > 0).length;
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-4 text-sm text-slate-500">
        <span>
          مجموع: <b className="text-slate-800 dark:text-slate-100">{fa(Math.round(total / 60))} ساعت</b>
        </span>
        <span>
          روزهای فعال: <b className="text-slate-800 dark:text-slate-100">{fa(active)}</b>
        </span>
      </div>
      <div ref={scroller} className="scroll-thin overflow-x-auto pb-2">
        <div className="inline-block">
          <div className="relative mb-1 h-4" style={{ width: weeks.length * 14 }}>
            {monthLabels.map((m) => (
              <span key={m.col} className="absolute text-[10px] text-slate-400" style={{ insetInlineStart: m.col * 14 }}>
                {m.label}
              </span>
            ))}
          </div>
          <div className="flex gap-[3px]">
            {weeks.map((w, i) => (
              <div key={i} className="flex flex-col gap-[3px]">
                {Array.from({ length: 7 }, (_, k) => {
                  const c = w[k];
                  if (!c) return <div key={k} className="size-[11px]" />;
                  return (
                    <div
                      key={k}
                      title={`${new Intl.DateTimeFormat("fa-IR-u-ca-persian", { day: "numeric", month: "long", weekday: "short" }).format(parseLocal(c.date)!)}: ${shortDuration(c.minutes)}`}
                      className={cx("size-[11px] rounded-[3px]", colors[level(c.minutes)])}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-1 text-[10px] text-slate-400">
        کم
        {colors.map((c, i) => (
          <span key={i} className={cx("size-[11px] rounded-[3px]", c)} />
        ))}
        زیاد (سبز = رسیدن به هدف روزانه)
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ IELTS line chart */
const SKILL_COLORS: Record<string, string> = {
  listening: "#0ea5e9",
  reading: "#10b981",
  writing: "#8b5cf6",
  speaking: "#f59e0b",
  overall: "#e11d48",
};
export const SKILL_FA: Record<string, string> = {
  listening: "لیسنینگ",
  reading: "ریدینگ",
  writing: "رایتینگ",
  speaking: "اسپیکینگ",
  overall: "کلی",
};

export function IeltsChart({ series, target }: { series: Record<string, IeltsPoint[]>; target: number }) {
  const all = Object.values(series).flat();
  if (!all.length)
    return <p className="py-6 text-center text-sm text-slate-500">هنوز نمره‌ای ثبت نشده. یه رایتینگ بفرست تا تصحیحش کنم، یا نمره ماک‌تستت رو بگو.</p>;
  const W = 640;
  const H = 220;
  const P = { l: 30, r: 12, t: 12, b: 26 };
  const times = all.map((p) => parseLocal(p.date)!.getTime());
  let t0 = Math.min(...times);
  let t1 = Math.max(...times);
  if (t1 - t0 < 86400000 * 7) {
    t0 -= 86400000 * 3;
    t1 += 86400000 * 3;
  }
  const bands = all.map((p) => p.band);
  const lo = Math.max(0, Math.floor(Math.min(...bands, target) - 1));
  const hi = Math.min(9, Math.ceil(Math.max(...bands, target) + 0.5));
  // RTL: newest on the left
  const x = (t: number) => W - P.r - ((t - t0) / (t1 - t0)) * (W - P.l - P.r);
  const y = (b: number) => P.t + (1 - (b - lo) / (hi - lo)) * (H - P.t - P.b);
  const ticks = [];
  for (let b = lo; b <= hi; b += 1) ticks.push(b);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="روند نمره آیلتس">
        {ticks.map((b) => (
          <g key={b}>
            <line x1={P.l} x2={W - P.r} y1={y(b)} y2={y(b)} className="stroke-slate-100 dark:stroke-slate-800" />
            <text x={P.l - 6} y={y(b) + 4} textAnchor="end" className="fill-slate-400 text-[10px]">
              {fa(b)}
            </text>
          </g>
        ))}
        <line x1={P.l} x2={W - P.r} y1={y(target)} y2={y(target)} stroke="#e11d48" strokeDasharray="5 5" strokeWidth={1.5} />
        <text x={P.l + 4} y={y(target) - 5} className="fill-rose-500 text-[10px]">
          هدف {fa(target)}
        </text>
        {Object.entries(series).map(([skill, pts]) => {
          if (!pts.length) return null;
          const path = pts.map((p, i) => `${i ? "L" : "M"}${x(parseLocal(p.date)!.getTime())},${y(p.band)}`).join(" ");
          return (
            <g key={skill}>
              <path d={path} fill="none" stroke={SKILL_COLORS[skill] ?? "#64748b"} strokeWidth={2.2} strokeLinejoin="round" />
              {pts.map((p) => (
                <circle key={p.id} cx={x(parseLocal(p.date)!.getTime())} cy={y(p.band)} r={3.5} fill={SKILL_COLORS[skill] ?? "#64748b"}>
                  <title>{`${SKILL_FA[skill] ?? skill}: ${p.band}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
      <div className="mt-1 flex flex-wrap gap-3 text-xs">
        {Object.entries(series)
          .filter(([, pts]) => pts.length)
          .map(([skill]) => (
            <span key={skill} className="flex items-center gap-1.5">
              <span className="size-2.5 rounded-full" style={{ background: SKILL_COLORS[skill] }} />
              {SKILL_FA[skill] ?? skill}
            </span>
          ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 24h profile */
export function HourBars({ profile, peaks }: { profile: number[]; peaks: number[] }) {
  const max = Math.max(...profile, 1);
  return (
    <div>
      <div className="flex h-24 items-end gap-[3px]" dir="ltr">
        {profile.map((m, h) => (
          <div
            key={h}
            title={`${h}:00 — ${shortDuration(m)}`}
            className={cx("flex-1 rounded-t", peaks.includes(h) ? "bg-gradient-to-t from-amber-500 to-orange-400" : "bg-slate-200 dark:bg-slate-700")}
            style={{ height: `${Math.max(3, (m / max) * 100)}%` }}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-400" dir="ltr">
        <span>{fa(0)}</span>
        <span>{fa(6)}</span>
        <span>{fa(12)}</span>
        <span>{fa(18)}</span>
        <span>{fa(23)}</span>
      </div>
    </div>
  );
}
