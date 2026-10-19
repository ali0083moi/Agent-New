import { useMemo, useState } from "react";
import { ListTodo, Plus } from "lucide-react";
import type { Task } from "../api";
import { useData } from "../live";
import { TaskEditor, TaskItem } from "../components/TaskItem";
import { Button, Card, Empty, Segmented, Spinner } from "../components/ui";
import { CATEGORY, cx, fa, parseLocal, serverNow } from "../util";

type Group = { key: string; title: string; tasks: Task[] };

function groupTasks(all: Task[]): Group[] {
  const ids = new Set(all.map((t) => t.id));
  const tasks = all.filter((t) => !t.parent_id || !ids.has(t.parent_id)); // subtasks render under their project
  const now = serverNow();
  const endToday = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const endWeek = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 7);
  const g: Record<string, Group> = {
    late: { key: "late", title: "عقب‌افتاده", tasks: [] },
    today: { key: "today", title: "امروز", tasks: [] },
    week: { key: "week", title: "۷ روز آینده", tasks: [] },
    later: { key: "later", title: "بعداً", tasks: [] },
    none: { key: "none", title: "بدون ددلاین", tasks: [] },
    done: { key: "done", title: "انجام‌شده", tasks: [] },
  };
  for (const t of tasks) {
    if (t.status === "done" || t.status === "cancelled") g.done.tasks.push(t);
    else if (!t.due) g.none.tasks.push(t);
    else {
      const d = parseLocal(t.due)!;
      if (d < now) g.late.tasks.push(t);
      else if (d < endToday) g.today.tasks.push(t);
      else if (d < endWeek) g.week.tasks.push(t);
      else g.later.tasks.push(t);
    }
  }
  return Object.values(g).filter((x) => x.tasks.length);
}

export default function Tasks() {
  const [status, setStatus] = useState<"open" | "done" | "all">("open");
  const [category, setCategory] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const { data, loading } = useData<{ tasks: Task[] }>(`/api/tasks?status=${status}${category ? `&category=${category}` : ""}`, ["tasks", "stats"]);
  const groups = useMemo(() => groupTasks(data?.tasks ?? []), [data]);

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-black">کارها</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">بیشتر وقت‌ها کافیه توی گفتگو بگی؛ این صفحه برای مرور و ویرایش سریعه.</p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="size-4" /> تسک جدید
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Segmented value={status} onChange={setStatus} options={[{ value: "open", label: "باز" }, { value: "done", label: "انجام‌شده" }, { value: "all", label: "همه" }]} />
        <div className="flex flex-wrap gap-1.5">
          <button onClick={() => setCategory("")} className={cx("rounded-full px-3 py-1 text-sm", !category ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300")}>
            همه حوزه‌ها
          </button>
          {Object.entries(CATEGORY).map(([k, c]) => (
            <button key={k} onClick={() => setCategory(k === category ? "" : k)} className={cx("rounded-full px-3 py-1 text-sm transition", category === k ? "ring-2 ring-brand-400 " + c.cls : c.cls)}>
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {loading && !data ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : groups.length === 0 ? (
        <Card>
          <Empty icon={<ListTodo className="size-6" />} title="چیزی اینجا نیست">
            مثلاً بنویس: «تا پنجشنبه باید گزارش پروژه رو تحویل بدم، حدود ۳ ساعت کار داره».
          </Empty>
        </Card>
      ) : (
        groups.map((g) => (
          <Card
            key={g.key}
            title={
              <span className={cx(g.key === "late" && "text-rose-600 dark:text-rose-400")}>
                {g.title} <span className="text-sm font-normal text-slate-400">({fa(g.tasks.length)})</span>
              </span>
            }
          >
            <div className="-mx-2 space-y-0.5">
              {g.tasks.map((t) => {
                const kids = (data?.tasks ?? []).filter((k) => k.parent_id === t.id);
                return (
                  <div key={t.id}>
                    <TaskItem task={t} />
                    {kids.length > 0 && (
                      <div className="ms-6 border-s-2 border-brand-100 ps-2 dark:border-brand-500/20">
                        {kids.map((k) => (
                          <TaskItem key={k.id} task={k} nested />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </Card>
        ))
      )}
      {creating && <TaskEditor onClose={() => setCreating(false)} />}
    </div>
  );
}
