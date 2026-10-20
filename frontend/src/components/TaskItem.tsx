import { useState } from "react";
import { Check, Clock, Layers, Pencil, Play, Trash2, Zap } from "lucide-react";
import { api, type Task } from "../api";
import { useLive } from "../live";
import { CATEGORY, cx, duration, fa, faDateTime, relative } from "../util";
import { JalaliInput } from "./JalaliPicker";
import { useProjects } from "./Projects";
import { Button, CategoryBadge, Field, IconButton, Input, Modal, ProgressBar, Segmented, Select } from "./ui";

const PRIORITY = [
  { value: "1", label: "🔴 بالا" },
  { value: "2", label: "🟡 معمولی" },
  { value: "3", label: "⚪ پایین" },
];

export function TaskItem({ task, compact, nested }: { task: Task; compact?: boolean; nested?: boolean }) {
  const { toast, bump } = useLive();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const done = task.status === "done";
  const rel = relative(task.due);

  const toggle = async () => {
    setBusy(true);
    try {
      await api.patch(`/api/tasks/${task.id}`, { status: done ? "todo" : "done" });
      bump("tasks", "stats");
      if (!done) toast(`«${task.title}» انجام شد 🎉`, "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const focus = async () => {
    try {
      await api.post("/api/focus/start", { task_id: task.id });
      bump("stats", "tasks");
      toast("تایمر شروع شد ⏱", "success");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div
      className={cx(
        "group flex items-start gap-3 rounded-2xl border border-transparent px-3 py-2.5 transition hover:border-slate-200 hover:bg-white dark:hover:border-slate-800 dark:hover:bg-slate-900",
        done && "opacity-60",
      )}
    >
      <button
        onClick={toggle}
        disabled={busy}
        aria-label={done ? "برگرداندن" : "انجام شد"}
        className={cx(
          "mt-0.5 flex size-5.5 shrink-0 items-center justify-center rounded-lg border-2 transition",
          done ? "border-emerald-500 bg-emerald-500 text-white" : "border-slate-300 hover:border-brand-500 dark:border-slate-600",
        )}
      >
        {done && <Check className="size-3.5" strokeWidth={3} />}
      </button>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <button type="button" onClick={() => setEditing(true)} className={cx("text-start font-semibold hover:text-brand-600 dark:hover:text-brand-300", done && "line-through")}>
            {task.title}
          </button>
          {task.priority === 1 && !done && <span className="text-xs text-rose-500">● مهم</span>}
          {task.energy === "high" && !done && (
            <span className="inline-flex items-center text-xs text-amber-600 dark:text-amber-400" title="کار عمیق">
              <Zap className="size-3" />
            </span>
          )}
          {!compact && !nested && <CategoryBadge category={task.category} />}
          {task.project && !nested && (
            <span className="inline-flex items-center rounded-lg bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">📁 {task.project}</span>
          )}
          {task.subtasks_total ? (
            <span className="inline-flex items-center gap-1 rounded-lg bg-brand-50 px-1.5 py-0.5 text-[11px] text-brand-700 dark:bg-brand-500/15 dark:text-brand-200">
              <Layers className="size-3" /> {fa(task.subtasks_done ?? 0)}/{fa(task.subtasks_total)}
            </span>
          ) : null}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
          {task.due && (
            <span
              className={cx(
                "inline-flex items-center gap-1",
                !done && rel.tone === "late" && "font-semibold text-rose-600 dark:text-rose-400",
                !done && rel.tone === "soon" && "font-semibold text-amber-600 dark:text-amber-400",
              )}
              title={faDateTime(task.due)}
            >
              <Clock className="size-3.5" />
              {compact ? rel.text : `${faDateTime(task.due)} · ${rel.text}`}
            </span>
          )}
          {!done && (
            <span title={task.adjusted_estimate_minutes ? `تخمین تو ${duration(task.estimated_minutes)} بود؛ از روی سابقه‌ات ${duration(task.adjusted_estimate_minutes)} حساب شده` : undefined}>
              باقی‌مانده: {duration(task.remaining_minutes)}
              {task.adjusted_estimate_minutes ? " 📈" : ""}
            </span>
          )}
          {task.spent_minutes > 0 && <span>صرف‌شده: {duration(task.spent_minutes)}</span>}
        </div>
        {task.progress > 0 && !done && (
          <div className="mt-2 flex items-center gap-2">
            <ProgressBar value={task.progress} className="max-w-48" />
            <span className="text-[11px] text-slate-500">{fa(task.progress)}٪</span>
          </div>
        )}
      </div>
      {!done && !compact && (
        <div className="flex shrink-0 items-center opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
          <IconButton title="شروع تمرکز" onClick={focus}>
            <Play className="size-4" />
          </IconButton>
          <IconButton title="ویرایش" onClick={() => setEditing(true)}>
            <Pencil className="size-4" />
          </IconButton>
        </div>
      )}
      {editing && <TaskEditor task={task} onClose={() => setEditing(false)} />}
    </div>
  );
}

export function TaskEditor({ task, onClose }: { task?: Task; onClose: () => void }) {
  const { toast, bump } = useLive();
  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [category, setCategory] = useState(task?.category ?? "personal");
  const [priority, setPriority] = useState(String(task?.priority ?? 2));
  const [due, setDue] = useState<string | null>(task?.due ?? null);
  const [energy, setEnergy] = useState<"high" | "normal" | "low">(task?.energy ?? "normal");
  const [projectId, setProjectId] = useState<string>(task?.project_id ? String(task.project_id) : "");
  const projects = useProjects();
  const [estimate, setEstimate] = useState(task?.estimated_minutes ? String(task.estimated_minutes) : "");
  const [progress, setProgress] = useState(task?.progress ?? 0);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!title.trim()) return;
    setBusy(true);
    const body: Record<string, unknown> = {
      title: title.trim(),
      description,
      category,
      priority: Number(priority),
      energy,
      estimated_minutes: estimate ? Number(estimate) : undefined,
    };
    if (projectId) body.project = projectId;
    else if (task?.project_id) body.project = "none";
    try {
      if (task) {
        body.due = due ?? "none";
        body.progress = progress;
        await api.patch(`/api/tasks/${task.id}`, body);
      } else {
        if (due) body.due = due;
        await api.post("/api/tasks", body);
      }
      bump("tasks", "followups");
      toast("ذخیره شد", "success");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!task || !confirm("این تسک حذف شود؟")) return;
    await api.del(`/api/tasks/${task.id}`);
    bump("tasks");
    onClose();
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={task ? "ویرایش تسک" : "تسک جدید"}
      footer={
        <>
          {task && (
            <Button variant="danger" onClick={remove} className="me-auto">
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
        <Field label="عنوان">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus placeholder="مثلاً: رایتینگ تسک ۲" />
        </Field>
        <Field label="توضیحات">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="w-full rounded-xl border border-slate-200 bg-white p-3 text-sm outline-none focus:border-brand-400 dark:border-slate-700 dark:bg-slate-900"
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="حوزه">
            <Select value={category} onChange={setCategory} options={Object.entries(CATEGORY).map(([value, c]) => ({ value, label: c.label }))} />
          </Field>
          <Field label="اولویت">
            <Select value={priority} onChange={setPriority} options={PRIORITY} />
          </Field>
          <Field label="ددلاین" hint={due ? faDateTime(due) : "اختیاری"}>
            <JalaliInput value={due} onChange={setDue} />
          </Field>
          <Field
            label="زمان تخمینی (دقیقه)"
            hint={task?.adjusted_estimate_minutes ? `از روی سابقه‌ات: ${duration(task.adjusted_estimate_minutes)}` : undefined}
          >
            <Input type="number" min={5} step={5} value={estimate} onChange={(e) => setEstimate(e.target.value)} placeholder="۶۰" />
          </Field>
        </div>
        <Field label="پروژه">
          <Select
            value={projectId}
            onChange={setProjectId}
            options={[{ value: "", label: "بدون پروژه" }, ...(projects.data?.projects ?? []).map((p) => ({ value: String(p.id), label: p.name }))]}
          />
        </Field>
        <Field label="نوع کار" hint="کارهای عمیق در ساعت‌های اوج تمرکزت چیده می‌شوند">
          <Segmented
            value={energy}
            onChange={setEnergy}
            options={[
              { value: "high", label: "⚡ عمیق" },
              { value: "normal", label: "معمولی" },
              { value: "low", label: "سبک" },
            ]}
          />
        </Field>
        {task && (
          <Field label={`پیشرفت: ${fa(progress)}٪`}>
            <input type="range" min={0} max={100} step={5} value={progress} onChange={(e) => setProgress(Number(e.target.value))} className="w-full accent-brand-600" />
          </Field>
        )}
        {task?.notes && (
          <div className="rounded-xl bg-slate-50 p-3 text-xs leading-6 whitespace-pre-line text-slate-500 dark:bg-slate-800/50">{task.notes}</div>
        )}
      </div>
    </Modal>
  );
}
