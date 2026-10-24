import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type ChatMessage } from "./api";

type Toast = { id: number; text: string; tone: "info" | "error" | "success" };

export type Draft = { turn_id: string; text: string; status?: string };

type LiveCtx = {
  draft: Draft | null;
  /** the chat session shown in the chat page (null until the first load) */
  sessionId: number | null;
  /** the session web + Telegram are currently writing into */
  currentSessionId: number | null;
  openSession: (id: number | null) => Promise<void>;
  newSession: () => Promise<void>;
  messages: ChatMessage[];
  hasMore: boolean;
  loadOlder: () => Promise<void>;
  typing: boolean;
  sending: boolean;
  send: (text: string, files?: File[]) => Promise<void>;
  version: Record<string, number>;
  bump: (...domains: string[]) => void;
  connected: boolean;
  toast: (text: string, tone?: Toast["tone"]) => void;
  toasts: Toast[];
  unread: number;
  markRead: () => void;
};

const Ctx = createContext<LiveCtx | null>(null);

function merge(a: ChatMessage[], b: ChatMessage[]): ChatMessage[] {
  const map = new Map<number, ChatMessage>();
  for (const m of a) map.set(m.id, m);
  for (const m of b) map.set(m.id, m);
  return [...map.values()].sort((x, y) => x.id - y.id);
}

export function LiveProvider({ children, activePage }: { children: ReactNode; activePage: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [sending, setSending] = useState(false);
  const [connected, setConnected] = useState(false);
  const [version, setVersion] = useState<Record<string, number>>({});
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [unread, setUnread] = useState(0);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<number | null>(null);
  const pageRef = useRef(activePage);
  pageRef.current = activePage;
  // refs so the long-lived SSE handler sees the latest values
  const viewRef = useRef<number | null>(null);
  const currentRef = useRef<number | null>(null);
  viewRef.current = sessionId;
  currentRef.current = currentSessionId;

  const toast = useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const bump = useCallback((...domains: string[]) => {
    setVersion((v) => {
      const next = { ...v };
      for (const d of [...domains, "any"]) next[d] = (next[d] ?? 0) + 1;
      return next;
    });
  }, []);

  /** Load a session's latest messages (null = the current session). Replaces the list when switching sessions. */
  const load = useCallback(async (id: number | null, replace: boolean) => {
    const data = await api.get<{ session_id: number; messages: ChatMessage[] }>(
      `/api/messages?limit=60${id ? `&session_id=${id}` : ""}`,
    );
    if (id === null) setCurrentSessionId(data.session_id);
    setSessionId(data.session_id);
    setMessages((prev) => (replace ? data.messages : merge(prev, data.messages)));
    setHasMore(data.messages.length >= 60);
  }, []);

  const refresh = useCallback(() => load(viewRef.current, false), [load]);

  const openSession = useCallback(
    async (id: number | null) => {
      setDraft(null);
      await load(id, true);
    },
    [load],
  );

  const newSession = useCallback(async () => {
    const cs = await api.post<{ id: number }>("/api/sessions", {});
    setCurrentSessionId(cs.id);
    await load(cs.id, true);
    bump("sessions");
  }, [load, bump]);

  useEffect(() => {
    refresh().catch(() => undefined);
    let es: EventSource | null = null;
    let first = true;
    const open = () => {
      es = new EventSource("/api/stream");
      es.onopen = () => {
        setConnected(true);
        if (!first) refresh().catch(() => undefined);
        first = false;
      };
      es.onerror = () => setConnected(false);
      es.onmessage = (ev) => {
        try {
          const { event, data } = JSON.parse(ev.data);
          if (event === "message") {
            const m = data as ChatMessage;
            const viewing = viewRef.current;
            if (m.session_id !== viewing) {
              // a new session started (idle rollover / Telegram /new): follow it if we were looking at the current one
              if (viewing !== null && viewing === currentRef.current && m.session_id !== null) {
                setCurrentSessionId(m.session_id);
                load(m.session_id, true).catch(() => undefined);
              }
              bump("sessions");
              if (m.role === "assistant" && pageRef.current !== "chat") setUnread((u) => u + 1);
              return;
            }
            setMessages((prev) => merge(prev, [m]));
            bump("sessions");
            if (data.role === "assistant" && data.meta?.turn_id) setDraft((d) => (d && d.turn_id === data.meta.turn_id ? null : d));
            if (data.role === "assistant" && pageRef.current !== "chat") setUnread((u) => u + 1);
            if (data.role === "assistant") bump("messages");
          } else if (event === "typing") {
            setTyping(Boolean(data));
            if (!data) setDraft(null);
          } else if (event === "stream") {
            setDraft((d) => ({
              turn_id: data.turn_id,
              text: data.text ?? (d && d.turn_id === data.turn_id ? d.text : ""),
              status: data.status ?? (data.text ? undefined : d?.status),
            }));
          } else if (event === "data_changed") {
            bump(...(data as string[]));
          } else if (event === "sessions") {
            const cur = (data as { current?: number }).current;
            if (cur && cur !== currentRef.current) {
              const follow = viewRef.current === null || viewRef.current === currentRef.current;
              setCurrentSessionId(cur);
              if (follow) load(cur, true).catch(() => undefined);
            }
            bump("sessions");
          }
        } catch {
          /* ignore */
        }
      };
    };
    open();
    return () => es?.close();
  }, [refresh, bump, load]);

  const loadOlder = useCallback(async () => {
    const first = messages[0];
    if (!first) return;
    const data = await api.get<{ messages: ChatMessage[] }>(
      `/api/messages?before_id=${first.id}&limit=60${sessionId ? `&session_id=${sessionId}` : ""}`,
    );
    setMessages((prev) => merge(data.messages, prev));
    setHasMore(data.messages.length >= 60);
  }, [messages, sessionId]);

  const send = useCallback(
    async (text: string, files: File[] = []) => {
      const fd = new FormData();
      fd.append("text", text);
      if (viewRef.current) fd.append("session_id", String(viewRef.current));
      for (const f of files) fd.append("files", f, f.name);
      setSending(true);
      try {
        const res = await api.post<{ user: ChatMessage; assistant: ChatMessage }>("/api/chat", fd);
        setMessages((prev) => merge(prev, [res.user, res.assistant]));
        if (res.user.session_id) setCurrentSessionId(res.user.session_id);
        bump("sessions");
      } catch (e) {
        toast((e as Error).message || "ارسال نشد", "error");
        throw e;
      } finally {
        setSending(false);
      }
    },
    [toast, bump],
  );

  const value = useMemo<LiveCtx>(
    () => ({
      draft,
      sessionId,
      currentSessionId,
      openSession,
      newSession,
      messages,
      hasMore,
      loadOlder,
      typing,
      sending,
      send,
      version,
      bump,
      connected,
      toast,
      toasts,
      unread,
      markRead: () => setUnread(0),
    }),
    [draft, sessionId, currentSessionId, openSession, newSession, messages, hasMore, loadOlder, typing, sending, send,
      version, bump, connected, toast, toasts, unread],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLive(): LiveCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("LiveProvider missing");
  return c;
}

/** Fetch JSON and refetch whenever one of the given domains changes. */
export function useData<T>(url: string | null, domains: string[] = ["any"]) {
  const { version } = useLive();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const key = domains.map((d) => version[d] ?? 0).join(".");
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!url) return;
    let alive = true;
    setLoading(true);
    api
      .get<T>(url)
      .then((d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => alive && setError((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [url, key, nonce]);
  return { data, error, loading, reload: () => setNonce((n) => n + 1), setData };
}
