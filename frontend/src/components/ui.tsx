import { useEffect, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import { X, Loader2, CheckCircle2, AlertTriangle, Info } from "lucide-react";
import { cx, fa, CATEGORY } from "../util";
import { useLive } from "../live";

type Variant = "primary" | "ghost" | "soft" | "danger" | "outline";

export function Button({
  variant = "primary",
  size = "md",
  loading,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean }) {
  const styles: Record<Variant, string> = {
    primary:
      "bg-gradient-to-l from-brand-600 to-violet-600 text-white shadow-[var(--shadow-lift)] hover:brightness-110 active:brightness-95",
    soft: "bg-brand-50 text-brand-700 hover:bg-brand-100 dark:bg-brand-500/15 dark:text-brand-200 dark:hover:bg-brand-500/25",
    ghost: "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
    outline:
      "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800",
    danger: "bg-rose-50 text-rose-700 hover:bg-rose-100 dark:bg-rose-500/15 dark:text-rose-300",
  };
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-xl font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-8 px-3 text-sm" : "h-10 px-4 text-sm",
        styles[variant],
        className,
      )}
      disabled={loading || rest.disabled}
      {...rest}
    >
      {loading && <Loader2 className="size-4 animate-spin" />}
      {children}
    </button>
  );
}

export function IconButton({ className, children, title, ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      title={title}
      aria-label={title}
      className={cx(
        "inline-flex size-9 shrink-0 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 disabled:opacity-40 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Card({ className, children, title, action, icon }: { className?: string; children: ReactNode; title?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <section className={cx("card p-4 sm:p-5", className)}>
      {(title || action) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          <h3 className="flex items-center gap-2 text-[15px] font-bold text-slate-800 dark:text-slate-100">
            {icon}
            {title}
          </h3>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function CategoryBadge({ category }: { category: string }) {
  const c = CATEGORY[category] ?? CATEGORY.other;
  return <span className={cx("inline-flex items-center rounded-lg px-2 py-0.5 text-xs font-medium", c.cls)}>{c.label}</span>;
}

export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-lg bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx("size-5 animate-spin text-brand-500", className)} />;
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center text-slate-500 dark:text-slate-400">
      {icon && <div className="mb-1 rounded-2xl bg-slate-100 p-3 text-slate-400 dark:bg-slate-800">{icon}</div>}
      <p className="font-medium text-slate-600 dark:text-slate-300">{title}</p>
      {children && <div className="max-w-sm text-sm">{children}</div>}
    </div>
  );
}

export function Modal({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 p-0 backdrop-blur-sm sm:items-center sm:p-4" onClick={onClose}>
      <div
        className="pop max-h-[92vh] w-full overflow-y-auto rounded-t-3xl bg-white p-5 shadow-2xl sm:max-w-lg sm:rounded-3xl dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold">{title}</h2>
          <IconButton title="بستن" onClick={onClose}>
            <X className="size-5" />
          </IconButton>
        </div>
        {children}
        {footer && <div className="mt-5 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-slate-600 dark:text-slate-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}

export const inputCls =
  "h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none transition placeholder:text-slate-400 focus:border-brand-400 focus:ring-4 focus:ring-brand-500/10 dark:border-slate-700 dark:bg-slate-900";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputCls, props.className)} />;
}

export function Select({ value, onChange, options, className }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; className?: string }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={cx(inputCls, "pe-8", className)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition",
        checked ? "bg-brand-600" : "bg-slate-300 dark:bg-slate-700",
      )}
    >
      <span className={cx("inline-block size-5 rounded-full bg-white shadow transition", checked ? "-translate-x-5.5" : "-translate-x-0.5")} />
    </button>
  );
}

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] }) {
  return (
    <div className="inline-flex rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cx(
            "rounded-lg px-3 py-1.5 text-sm font-medium transition",
            value === o.value ? "bg-white text-brand-700 shadow-sm dark:bg-slate-950 dark:text-brand-200" : "text-slate-500 hover:text-slate-800 dark:text-slate-400",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function ProgressBar({ value, className, tone = "brand" }: { value: number; className?: string; tone?: "brand" | "emerald" | "amber" | "rose" }) {
  const tones = {
    brand: "from-brand-500 to-violet-500",
    emerald: "from-emerald-500 to-teal-400",
    amber: "from-amber-500 to-orange-400",
    rose: "from-rose-500 to-pink-500",
  };
  return (
    <div className={cx("h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800", className)}>
      <div className={cx("h-full rounded-full bg-gradient-to-l transition-all", tones[tone])} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

export function Ring({ value, size = 84, stroke = 9, children }: { value: number; size?: number; stroke?: number; children?: ReactNode }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id="ring-g" x1="0" x2="1">
            <stop offset="0" stopColor="#6366f1" />
            <stop offset="1" stopColor="#a855f7" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} className="fill-none stroke-slate-100 dark:stroke-slate-800" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
          fill="none"
          stroke="url(#ring-g)"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - v)}
          style={{ transition: "stroke-dashoffset .6s ease" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">{children}</div>
    </div>
  );
}

export function Toasts() {
  const { toasts } = useLive();
  return (
    <div className="pointer-events-none fixed inset-x-0 top-3 z-[60] flex flex-col items-center gap-2 px-4">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cx(
            "pop pointer-events-auto flex max-w-md items-center gap-2 rounded-2xl px-4 py-2.5 text-sm shadow-lg",
            t.tone === "error" && "bg-rose-600 text-white",
            t.tone === "success" && "bg-emerald-600 text-white",
            t.tone === "info" && "bg-slate-900 text-white dark:bg-white dark:text-slate-900",
          )}
        >
          {t.tone === "error" ? <AlertTriangle className="size-4" /> : t.tone === "success" ? <CheckCircle2 className="size-4" /> : <Info className="size-4" />}
          {t.text}
        </div>
      ))}
    </div>
  );
}

export function Stat({ label, value, sub, icon, tone = "brand" }: { label: string; value: ReactNode; sub?: ReactNode; icon: ReactNode; tone?: "brand" | "emerald" | "amber" | "rose" | "sky" }) {
  const tones = {
    brand: "bg-brand-50 text-brand-600 dark:bg-brand-500/15 dark:text-brand-300",
    emerald: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300",
    amber: "bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300",
    rose: "bg-rose-50 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300",
    sky: "bg-sky-50 text-sky-600 dark:bg-sky-500/15 dark:text-sky-300",
  };
  return (
    <div className="card flex items-center gap-3 p-4">
      <div className={cx("rounded-xl p-2.5", tones[tone])}>{icon}</div>
      <div className="min-w-0">
        <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
        <div className="truncate text-lg font-extrabold">{value}</div>
        {sub && <div className="truncate text-xs text-slate-400">{sub}</div>}
      </div>
    </div>
  );
}

export function Num({ children }: { children: number | string }) {
  return <>{fa(children)}</>;
}
