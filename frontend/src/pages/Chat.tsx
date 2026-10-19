import { useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import { BellRing, ImagePlus, Loader2, Mic, Monitor, Send, Sparkles, Square, X } from "lucide-react";
import type { ChatMessage } from "../api";
import { useLive } from "../live";
import Markdown from "../components/Markdown";
import { IconButton } from "../components/ui";
import { cx, faDate, faTime, FOLLOWUP_KIND, isoDate, parseLocal, serverNow } from "../util";

const SUGGESTIONS = [
  "سه‌شنبه و پنجشنبه‌ها ساعت ۶ تا ۸ شب کلاس زبان دارم",
  "هدفم اینه که تا ۶ ماه دیگه آیلتس ۷ بگیرم",
  "📷 عکس برنامه کلاسی دانشگاهم رو می‌فرستم",
  "این هفته چند تا کار با ددلاین دارم، برام برنامه بریز",
  "پنجشنبه عصر می‌تونم برم بیرون؟",
  "امروز چقدر کار مفید کردم؟",
];

function TelegramIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
      <path d="M21.94 4.3 18.9 19.05c-.23 1.03-.84 1.29-1.7.8l-4.7-3.46-2.27 2.18c-.25.25-.46.46-.94.46l.34-4.78 8.7-7.86c.38-.34-.08-.53-.59-.19L6.97 13.0l-4.63-1.45c-1-.31-1.02-1 .21-1.48l18.08-6.97c.84-.31 1.57.19 1.3 1.2Z" />
    </svg>
  );
}

function dayLabel(iso: string): string {
  const d = parseLocal(iso);
  if (!d) return "";
  const today = isoDate(serverNow());
  const y = new Date(serverNow());
  y.setDate(y.getDate() - 1);
  const k = isoDate(d);
  if (k === today) return "امروز";
  if (k === isoDate(y)) return "دیروز";
  return faDate(d);
}

function Bubble({ m, showQuick, onQuick }: { m: ChatMessage; showQuick: boolean; onQuick: (t: string) => void }) {
  const mine = m.role === "user";
  const proactive = m.role === "assistant" && m.kind !== "chat";
  return (
    <div className={cx("pop flex w-full", mine ? "justify-end" : "justify-start")}>
      <div className={cx("flex max-w-[88%] flex-col gap-1 sm:max-w-[75%]", mine ? "items-end" : "items-start")}>
        {proactive && (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
            <BellRing className="size-3" /> {FOLLOWUP_KIND[m.kind] ?? "پیگیری"}
          </span>
        )}
        <div
          className={cx(
            "rounded-3xl px-4 py-2.5 text-[15px]",
            mine
              ? "rounded-ee-md bg-gradient-to-l from-brand-600 to-violet-600 text-white shadow-[var(--shadow-lift)]"
              : "rounded-es-md border border-slate-200/70 bg-white shadow-[var(--shadow-soft)] dark:border-slate-800 dark:bg-slate-900",
            proactive && "border-amber-200 dark:border-amber-500/30",
          )}
        >
          {m.meta?.attachments?.map((a) => (
            <a key={a.url} href={a.url} target="_blank" rel="noreferrer">
              <img src={a.url} alt="" className="mb-2 max-h-64 rounded-2xl object-cover" />
            </a>
          ))}
          {m.meta?.voice && <div className={cx("mb-1 flex items-center gap-1 text-xs", mine ? "text-white/80" : "text-slate-500")}><Mic className="size-3" /> ویس</div>}
          {mine ? <div className="whitespace-pre-wrap leading-7" dir="auto">{m.content}</div> : <Markdown>{m.content}</Markdown>}
        </div>
        {m.meta?.actions && m.meta.actions.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {m.meta.actions.map((a, i) => (
              <span
                key={i}
                className={cx(
                  "rounded-full px-2 py-0.5 text-[11px] font-medium",
                  a.ok ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-300",
                )}
              >
                {a.label}
              </span>
            ))}
          </div>
        )}
        {showQuick && m.meta?.quick_replies && m.meta.quick_replies.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {m.meta.quick_replies.map((q) => (
              <button
                key={q}
                onClick={() => onQuick(q)}
                className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-sm text-brand-700 transition hover:bg-brand-100 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-200"
              >
                {q}
              </button>
            ))}
          </div>
        )}
        <div className="flex items-center gap-1 px-1 text-[11px] text-slate-400">
          {mine && m.channel === "telegram" && <TelegramIcon className="size-3 text-sky-500" />}
          {mine && m.channel === "web" && <Monitor className="size-3" />}
          {faTime(m.created_at)}
        </div>
      </div>
    </div>
  );
}

