import { useState } from "react";
import { Check, ExternalLink, Plus, School } from "lucide-react";
import { api } from "../api";
import { useData, useLive } from "../live";
import { cx, fa, faDate } from "../util";
import { JalaliInput } from "./JalaliPicker";
import { Badge, Button, Card, Field, Input, Modal, Select } from "./ui";

export type ApplicationItem = { id: number; kind: string; title: string; status: string; person: string; due: string | null };
export type Application = {
  id: number;
  university: string;
  program: string;
  country: string;
  degree: string;
  deadline: string | null;
  days_left: number | null;
  status: string;
  status_fa: string;
  portal_url: string;
  fee: string;
  notes: string;
  contact: { id: number; name: string } | null;
  items: ApplicationItem[];
  done: number;
  total: number;
};

const STATUS_CLS: Record<string, string> = {
  researching: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  preparing: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  submitted: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
  interview: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  admitted: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  rejected: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
  waitlisted: "bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-300",
  withdrawn: "bg-slate-100 text-slate-500 dark:bg-slate-800",
};
const ITEM_NEXT: Record<string, string> = { todo: "in_progress", in_progress: "done", requested: "done", done: "todo" };
const ITEM_FA: Record<string, string> = { todo: "مانده", in_progress: "در حال انجام", requested: "درخواست شد", done: "انجام شد" };

function deadlineTone(days: number | null): string {
  if (days === null) return "";
  if (days < 0) return "!bg-slate-100 !text-slate-500";
  if (days <= 7) return "!bg-rose-50 !text-rose-700";
  if (days <= 21) return "!bg-amber-50 !text-amber-700";
  return "";
}

