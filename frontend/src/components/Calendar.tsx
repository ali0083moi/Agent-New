import { useEffect, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { jalaaliMonthLength, toJalaali } from "jalaali-js";
import { GripVertical } from "lucide-react";
import { api, type AgendaDay, type AgendaItem, type Task } from "../api";
import { useData, useLive } from "../live";
import { cx, fa, faDate, faTime, isoDate, parseLocal, serverNow } from "../util";
import { JALALI_MONTHS, gregorianIso } from "./JalaliPicker";
import { Button, Field, Input, Modal, Segmented, Select } from "./ui";
import { Timeline } from "./widgets";

const START_H = 7;
const END_H = 24;
const HOUR_PX = 46;
const SNAP = 15;

const ITEM_STYLE: Record<string, string> = {
  class: "bg-violet-100 border-violet-300 text-violet-900 dark:bg-violet-500/20 dark:border-violet-500/40 dark:text-violet-100",
  event: "bg-amber-100 border-amber-300 text-amber-900 dark:bg-amber-500/20 dark:border-amber-500/40 dark:text-amber-100",
  block: "bg-emerald-50 border-emerald-300 border-dashed text-emerald-900 dark:bg-emerald-500/10 dark:border-emerald-500/40 dark:text-emerald-100",
  deadline: "bg-rose-500 border-rose-600 text-white",
};

const pad = (n: number) => String(n).padStart(2, "0");
const minutesOf = (s: string) => {
  const d = parseLocal(s)!;
  return d.getHours() * 60 + d.getMinutes();
};
const toStamp = (date: string, minutes: number) => `${date} ${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
const snap = (m: number) => Math.round(m / SNAP) * SNAP;

type Drag = {
  item: AgendaItem;
  mode: "move" | "resize";
  startY: number;
  origStart: number;
  origEnd: number;
  date: string;
  start: number;
  end: number;
  moved: boolean;
};

export function WeekGrid({ days, onChanged }: { days: AgendaDay[]; onChanged: () => void }) {
  const { toast } = useLive();
  const todayIso = isoDate(serverNow());
  const now = serverNow();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const hours = Array.from({ length: END_H - START_H }, (_, i) => START_H + i);
  const cols = useRef<Record<string, HTMLDivElement | null>>({});
  const [drag, setDragState] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const justDragged = useRef(false);
  const setDrag = (next: Drag | null | ((d: Drag | null) => Drag | null)) => {
    const value = typeof next === "function" ? next(dragRef.current) : next;
    dragRef.current = value;
    setDragState(value);
  };
  const [adding, setAdding] = useState<{ date: string; minutes: number } | null>(null);

  const dayAt = (x: number): string | null => {
    for (const [date, el] of Object.entries(cols.current)) {
      const r = el?.getBoundingClientRect();
      if (r && x >= r.left && x <= r.right) return date;
    }
    return null;
  };

  const onDown = (e: RPointerEvent, it: AgendaItem, date: string, mode: "move" | "resize") => {
    if (it.type !== "block" && it.type !== "event") return;
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    const s = minutesOf(it.start);
    const en = it.end.slice(0, 10) === date ? minutesOf(it.end) : END_H * 60;
    setDrag({ item: it, mode, startY: e.clientY, origStart: s, origEnd: en, date, start: s, end: en, moved: false });
  };

  useEffect(() => {
    if (!drag) return;
    const move = (e: PointerEvent) => {
      const delta = snap(((e.clientY - drag.startY) / HOUR_PX) * 60);
      setDrag((d) => {
        if (!d) return d;
        if (d.mode === "resize") {
          const end = Math.min(Math.max(d.origStart + SNAP, d.origEnd + delta), END_H * 60);
          return { ...d, end, moved: d.moved || end !== d.origEnd };
        }
        const len = d.origEnd - d.origStart;
        let start = Math.max(START_H * 60, Math.min(d.origStart + delta, END_H * 60 - len));
        start = snap(start);
        const date = dayAt(e.clientX) ?? d.date;
        return { ...d, start, end: start + len, date, moved: d.moved || start !== d.origStart || date !== d.date };
      });
    };
    const up = async () => {
      const d = dragRef.current;
      setDrag(null);
      if (!d || !d.moved) return;
      justDragged.current = true;
      setTimeout(() => (justDragged.current = false), 100);
      try {
        if (d.item.type === "block") {
          await api.patch(`/api/blocks/${d.item.ref_id}`, { start: toStamp(d.date, d.start), duration_minutes: d.end - d.start });
        } else {
          await api.patch(`/api/events/${d.item.ref_id}`, { start: toStamp(d.date, d.start), end: toStamp(d.date, d.end) });
        }
        toast("جابه‌جا شد ✅", "success");
        onChanged();
      } catch (err) {
        toast((err as Error).message, "error");
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag?.item.ref_id, drag?.mode]);

  const clickEmpty = (e: React.MouseEvent<HTMLDivElement>, date: string) => {
    if (dragRef.current || justDragged.current) return;
    const r = e.currentTarget.getBoundingClientRect();
    const minutes = Math.floor((START_H * 60 + ((e.clientY - r.top) / HOUR_PX) * 60) / 30) * 30;
    setAdding({ date, minutes });
  };

  return (
    <div className="card overflow-x-auto p-0">
      <div className="min-w-[760px] select-none">
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
                {fa(pad(h))}:۰۰
              </div>
            ))}
          </div>
          {days.map((d) => (
            <div
              key={d.date}
              ref={(el) => {
                cols.current[d.date] = el;
              }}
              onClick={(e) => clickEmpty(e, d.date)}
              className={cx("relative cursor-cell border-s border-slate-100 dark:border-slate-800/80", d.date === todayIso && "bg-brand-50/40 dark:bg-brand-500/5")}
            >
              {hours.map((h) => (
                <div key={h} className="pointer-events-none absolute inset-x-0 border-t border-slate-100 dark:border-slate-800/60" style={{ top: (h - START_H) * HOUR_PX }} />
              ))}
              {d.date === todayIso && nowMin >= START_H * 60 && (
                <div className="pointer-events-none absolute inset-x-0 z-20 h-0.5 bg-rose-500" style={{ top: ((nowMin - START_H * 60) / 60) * HOUR_PX }}>
                  <span className="absolute -start-1 -top-1 size-2.5 rounded-full bg-rose-500" />
                </div>
              )}
              {d.items.map((it, i) => {
                const dragging = drag && drag.item.ref_id === it.ref_id && drag.item.type === it.type;
                if (dragging && drag.date !== d.date) return null;
                const s = dragging ? drag.start : Math.max(minutesOf(it.start), START_H * 60);
                const sameDay = it.end.slice(0, 10) === d.date;
                const e =
                  dragging ? drag.end : it.type === "deadline" ? s + 22 : sameDay ? Math.min(minutesOf(it.end), END_H * 60) : END_H * 60;
                if (e <= START_H * 60) return null;
                const top = ((s - START_H * 60) / 60) * HOUR_PX;
                const height = Math.max(((e - s) / 60) * HOUR_PX - 2, 20);
                const movable = it.type === "block" || it.type === "event";
                return (
                  <div
                    key={`${it.type}-${it.ref_id}-${i}`}
                    title={`${it.title} · ${faTime(it.start)}${it.type !== "deadline" ? " تا " + faTime(it.end) : ""}${movable ? " — بکش تا جابه‌جا شود" : ""}`}
                    onClick={(ev) => ev.stopPropagation()}
                    onPointerDown={(ev) => onDown(ev, it, d.date, "move")}
                    className={cx(
                      "group absolute inset-x-1 z-10 overflow-hidden rounded-lg border px-1.5 py-1 text-[11px] leading-4",
                      ITEM_STYLE[it.type],
                      movable && "cursor-grab touch-none active:cursor-grabbing",
                      dragging && "z-30 shadow-xl ring-2 ring-brand-400",
                    )}
                    style={{ top, height }}
                  >
                    <div className="flex items-center gap-0.5 truncate font-semibold">
                      {movable && <GripVertical className="size-3 shrink-0 opacity-40" />}
                      {it.type === "deadline" ? "⏰ " : ""}
                      {it.title}
                    </div>
                    {height > 32 && (
                      <div className="truncate opacity-75">
                        {dragging ? `${fa(pad(Math.floor(s / 60)))}:${fa(pad(s % 60))} - ${fa(pad(Math.floor(e / 60)))}:${fa(pad(e % 60))}` : `${faTime(it.start)}${it.type !== "deadline" ? ` - ${faTime(it.end)}` : ""}`}
                      </div>
                    )}
                    {movable && (
                      <div
                        onPointerDown={(ev) => onDown(ev, it, d.date, "resize")}
                        className="absolute inset-x-0 bottom-0 h-2 cursor-ns-resize opacity-0 group-hover:opacity-100"
                        style={{ background: "linear-gradient(transparent, rgb(0 0 0 / 0.12))" }}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      {adding && <AddSlot date={adding.date} minutes={adding.minutes} onClose={() => setAdding(null)} onDone={onChanged} />}
    </div>
  );
}

function AddSlot({ date, minutes, onClose, onDone }: { date: string; minutes: number; onClose: () => void; onDone: () => void }) {
  const { toast } = useLive();
  const tasks = useData<{ tasks: Task[] }>("/api/tasks?status=open", ["tasks"]);
  const [mode, setMode] = useState<"event" | "block">("event");
  const [title, setTitle] = useState("");
  const [time, setTime] = useState(`${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`);
  const [duration, setDuration] = useState("60");
  const [taskId, setTaskId] = useState("");
  const [busy, setBusy] = useState(false);
  const leafTasks = (tasks.data?.tasks ?? []).filter((t) => !t.subtasks_total);

  const save = async () => {
    setBusy(true);
    try {
      if (mode === "event") {
        if (!title.trim()) throw new Error("عنوان لازم است");
        await api.post("/api/events", { title, start: `${date} ${time}`, duration_minutes: Number(duration) });
      } else {
        if (!taskId) throw new Error("یک تسک انتخاب کن");
        await api.post("/api/blocks", { task_id: Number(taskId), start: `${date} ${time}`, duration_minutes: Number(duration) });
      }
      toast("اضافه شد ✅", "success");
      onDone();
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open
      onClose={onClose}
      title={`افزودن · ${faDate(date)}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            انصراف
          </Button>
          <Button onClick={save} loading={busy}>
            افزودن
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Segmented value={mode} onChange={setMode} options={[{ value: "event", label: "📌 رویداد / قرار" }, { value: "block", label: "📚 بلوک کاری" }]} />
        {mode === "event" ? (
          <Field label="عنوان">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="مثلاً: بیرون با دوستان" autoFocus />
          </Field>
        ) : (
          <Field label="تسک">
            <Select value={taskId} onChange={setTaskId} options={[{ value: "", label: "انتخاب کن…" }, ...leafTasks.map((t) => ({ value: String(t.id), label: t.title }))]} />
          </Field>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="ساعت شروع">
            <Input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
          </Field>
          <Field label="مدت (دقیقه)">
            <Input type="number" min={15} step={15} value={duration} onChange={(e) => setDuration(e.target.value)} />
          </Field>
        </div>
        {mode === "block" && <p className="text-xs text-slate-500">بلوک‌هایی که خودت می‌گذاری یا جابه‌جا می‌کنی در برنامه‌ریزی دوباره سر جایشان می‌مانند.</p>}
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ month view */
export function MonthView() {
  const now = serverNow();
  const j0 = toJalaali(now.getFullYear(), now.getMonth() + 1, now.getDate());
  const [view, setView] = useState({ jy: j0.jy, jm: j0.jm });
  const [selected, setSelected] = useState<string>(isoDate(now));
  const first = gregorianIso(view.jy, view.jm, 1);
  const len = jalaaliMonthLength(view.jy, view.jm);
  const { data } = useData<{ days: AgendaDay[] }>(`/api/agenda?start=${first}&days=${len}`, ["schedule", "tasks"]);
  const byDate: Record<string, AgendaDay> = {};
  for (const d of data?.days ?? []) byDate[d.date] = d;

  const shift = (delta: number) =>
    setView((v) => {
      let m = v.jm + delta;
      let y = v.jy;
      if (m < 1) [m, y] = [12, y - 1];
      if (m > 12) [m, y] = [1, y + 1];
      return { jy: y, jm: m };
    });

  const firstSat = (() => {
    const g = parseLocal(first)!;
    return (g.getDay() + 1) % 7;
  })();
  const cells: (number | null)[] = [...Array(firstSat).fill(null), ...Array.from({ length: len }, (_, i) => i + 1)];
  while (cells.length % 7) cells.push(null);
  const today = isoDate(now);
  const sel = byDate[selected];

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
      <div className="card p-3 sm:p-4">
        <div className="mb-3 flex items-center justify-between">
          <Button size="sm" variant="ghost" onClick={() => shift(-1)}>
            ماه قبل
          </Button>
          <div className="text-lg font-black">
            {JALALI_MONTHS[view.jm - 1]} {fa(view.jy)}
          </div>
          <Button size="sm" variant="ghost" onClick={() => shift(1)}>
            ماه بعد
          </Button>
        </div>
        <div className="grid grid-cols-7 gap-1 text-center text-xs text-slate-400">
          {["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"].map((w) => (
            <div key={w} className="py-1">
              <span className="hidden sm:inline">{w}</span>
              <span className="sm:hidden">{w[0]}</span>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1">
          {cells.map((d, i) => {
            if (!d) return <div key={i} />;
            const iso = gregorianIso(view.jy, view.jm, d);
            const day = byDate[iso];
            const items = day?.items ?? [];
            const special = items.filter((x) => x.type === "deadline" || x.type === "event" || x.kind === "exam");
            const classes = items.filter((x) => x.type === "class").length;
            const blocks = items.filter((x) => x.type === "block").length;
            return (
              <button
                key={i}
                onClick={() => setSelected(iso)}
                className={cx(
                  "flex min-h-20 flex-col items-stretch gap-0.5 rounded-xl border p-1 text-start text-[10px] transition sm:min-h-24 sm:p-1.5",
                  selected === iso ? "border-brand-400 bg-brand-50 dark:bg-brand-500/10" : "border-slate-100 hover:border-slate-300 dark:border-slate-800",
                  i % 7 === 6 && "bg-slate-50/60 dark:bg-slate-800/20",
                )}
              >
                <span className={cx("mb-0.5 self-start rounded-md px-1 text-xs font-bold", iso === today && "bg-brand-600 text-white")}>{fa(d)}</span>
                {special.slice(0, 2).map((x, k) => (
                  <span key={k} className={cx("truncate rounded px-1", x.type === "deadline" ? "bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-200" : "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-200")}>
                    {x.title}
                  </span>
                ))}
                {special.length > 2 && <span className="text-slate-400">+{fa(special.length - 2)}</span>}
                <span className="mt-auto flex gap-1">
                  {classes > 0 && <span className="rounded bg-violet-100 px-1 text-violet-700 dark:bg-violet-500/20 dark:text-violet-200">{fa(classes)} کلاس</span>}
                  {blocks > 0 && <span className="rounded bg-emerald-100 px-1 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-200">{fa(blocks)}</span>}
                </span>
              </button>
            );
          })}
        </div>
      </div>
      <div className="card h-fit p-4">
        <h3 className="mb-3 font-bold">{faDate(selected)}</h3>
        <Timeline items={sel?.items ?? []} empty="برنامه‌ای ندارد" />
      </div>
    </div>
  );
}