export default function Chat({ assistantName }: { assistantName: string }) {
  const { messages, typing, sending, send, hasMore, loadOlder, connected, markRead, toast } = useLive();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [recording, setRecording] = useState(false);
  const [dragging, setDragging] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const recRef = useRef<MediaRecorder | null>(null);
  const nearBottom = useRef(true);
  const [previews, setPreviews] = useState<string[]>([]);

  useEffect(() => markRead(), [messages.length, markRead]);
  useEffect(() => {
    const urls = files.map((f) => URL.createObjectURL(f));
    setPreviews(urls);
    return () => urls.forEach((u) => URL.revokeObjectURL(u));
  }, [files]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && nearBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages, typing, sending]);

  useEffect(() => {
    const open = () => fileRef.current?.click();
    window.addEventListener("peygir:pick-image", open);
    return () => window.removeEventListener("peygir:pick-image", open);
  }, []);

  const autosize = () => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  };

  const submit = async (override?: string) => {
    const t = (override ?? text).trim();
    if ((!t && !files.length) || sending) return;
    const f = files;
    if (!override) {
      setText("");
      setFiles([]);
      requestAnimationFrame(autosize);
    }
    nearBottom.current = true;
    try {
      await send(t, override ? [] : f);
    } catch {
      if (!override) {
        setText(t);
        setFiles(f);
      }
    }
  };

  const addImages = (list: FileList | File[] | null) => {
    if (!list) return;
    const imgs = [...list].filter((f) => f.type.startsWith("image/"));
    if (imgs.length) setFiles((prev) => [...prev, ...imgs].slice(0, 4));
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const imgs = [...e.clipboardData.files].filter((f) => f.type.startsWith("image/"));
    if (imgs.length) {
      e.preventDefault();
      addImages(imgs);
    }
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    addImages(e.dataTransfer.files);
  };

  const toggleRecord = async () => {
    if (recording) {
      recRef.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        const type = rec.mimeType || "audio/webm";
        const blob = new Blob(chunks, { type });
        if (blob.size < 1200) return;
        const ext = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
        nearBottom.current = true;
        await send(text.trim(), [new File([blob], `voice.${ext}`, { type: type.split(";")[0] })]).catch(() => undefined);
        setText("");
      };
      recRef.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      toast("دسترسی به میکروفون ممکن نشد", "error");
    }
  };

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const lastId = messages[messages.length - 1]?.id;

  return (
    <div className="flex h-full min-h-0 flex-col" onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
      {/* header */}
      <div className="flex items-center gap-3 border-b border-slate-200/70 px-4 py-3 dark:border-slate-800">
        <div className="relative">
          <div className="flex size-10 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-fuchsia-500 text-white shadow-[var(--shadow-lift)]">
            <Sparkles className="size-5" />
          </div>
          <span className={cx("absolute -bottom-0.5 -end-0.5 size-3 rounded-full ring-2 ring-white dark:ring-slate-950", connected ? "bg-emerald-500" : "bg-slate-400")} />
        </div>
        <div className="min-w-0">
          <div className="font-bold">{assistantName}</div>
          <div className="text-xs text-slate-500 dark:text-slate-400">
            {typing || sending ? "در حال نوشتن…" : connected ? "آنلاین · همگام با تلگرام" : "در حال اتصال…"}
          </div>
        </div>
      </div>

      {/* messages */}
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
        }}
        className="scroll-thin relative min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-4 sm:px-6"
      >
        {dragging && (
          <div className="pointer-events-none absolute inset-3 z-10 flex items-center justify-center rounded-3xl border-2 border-dashed border-brand-400 bg-brand-50/80 text-brand-700 dark:bg-brand-500/10">
            عکس را اینجا رها کن
          </div>
        )}
        {hasMore && (
          <div className="flex justify-center">
            <button onClick={() => { nearBottom.current = false; loadOlder(); }} className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-500 dark:bg-slate-800">
              پیام‌های قبلی
            </button>
          </div>
        )}
        {messages.length === 0 && (
          <div className="mx-auto flex max-w-xl flex-col items-center pt-6 text-center sm:pt-12">
            <div className="mb-4 flex size-16 items-center justify-center rounded-3xl bg-gradient-to-br from-brand-500 to-fuchsia-500 text-white shadow-[var(--shadow-lift)]">
              <Sparkles className="size-8" />
            </div>
            <h2 className="text-xl font-extrabold">سلام! من {assistantName}م 👋</h2>
            <p className="mt-2 leading-8 text-slate-500 dark:text-slate-400">
              منشی شخصی و پیگیرت. کلاس‌ها، تکلیف‌ها، ددلاین‌ها و کارهات رو بهم بگو؛ خودم ثبت می‌کنم، برنامه می‌ریزم و تا تموم نشدن پیگیرت می‌شم.
            </p>
            <div className="mt-6 grid w-full gap-2 sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => (s.startsWith("📷") ? fileRef.current?.click() : submit(s))}
                  className="card px-4 py-3 text-start text-sm transition hover:-translate-y-0.5 hover:border-brand-300"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => {
          const prev = messages[i - 1];
          const showDay = !prev || prev.created_at.slice(0, 10) !== m.created_at.slice(0, 10);
          return (
            <div key={m.id}>
              {showDay && (
                <div className="my-3 flex justify-center">
                  <span className="rounded-full bg-slate-200/60 px-3 py-0.5 text-[11px] text-slate-500 dark:bg-slate-800">{dayLabel(m.created_at)}</span>
                </div>
              )}
              <Bubble m={m} showQuick={m.id === lastAssistant?.id && m.id === lastId && !sending} onQuick={(q) => submit(q)} />
            </div>
          );
        })}
        {(typing || sending) && (
          <div className="flex justify-start">
            <div className="flex items-center gap-1.5 rounded-3xl rounded-es-md border border-slate-200/70 bg-white px-4 py-3.5 dark:border-slate-800 dark:bg-slate-900">
              {[0, 1, 2].map((i) => (
                <span key={i} className="typing-dot size-2 rounded-full bg-brand-400" style={{ animationDelay: `${i * 0.15}s` }} />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* composer */}
      <div className="border-t border-slate-200/70 bg-white/70 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur dark:border-slate-800 dark:bg-slate-950/70">
        {previews.length > 0 && (
          <div className="mb-2 flex gap-2">
            {previews.map((u, i) => (
              <div key={u} className="relative">
                <img src={u} alt="" className="size-16 rounded-xl object-cover" />
                <button onClick={() => setFiles((f) => f.filter((_, j) => j !== i))} className="absolute -end-1.5 -top-1.5 rounded-full bg-slate-900 p-0.5 text-white">
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-end gap-2">
          <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={(e) => { addImages(e.target.files); e.target.value = ""; }} />
          <IconButton title="ارسال عکس (مثلاً برنامه کلاسی)" onClick={() => fileRef.current?.click()}>
            <ImagePlus className="size-5" />
          </IconButton>
          <div className="flex min-h-11 flex-1 items-center rounded-2xl border border-slate-200 bg-white px-3 focus-within:border-brand-400 focus-within:ring-4 focus-within:ring-brand-500/10 dark:border-slate-700 dark:bg-slate-900">
            <textarea
              ref={taRef}
              value={text}
              rows={1}
              dir="auto"
              onPaste={onPaste}
              onChange={(e) => {
                setText(e.target.value);
                autosize();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder={recording ? "در حال ضبط… دوباره بزن تا ارسال شود" : "هر چی تو ذهنته بنویس…"}
              className="max-h-44 w-full resize-none bg-transparent py-2.5 text-[15px] leading-6 outline-none placeholder:text-slate-400"
            />
          </div>
          {text.trim() || files.length ? (
            <button
              onClick={() => submit()}
              disabled={sending}
              aria-label="ارسال"
              className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-l from-brand-600 to-violet-600 text-white shadow-[var(--shadow-lift)] transition hover:brightness-110 disabled:opacity-60"
            >
              {sending ? <Loader2 className="size-5 animate-spin" /> : <Send className="size-5 -scale-x-100" />}
            </button>
          ) : (
            <button
              onClick={toggleRecord}
              aria-label={recording ? "توقف و ارسال" : "ضبط ویس"}
              className={cx(
                "flex size-11 shrink-0 items-center justify-center rounded-2xl transition",
                recording ? "recording bg-rose-500 text-white" : "bg-brand-50 text-brand-600 hover:bg-brand-100 dark:bg-brand-500/15 dark:text-brand-200",
              )}
            >
              {recording ? <Square className="size-4 fill-current" /> : <Mic className="size-5" />}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
