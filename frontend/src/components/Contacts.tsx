import { useState } from "react";
import { GraduationCap, Mail, Plus } from "lucide-react";
import { api, type Contact } from "../api";
import { useData, useLive } from "../live";
import { cx, fa, faDateTime } from "../util";
import { Badge, Button, Card, Field, Input, Modal, Select } from "./ui";

const STATUSES: { value: string; label: string; cls: string }[] = [
  { value: "to_contact", label: "باید ایمیل بزنم", cls: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300" },
  { value: "contacted", label: "ایمیل زده‌ام", cls: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300" },
  { value: "replied", label: "جواب داده", cls: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300" },
  { value: "interview", label: "مصاحبه/جلسه", cls: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300" },
  { value: "positive", label: "مثبت 🎉", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300" },
  { value: "rejected", label: "رد شد", cls: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300" },
  { value: "no_reply", label: "بی‌جواب", cls: "bg-slate-100 text-slate-500 dark:bg-slate-800" },
];
const statusCls = (s: string) => STATUSES.find((x) => x.value === s)?.cls ?? "";

function ContactEditor({ contact, onClose }: { contact?: Contact; onClose: () => void }) {
  const { toast, bump } = useLive();
  const [f, setF] = useState({
    name: contact?.name ?? "",
    organization: contact?.organization ?? "",
    field: contact?.field ?? "",
    email: contact?.email ?? "",
    url: contact?.url ?? "",
    status: contact?.status ?? "to_contact",
    notes: contact?.notes ?? "",
  });
  const set = (k: keyof typeof f) => (v: string) => setF((x) => ({ ...x, [k]: v }));
  const save = async () => {
    try {
      if (contact) await api.patch(`/api/contacts/${contact.id}`, f);
      else await api.post("/api/contacts", f);
      bump("contacts", "followups");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const remove = async () => {
    if (!contact || !confirm("حذف شود؟")) return;
    await api.del(`/api/contacts/${contact.id}`);
    bump("contacts");
    onClose();
  };
  return (
    <Modal
      open
      onClose={onClose}
      title={contact ? contact.name : "استاد جدید"}
      footer={
        <>
          {contact && (
            <Button variant="danger" className="me-auto" onClick={remove}>
              حذف
            </Button>
          )}
          <Button onClick={save} disabled={!f.name.trim()}>
            ذخیره
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <Field label="نام">
            <Input className="ltr" value={f.name} onChange={(e) => set("name")(e.target.value)} placeholder="Prof. Jane Smith" autoFocus />
          </Field>
        </div>
        <Field label="دانشگاه">
          <Input value={f.organization} onChange={(e) => set("organization")(e.target.value)} />
        </Field>
        <Field label="وضعیت">
          <Select value={f.status} onChange={set("status")} options={STATUSES.map(({ value, label }) => ({ value, label }))} />
        </Field>
        <div className="col-span-2">
          <Field label="حوزه تحقیقاتی / چرا مناسب است">
            <Input value={f.field} onChange={(e) => set("field")(e.target.value)} />
          </Field>
        </div>
        <Field label="ایمیل">
          <Input className="ltr" value={f.email} onChange={(e) => set("email")(e.target.value)} />
        </Field>
        <Field label="لینک صفحه">
          <Input className="ltr" value={f.url} onChange={(e) => set("url")(e.target.value)} />
        </Field>
        <div className="col-span-2">
          <Field label="یادداشت">
            <textarea
              value={f.notes}
              onChange={(e) => set("notes")(e.target.value)}
              rows={3}
              className="w-full rounded-xl border border-slate-200 bg-white p-3 text-sm dark:border-slate-700 dark:bg-slate-900"
            />
          </Field>
        </div>
      </div>
    </Modal>
  );
}

export function ContactsCard() {
  const { data } = useData<{ counts: Record<string, number>; contacts: Contact[] }>("/api/contacts", ["contacts", "followups"]);
  const [editing, setEditing] = useState<Contact | null | "new">(null);
  const contacts = data?.contacts ?? [];
  const active = contacts.filter((c) => !["rejected", "no_reply"].includes(c.status));
  return (
    <Card
      title="اپلای — پیدا کردن استاد"
      icon={<GraduationCap className="size-4 text-violet-500" />}
      action={
        <Button size="sm" variant="soft" onClick={() => setEditing("new")}>
          <Plus className="size-4" /> استاد
        </Button>
      }
    >
      {contacts.length ? (
        <>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {STATUSES.filter((s) => data?.counts[s.value]).map((s) => (
              <span key={s.value} className={cx("rounded-full px-2.5 py-0.5 text-xs", s.cls)}>
                {s.label}: {fa(data!.counts[s.value])}
              </span>
            ))}
          </div>
          <div className="space-y-2">
            {[...active, ...contacts.filter((c) => !active.includes(c))].map((c) => (
              <button
                key={c.id}
                onClick={() => setEditing(c)}
                className="flex w-full items-start gap-3 rounded-2xl border border-slate-100 p-3 text-start transition hover:border-slate-300 dark:border-slate-800"
              >
                <div className="rounded-xl bg-violet-50 p-2 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300">
                  <Mail className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="ltr font-semibold">{c.name}</span>
                    <span className={cx("rounded-full px-2 py-0.5 text-[11px]", statusCls(c.status))}>{c.status_fa}</span>
                  </div>
                  <div className="truncate text-xs text-slate-500">
                    {c.organization}
                    {c.field ? ` · ${c.field}` : ""}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate-500">
                    {c.days_since_contact !== null && <Badge>{fa(c.days_since_contact)} روز از آخرین ایمیل</Badge>}
                    {c.next_followup && <Badge className="!bg-amber-50 !text-amber-700">پیگیری: {faDateTime(c.next_followup)}</Badge>}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </>
      ) : (
        <p className="text-sm leading-7 text-slate-500">
          استادهایی که می‌خوای بهشون ایمیل بزنی رو اینجا یا توی گفتگو بگو (مثلاً «به دکتر X از دانشگاه تورنتو ایمیل زدم»). خودم یادم می‌مونه کی پیگیری کنی و ایمیلش رو هم برات می‌نویسم.
        </p>
      )}
      {editing && <ContactEditor contact={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
    </Card>
  );
}
