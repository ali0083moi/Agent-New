import { useCallback, useEffect, useState } from "react";
import { Send, CalendarDays, CheckSquare, FileText, LayoutDashboard, Lock, MessageCircle, Settings as SettingsIcon, Sparkles } from "lucide-react";
import { api, type Me } from "./api";
import { LiveProvider, useLive } from "./live";
import { Button, Input, Spinner, Toasts } from "./components/ui";
import Chat from "./pages/Chat";
import Dashboard from "./pages/Dashboard";
import Tasks from "./pages/Tasks";
import Schedule from "./pages/Schedule";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import { cx, fa, storageGet, storageSet, syncServerClock } from "./util";

const PAGES = [
  { key: "chat", label: "گفتگو", icon: MessageCircle },
  { key: "today", label: "امروز", icon: LayoutDashboard },
  { key: "tasks", label: "کارها", icon: CheckSquare },
  { key: "schedule", label: "برنامه", icon: CalendarDays },
  { key: "reports", label: "گزارش", icon: FileText },
  { key: "settings", label: "تنظیمات", icon: SettingsIcon },
] as const;

function usePage(): [string, (p: string) => void] {
  const read = () => (window.location.hash.replace(/^#\/?/, "") || "chat").split("?")[0];
  const [page, setPage] = useState(read);
  useEffect(() => {
    const on = () => setPage(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = useCallback((p: string) => {
    window.location.hash = `/${p}`;
  }, []);
  return [PAGES.some((x) => x.key === page) ? page : "chat", go];
}

function useTheme(): [string, (t: string) => void] {
  const [theme, setThemeState] = useState(() => storageGet("peygir-theme") || "system");
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = theme === "dark" || (theme === "system" && mq.matches);
      document.documentElement.classList.toggle("dark", dark);
    };
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme]);
  const setTheme = (t: string) => {
    storageSet("peygir-theme", t);
    setThemeState(t);
  };
  return [theme, setTheme];
}

function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setErr("");
    try {
      await api.post("/api/login", { password: pw });
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="app-bg flex min-h-full items-center justify-center p-4">
      <div className="card w-full max-w-sm p-6 text-center">
        <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-fuchsia-500 text-white">
          <Lock className="size-6" />
        </div>
        <h1 className="text-xl font-black">ورود به پیگیر</h1>
        <p className="mb-4 mt-1 text-sm text-slate-500">رمز عبوری که در APP_PASSWORD گذاشتی را وارد کن.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="space-y-3"
        >
          <Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus placeholder="رمز عبور" />
          {err && <p className="text-sm text-rose-600">{err}</p>}
          <Button className="w-full" loading={busy} type="submit">
            ورود
          </Button>
        </form>
      </div>
    </div>
  );
}

function Shell({ me, page, go }: { me: Me; page: string; go: (p: string) => void }) {
  const { unread } = useLive();
  const [theme, setTheme] = useTheme();
  const name = me.assistant_name || "پیگیر";

  return (
    <div className="app-bg flex h-full">
      {/* sidebar (desktop) */}
      <aside className="hidden w-60 shrink-0 flex-col border-e border-slate-200/70 bg-white/60 p-4 backdrop-blur md:flex dark:border-slate-800 dark:bg-slate-950/50">
        <div className="mb-6 flex items-center gap-3 px-2">
          <div className="flex size-10 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-fuchsia-500 text-white shadow-[var(--shadow-lift)]">
            <Sparkles className="size-5" />
          </div>
          <div>
            <div className="text-lg font-black leading-5">{name}</div>
            <div className="text-xs text-slate-500">منشی شخصی تو</div>
          </div>
        </div>
        <nav className="space-y-1">
          {PAGES.map((p) => {
            const Icon = p.icon;
            const active = page === p.key;
            return (
              <button
                key={p.key}
                onClick={() => go(p.key)}
                className={cx(
                  "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition",
                  active ? "bg-brand-600 text-white shadow-[var(--shadow-lift)]" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
                )}
              >
                <Icon className="size-4.5" />
                {p.label === "برنامه" ? "برنامه هفتگی" : p.label === "گزارش" ? "گزارش‌ها" : p.label}
                {p.key === "chat" && unread > 0 && !active && (
                  <span className="ms-auto rounded-full bg-rose-500 px-1.5 text-[11px] font-bold text-white">{fa(unread)}</span>
                )}
              </button>
            );
          })}
        </nav>
        <div className="mt-auto rounded-2xl bg-gradient-to-br from-brand-50 to-violet-50 p-3 text-xs leading-6 text-slate-600 dark:from-brand-500/10 dark:to-violet-500/10 dark:text-slate-300">
          <div className="mb-1 flex items-center gap-1.5 font-bold text-slate-800 dark:text-slate-100">
            <Send className="size-3.5" /> تلگرام
          </div>
          {me.telegram?.owner_bound ? "✅ متصل؛ پیگیری‌ها در تلگرام هم می‌آیند." : me.telegram?.configured ? "در انتظار اتصال — از تنظیمات کد بگیر." : "تنظیم نشده"}
        </div>
      </aside>

      {/* main */}
      <main className="flex min-w-0 flex-1 flex-col">
        <div className={cx("min-h-0 flex-1", page === "chat" ? "overflow-hidden" : "scroll-thin overflow-y-auto pb-20 md:pb-0")}>
          {page === "chat" && (
            <div className="mx-auto h-full max-w-4xl">
              <Chat assistantName={name} />
            </div>
          )}
          {page === "today" && <Dashboard go={go} />}
          {page === "tasks" && <Tasks />}
          {page === "schedule" && <Schedule go={go} />}
          {page === "reports" && <Reports />}
          {page === "settings" && <Settings theme={theme} setTheme={setTheme} />}
        </div>

        {/* bottom nav (mobile) */}
        <nav
          className={cx(
            "grid grid-cols-6 border-t border-slate-200/70 bg-white/90 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden dark:border-slate-800 dark:bg-slate-950/90",
            page !== "chat" && "fixed inset-x-0 bottom-0 z-40",
          )}
        >
          {PAGES.map((p) => {
            const Icon = p.icon;
            const active = page === p.key;
            return (
              <button key={p.key} onClick={() => go(p.key)} className={cx("relative flex flex-col items-center gap-0.5 py-2 text-[10px]", active ? "text-brand-600 dark:text-brand-300" : "text-slate-500")}>
                <Icon className="size-5" />
                {p.label}
                {p.key === "chat" && unread > 0 && !active && <span className="absolute end-1/4 top-1 size-2 rounded-full bg-rose-500" />}
              </button>
            );
          })}
        </nav>
      </main>
      <Toasts />
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [page, go] = usePage();
  const load = useCallback(() => {
    api
      .get<Me>("/api/me")
      .then((m) => {
        syncServerClock(m.now);
        setMe(m);
      })
      .catch(() => setMe({ auth_required: false, authenticated: true }));
  }, []);
  useEffect(() => {
    load();
    const on = () => setMe((m) => (m ? { ...m, authenticated: false } : m));
    window.addEventListener("peygir:unauthorized", on);
    return () => window.removeEventListener("peygir:unauthorized", on);
  }, [load]);

  if (!me)
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="size-8" />
      </div>
    );
  if (!me.authenticated) return <Login onDone={load} />;
  return (
    <LiveProvider activePage={page}>
      <Shell me={me} page={page} go={go} />
    </LiveProvider>
  );
}
