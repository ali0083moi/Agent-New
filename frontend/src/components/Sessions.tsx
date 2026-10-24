import { useState } from "react";
import { Check, MessageSquarePlus, MessagesSquare, Pencil, Trash2, X } from "lucide-react";
import { api, type ChatSessionInfo } from "../api";
import { useData, useLive } from "../live";
import { cx, fa, faDate, faTime, isoDate, parseLocal, serverNow } from "../util";
import { IconButton, inputCls } from "./ui";

function when(s: string | null): string {
  const d = parseLocal(s);
  if (!d) return "";
  const today = isoDate(serverNow());
  return isoDate(d) === today ? faTime(d) : faDate(d, true);
}

/** List of chat sessions: switch, rename, delete, start a new one. */
export function SessionList({ onPicked }: { onPicked?: () => void }) {
  const { sessionId, openSession, newSession, toast, bump } = useLive();
  const { data } = useData<{ sessions: ChatSessionInfo[]; current: number }>("/api/sessions", ["sessions"]);
  const [editing, setEditing] = useState<number | null>(null);
  const [title, setTitle] = useState("");

  const pick = async (id: number) => {
    await openSession(id).catch(() => toast("باز نشد", "error"));
    onPicked?.();
  };
  const rename = async (id: number) => {
    await api.patch(`/api/sessions/${id}`, { title });
    setEditing(null);
    bump("sessions");
  };
  const remove = async (s: ChatSessionInfo) => {
    if (!window.confirm(`گفتگوی «${s.title}» و همه پیام‌هایش پاک شود؟ (کارها و حافظه بلندمدت سر جایشان می‌مانند)`)) return;
    const r = await api.del<{ current: number }>(`/api/sessions/${s.id}`);
    if (s.id === sessionId) await openSession(r.current);
    bump("sessions");
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="p-3">
        <button
          onClick={() => newSession().then(() => onPicked?.()).catch(() => toast("ساخته نشد", "error"))}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-l from-brand-600 to-violet-600 py-2.5 text-sm font-medium text-white shadow-[var(--shadow-lift)] hover:brightness-110"
        >
          <MessageSquarePlus className="size-4" /> گفتگوی جدید
        </button>
        <p className="mt-2 text-[11px] leading-5 text-slate-400">
          هر گفتگو تاریخچه خودش را دارد؛ چیزهایی که درباره‌ات می‌داند، کارها و برنامه بین همه مشترک است. بعد از چند ساعت بی‌پیامی، گفتگوی تازه خودکار شروع می‌شود.
        </p>
      </div>
      <div className="scroll-thin min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {data?.sessions.map((s) => (
          <div
            key={s.id}
            className={cx(
              "group flex items-center gap-1 rounded-xl px-2 py-2 transition",
              s.id === sessionId ? "bg-brand-50 dark:bg-brand-500/15" : "hover:bg-slate-100 dark:hover:bg-slate-800",
            )}
          >
            {editing === s.id ? (
              <>
                <input
                  autoFocus
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && rename(s.id)}
                  className={cx(inputCls, "h-8")}
                />
                <IconButton title="ذخیره" onClick={() => rename(s.id)}><Check className="size-4" /></IconButton>
                <IconButton title="انصراف" onClick={() => setEditing(null)}><X className="size-4" /></IconButton>
              </>
            ) : (
              <>
                <button onClick={() => pick(s.id)} className="min-w-0 flex-1 text-start">
                  <div className="flex items-center gap-1.5">
                    <span className={cx("truncate text-sm", s.id === sessionId && "font-semibold text-brand-700 dark:text-brand-200")}>
                      {s.title}
                    </span>
                    {s.current && (
                      <span title="تلگرام و پیام‌های خودکار در این گفتگو هستند" className="size-1.5 shrink-0 rounded-full bg-emerald-500" />
                    )}
                  </div>
                  <div className="text-[11px] text-slate-400">
                    {when(s.last_message_at ?? s.created_at)} · {fa(s.messages)} پیام
                  </div>
                </button>
                <div className="flex opacity-0 transition group-hover:opacity-100 max-sm:opacity-100">
                  <IconButton title="تغییر نام" className="size-7" onClick={() => { setEditing(s.id); setTitle(s.title); }}>
                    <Pencil className="size-3.5" />
                  </IconButton>
                  <IconButton title="حذف" className="size-7 hover:text-rose-600" onClick={() => remove(s)}>
                    <Trash2 className="size-3.5" />
                  </IconButton>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Header controls: open the list (mobile drawer) + new chat. */
export function SessionDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm lg:hidden" onClick={onClose}>
      <div
        className="pop absolute inset-y-0 start-0 flex w-[85%] max-w-xs flex-col bg-white shadow-2xl dark:bg-slate-950"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200/70 px-3 py-2 dark:border-slate-800">
          <span className="flex items-center gap-2 text-sm font-bold"><MessagesSquare className="size-4" /> گفتگوها</span>
          <IconButton title="بستن" onClick={onClose}><X className="size-5" /></IconButton>
        </div>
        <SessionList onPicked={onClose} />
      </div>
    </div>
  );
}