function ApplicationModal({ app, statuses, onClose }: { app: Application | null; statuses: Record<string, string>; onClose: () => void }) {
  const { toast, bump } = useLive();
  const [f, setF] = useState({
    university: app?.university ?? "",
    program: app?.program ?? "",
    country: app?.country ?? "",
    degree: app?.degree ?? "msc",
    deadline: app?.deadline ?? "",
    status: app?.status ?? "preparing",
    portal_url: app?.portal_url ?? "",
    recommendations: 2,
  });
  const [items, setItems] = useState(app?.items ?? []);
  const set = (k: keyof typeof f) => (v: string | number) => setF((x) => ({ ...x, [k]: v }));
  const save = async () => {
    try {
      if (app) await api.patch(`/api/applications/${app.id}`, { ...f, university: undefined, recommendations: undefined });
      else await api.post("/api/applications", f);
      bump("applications", "followups");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const toggle = async (it: ApplicationItem) => {
    const next = ITEM_NEXT[it.status] ?? "done";
    const r = await api.patch<ApplicationItem>(`/api/applications/${app!.id}/items/${it.id}`, { status: next });
    setItems((xs) => xs.map((x) => (x.id === it.id ? { ...x, status: r.status } : x)));
    bump("applications");
  };
  const remove = async () => {
    if (!app || !confirm(`اپلای «${app.university}» حذف شود؟`)) return;
    await api.del(`/api/applications/${app.id}`);
    bump("applications", "followups");
    onClose();
  };
  return (
    <Modal
      open
      onClose={onClose}
      title={app ? app.university : "اپلای جدید"}
      footer={
        <>
          {app && <Button variant="danger" className="me-auto" onClick={remove}>حذف</Button>}
          <Button onClick={save} disabled={!f.university.trim()}>ذخیره</Button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        {!app && (
          <div className="col-span-2">
            <Field label="دانشگاه">
              <Input className="ltr" value={f.university} onChange={(e) => set("university")(e.target.value)} placeholder="University of Toronto" autoFocus />
            </Field>
          </div>
        )}
        <Field label="رشته / برنامه">
          <Input className="ltr" value={f.program} onChange={(e) => set("program")(e.target.value)} placeholder="MSc Computer Science" />
        </Field>
        <Field label="کشور">
          <Input value={f.country} onChange={(e) => set("country")(e.target.value)} />
        </Field>
        <Field label="ددلاین">
          <JalaliInput withTime={false} value={f.deadline || null} onChange={(v) => set("deadline")(v ?? "")} />
        </Field>
        <Field label="وضعیت">
          <Select value={f.status} onChange={set("status")} options={Object.entries(statuses).map(([value, label]) => ({ value, label }))} />
        </Field>
        <Field label="مقطع">
          <Select value={f.degree} onChange={set("degree")} options={[{ value: "msc", label: "ارشد" }, { value: "phd", label: "دکتری" }, { value: "other", label: "سایر" }]} />
        </Field>
        {!app ? (
          <Field label="تعداد توصیه‌نامه">
            <Input type="number" min={0} max={5} value={f.recommendations} onChange={(e) => set("recommendations")(Number(e.target.value))} />
          </Field>
        ) : (
          <div />
        )}
        <div className="col-span-2">
          <Field label="لینک پورتال اپلای">
            <Input className="ltr" value={f.portal_url} onChange={(e) => set("portal_url")(e.target.value)} />
          </Field>
        </div>
      </div>
      {app && (
        <div className="mt-4">
          <div className="mb-2 text-sm font-semibold">چک‌لیست</div>
          <div className="space-y-1.5">
            {items.map((it) => (
              <button
                key={it.id}
                onClick={() => toggle(it)}
                className="flex w-full items-center gap-2 rounded-xl border border-slate-100 px-3 py-2 text-start text-sm transition hover:border-slate-300 dark:border-slate-800"
              >
                <span
                  className={cx(
                    "flex size-5 shrink-0 items-center justify-center rounded-md border",
                    it.status === "done" ? "border-emerald-500 bg-emerald-500 text-white" : "border-slate-300 dark:border-slate-600",
                  )}
                >
                  {it.status === "done" && <Check className="size-3.5" />}
                </span>
                <span className={cx("flex-1", it.status === "done" && "text-slate-400 line-through")}>
                  {it.title}
                  {it.person && <span className="text-xs text-slate-500"> — {it.person}</span>}
                </span>
                {it.status !== "todo" && it.status !== "done" && <Badge>{ITEM_FA[it.status]}</Badge>}
              </button>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-slate-400">با هر کلیک: مانده ← در حال انجام ← انجام شد. توصیه‌نامه‌ها را در چت بگو («از دکتر کریمی توصیه‌نامه خواستم»).</p>
        </div>
      )}
    </Modal>
  );
}

export function ApplicationsCard() {
  const { data } = useData<{ applications: Application[]; status_fa: Record<string, string> }>("/api/applications", ["applications"]);
  const [editing, setEditing] = useState<Application | null | "new">(null);
  const apps = data?.applications ?? [];
  return (
    <Card
      title="اپلای — دانشگاه‌ها"
      icon={<School className="size-4 text-sky-500" />}
      action={
        <Button size="sm" variant="soft" onClick={() => setEditing("new")}>
          <Plus className="size-4" /> دانشگاه
        </Button>
      }
    >
      {apps.length ? (
        <div className="space-y-2">
          {apps.map((a) => (
            <button
              key={a.id}
              onClick={() => setEditing(a)}
              className="w-full rounded-2xl border border-slate-100 p-3 text-start transition hover:border-slate-300 dark:border-slate-800"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="ltr font-semibold">{a.university}</span>
                <span className={cx("rounded-full px-2 py-0.5 text-[11px]", STATUS_CLS[a.status])}>{a.status_fa}</span>
                {a.portal_url && (
                  <a href={a.portal_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="text-slate-400 hover:text-brand-600">
                    <ExternalLink className="size-3.5" />
                  </a>
                )}
              </div>
              <div className="truncate text-xs text-slate-500">
                <span className="ltr">{a.program}</span>
                {a.country ? ` · ${a.country}` : ""}
                {a.contact ? ` · ${a.contact.name}` : ""}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div className="h-full rounded-full bg-gradient-to-l from-sky-500 to-emerald-500" style={{ width: `${a.total ? (100 * a.done) / a.total : 0}%` }} />
                </div>
                <span className="text-[11px] text-slate-500">{fa(a.done)}/{fa(a.total)}</span>
                {a.deadline && (
                  <Badge className={deadlineTone(a.days_left)}>
                    {a.days_left !== null && a.days_left >= 0 ? `${fa(a.days_left)} روز مانده` : faDate(a.deadline, true)}
                  </Badge>
                )}
              </div>
            </button>
          ))}
        </div>
      ) : (
        <p className="text-sm leading-7 text-slate-500">
          هر دانشگاهی که می‌خواهی اپلای کنی را اینجا یا در گفتگو اضافه کن («برای ارشد کامپیوتر تورنتو اپلای می‌کنم، ددلاین ۱۵ دسامبر»). چک‌لیست مدارک
          ساخته می‌شود و ۳۰، ۱۴، ۷، ۳ و ۱ روز قبل ددلاین یادآوری می‌کنم.
        </p>
      )}
      {editing && (
        <ApplicationModal app={editing === "new" ? null : editing} statuses={data?.status_fa ?? {}} onClose={() => setEditing(null)} />
      )}
    </Card>
  );
}
