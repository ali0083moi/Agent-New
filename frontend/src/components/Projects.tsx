import { useState } from "react";
import { FolderKanban, Pencil, Plus, Trash2 } from "lucide-react";
import { api, type Project } from "../api";
import { useData, useLive } from "../live";
import { CATEGORY, cx, fa, faDateTime, shortDuration } from "../util";
import { Badge, Button, Field, Input, Modal, Segmented, Select } from "./ui";

export function useProjects() {
  return useData<{ projects: Project[] }>("/api/projects", ["projects", "tasks", "stats"]);
}

export function ProjectEditor({ project, onClose }: { project?: Project; onClose: () => void }) {
  const { toast, bump } = useLive();
  const [name, setName] = useState(project?.name ?? "");
  const [aliases, setAliases] = useState((project?.aliases ?? []).join("، "));
  const [client, setClient] = useState(project?.client ?? "");
  const [category, setCategory] = useState(project?.category ?? "work");
  const [status, setStatus] = useState<Project["status"]>(project?.status ?? "active");
  const [description, setDescription] = useState(project?.description ?? "");
  const save = async () => {
    const body = { name, aliases: aliases.split(/[,،\n]/).map((a) => a.trim()).filter(Boolean), client, category, status, description };
    try {
      if (project) await api.patch(`/api/projects/${project.id}`, body);
      else await api.post("/api/projects", body);
      bump("projects", "tasks");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const remove = async () => {
    if (!project || !confirm(`پروژه «${project.name}» حذف شود؟ (تسک‌ها می‌مانند)`)) return;
    await api.del(`/api/projects/${project.id}`);
    bump("projects", "tasks");
    onClose();
  };
  return (
    <Modal
      open
      onClose={onClose}
      title={project ? "ویرایش پروژه" : "پروژه جدید"}
      footer={
        <>
          {project && (
            <Button variant="danger" className="me-auto" onClick={remove}>
              <Trash2 className="size-4" /> حذف
            </Button>
          )}
          <Button onClick={save} disabled={!name.trim()}>
            ذخیره
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="نام پروژه">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="مثلاً: tahami supercars" autoFocus />
        </Field>
        <Field label="اسم‌های دیگری که صدایش می‌کنی" hint="با ویرگول جدا کن — مثلاً: پروژه ماشین، ماشین">
          <Input value={aliases} onChange={(e) => setAliases(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="کارفرما / مشتری">
            <Input value={client} onChange={(e) => setClient(e.target.value)} placeholder="اختیاری" />
          </Field>
          <Field label="حوزه">
            <Select value={category} onChange={setCategory} options={Object.entries(CATEGORY).map(([value, c]) => ({ value, label: c.label }))} />
          </Field>
        </div>
        <Field label="وضعیت">
          <Segmented value={status} onChange={setStatus} options={[{ value: "active", label: "فعال" }, { value: "paused", label: "متوقف" }, { value: "done", label: "تمام‌شده" }]} />
        </Field>
        <Field label="توضیحات">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

export function ProjectStrip({ selected, onSelect }: { selected: number | null; onSelect: (id: number | null) => void }) {
  const { data } = useProjects();
  const [editing, setEditing] = useState<Project | null | "new">(null);
  const projects = data?.projects ?? [];
  return (
    <div className="card p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-bold">
          <FolderKanban className="size-4 text-brand-500" /> پروژه‌ها
        </div>
        <Button size="sm" variant="soft" onClick={() => setEditing("new")}>
          <Plus className="size-4" /> پروژه
        </Button>
      </div>
      {projects.length ? (
        <div className="flex gap-2 overflow-x-auto pb-1 scroll-thin">
          <button
            onClick={() => onSelect(null)}
            className={cx("shrink-0 rounded-2xl border px-3 py-2 text-start text-sm transition", selected === null ? "border-brand-400 bg-brand-50 dark:bg-brand-500/10" : "border-slate-200 dark:border-slate-700")}
          >
            همه
          </button>
          {projects.map((p) => (
            <div
              key={p.id}
              onClick={() => onSelect(selected === p.id ? null : p.id)}
              className={cx(
                "group relative min-w-44 shrink-0 cursor-pointer rounded-2xl border px-3 py-2 text-sm transition",
                selected === p.id ? "border-brand-400 bg-brand-50 dark:bg-brand-500/10" : "border-slate-200 hover:border-slate-300 dark:border-slate-700",
                p.status !== "active" && "opacity-60",
              )}
            >
              <div className="flex items-center gap-1.5 pe-6 font-semibold">{p.name}</div>
              {p.aliases.length > 0 && <div className="truncate text-[11px] text-slate-500">{p.aliases.join("، ")}</div>}
              <div className="mt-1 flex flex-wrap gap-1 text-[11px] text-slate-500">
                <Badge>{fa(p.open_tasks)} کار باز</Badge>
                {p.minutes_this_week > 0 && <Badge>این هفته {shortDuration(p.minutes_this_week)}</Badge>}
              </div>
              {p.next_deadline && <div className="mt-1 truncate text-[11px] text-amber-600">⏰ {faDateTime(p.next_deadline.due)}</div>}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setEditing(p);
                }}
                className="absolute end-2 top-2 rounded-lg p-1 text-slate-400 opacity-0 transition hover:bg-slate-100 group-hover:opacity-100 dark:hover:bg-slate-800"
                title="ویرایش"
              >
                <Pencil className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-500">پروژه‌ای ثبت نشده. توی گفتگو بگو «یه پروژه دارم به اسم … که بهش … هم می‌گم».</p>
      )}
      {editing && <ProjectEditor project={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}
