"""Telegram bot (long polling) — chat with the assistant + receive notifications."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets
from typing import Any

import httpx

from . import prefs
from .config import get_settings
from .db import Message, session

log = logging.getLogger(__name__)

MAX_LEN = 4000

COMMANDS = {
    "/today": "برنامه امروزم چیه؟",
    "/tasks": "کارهای باز من رو با ددلاین‌هاشون لیست کن",
    "/plan": "برای کارهام برنامه‌ریزی کن",
    "/stats": "امروز و این هفته چقدر کار مفید کردم؟",
    "/report": "گزارش این هفته رو بنویس",
    "/stop": "تایمر رو متوقف کن",
    "/habits": "عادت‌هام چطور پیش می‌رن؟",
    "/ielts": "وضعیت آیلتسم چطوره؟",
    "/vocab": "لغت‌های امروزم رو ازم بپرس",
    "/undo": "کار آخری که کردی رو برگردون",
}

HELP = (
    "سلام! من منشی شخصی‌ات هستم 🤝\n"
    "هر چی داری بهم بگو: کلاس‌ها، تکلیف‌ها، ددلاین‌ها، قرارها… خودم ثبت می‌کنم و پیگیرت می‌شم.\n"
    "می‌تونی عکس برنامه کلاسی یا ویس هم بفرستی.\n\n"
    "/today برنامه امروز\n/tasks کارهای باز\n/plan برنامه‌ریزی\n/stats آمار کار مفید\n/report گزارش هفتگی\n"
    "/habits عادت‌ها\n/ielts وضعیت آیلتس\n/vocab مرور لغت\n/undo برگرداندن آخرین تغییر\n/stop توقف تایمر\n"
    "/new شروع گفتگوی جدید (حافظه بلندمدت سر جاشه)"
)
NEW_SESSION_TEXT = ("🆕 گفتگوی جدید شروع شد. چیزهایی که درباره‌ات می‌دونم و کارها و برنامه‌ات سر جاشونه؛ "
                    "فقط تاریخچه این گفتگو از صفر شروع می‌شه.")


# ---------------------------------------------------------------- markdown -> telegram html
def md_to_html(text: str) -> str:
    """Convert the light Markdown the LLM writes into Telegram-safe HTML."""
    code_blocks: list[str] = []

    def _stash_block(m: re.Match) -> str:
        code_blocks.append(f"<pre>{html.escape(m.group(2).strip())}</pre>")
        return f"\x00B{len(code_blocks) - 1}\x00"

    text = re.sub(r"```(\w*)\n?(.*?)```", _stash_block, text, flags=re.S)
    inline: list[str] = []

    def _stash_inline(m: re.Match) -> str:
        inline.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00I{len(inline) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", _stash_inline, text)
    text = html.escape(text, quote=False)

    out_lines: list[str] = []
    quote: list[str] = []

    def flush_quote() -> None:
        if quote:
            out_lines.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("&gt;"):
            quote.append(stripped[4:].lstrip())
            continue
        flush_quote()
        m = re.match(r"^\s*#{1,6}\s+(.*)$", line)
        if m:
            line = f"<b>{m.group(1).strip()}</b>"
        elif re.match(r"^\s*[-*]\s+", line):
            line = re.sub(r"^(\s*)[-*]\s+", lambda mm: mm.group(1) + "• ", line)
        elif re.match(r"^\s*\|?[\s:-]+\|[\s|:-]*$", line):
            continue  # markdown table separator row
        elif line.count("|") >= 2:
            line = " | ".join(c.strip() for c in line.strip().strip("|").split("|"))
        out_lines.append(line)
    flush_quote()
    text = "\n".join(out_lines)

    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w])_(?!\s)([^_\n]+?)_(?![\w])", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\x00I(\d+)\x00", lambda m: inline[int(m.group(1))], text)
    text = re.sub(r"\x00B(\d+)\x00", lambda m: code_blocks[int(m.group(1))], text)
    return text.strip()


def split_text(text: str, limit: int = MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n"):
        while len(para) > limit:
            parts.append(para[:limit])
            para = para[limit:]
        if len(cur) + len(para) + 1 > limit:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return parts


def strip_tags(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


# ---------------------------------------------------------------- bot
def _markup(buttons: list[list[dict[str, str]]] | None) -> dict[str, Any] | None:
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": b["text"], "callback_data": b["data"]} for b in row] for row in buttons]}


def _forward_label(msg: dict[str, Any]) -> str | None:
    origin = msg.get("forward_origin")
    if origin:
        t = origin.get("type")
        if t == "user":
            u = origin.get("sender_user", {})
            return " ".join(x for x in [u.get("first_name"), u.get("last_name")] if x) or u.get("username")
        if t == "hidden_user":
            return origin.get("sender_user_name")
        if t == "chat":
            return (origin.get("sender_chat") or {}).get("title")
        if t == "channel":
            return (origin.get("chat") or {}).get("title")
    if msg.get("forward_from_chat"):
        return msg["forward_from_chat"].get("title")
    if msg.get("forward_from"):
        return msg["forward_from"].get("first_name")
    if msg.get("forward_sender_name"):
        return msg["forward_sender_name"]
    return None


class TelegramStreamer:
    """Shows the reply growing inside one Telegram message (edits at most ~once a second)."""

    def __init__(self, bot: "TelegramBot", chat_id: int | str) -> None:
        self.bot, self.chat_id = bot, chat_id
        self.message_id: int | None = None
        self.last_edit = 0.0
        self.last_text = ""

    async def start(self) -> None:
        try:
            res = await self.bot.call("sendMessage", chat_id=self.chat_id, text="✍️ …")
            self.message_id = res.get("message_id")
        except Exception:  # noqa: BLE001
            self.message_id = None

    async def update(self, text: str) -> None:
        loop = asyncio.get_running_loop()
        if self.message_id is None or loop.time() - self.last_edit < 1.1:
            return
        shown = text[:MAX_LEN] + (" …" if len(text) < MAX_LEN else "")
        if shown == self.last_text:
            return
        self.last_edit, self.last_text = loop.time(), shown
        try:
            await self.bot.call("editMessageText", chat_id=self.chat_id, message_id=self.message_id, text=shown)
        except Exception:  # noqa: BLE001
            pass

    async def status(self, label: str) -> None:
        await self.update((self.last_text.rstrip(" …") + "\n" if self.last_text else "") + f"⏳ {label}")

    async def finish(self, text_md: str, buttons: list[list[dict[str, str]]] | None) -> int | None:
        """Final formatted text. Returns the id of the last message of the reply."""
        chunks = split_text(md_to_html(text_md))
        if self.message_id is None:
            return None
        try:
            await self.bot.edit(self.chat_id, self.message_id, chunks[0], buttons if len(chunks) == 1 else None)
        except Exception:  # noqa: BLE001
            try:
                await self.bot.call("deleteMessage", chat_id=self.chat_id, message_id=self.message_id)
            except Exception:  # noqa: BLE001
                pass
            return None
        last = self.message_id
        for i, chunk in enumerate(chunks[1:], start=1):
            last = await self.bot.send_html(self.chat_id, chunk, buttons if i == len(chunks) - 1 else None)
        return last


class TelegramBot:
    def __init__(self, token: str, api_base: str, proxy: str = "") -> None:
        self.token = token
        self.base = f"{api_base.rstrip('/')}/bot{token}"
        self.file_base = f"{api_base.rstrip('/')}/file/bot{token}"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=20.0), proxy=proxy or None)
        self.me: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.running = False

    async def call(self, method: str, **params: Any) -> Any:
        r = await self.client.post(f"{self.base}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"{method}: {data.get('description')}")
        return data["result"]

    async def send_html(self, chat_id: int | str, html_chunk: str, buttons: list[list[dict[str, str]]] | None = None) -> int | None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": html_chunk, "parse_mode": "HTML",
                                  "link_preview_options": {"is_disabled": True}}
        markup = _markup(buttons)
        if markup:
            params["reply_markup"] = markup
        try:
            res = await self.call("sendMessage", **params)
        except RuntimeError as e:
            if "parse" not in str(e).lower() and "can't" not in str(e).lower():
                raise
            params["text"] = strip_tags(html_chunk)
            params.pop("parse_mode", None)
            res = await self.call("sendMessage", **params)
        return res.get("message_id")

    async def send(self, chat_id: int | str, text_md: str, buttons: list[list[dict[str, str]]] | None = None) -> int | None:
        chunks = split_text(md_to_html(text_md))
        sent_id = None
        for i, chunk in enumerate(chunks):
            sent_id = await self.send_html(chat_id, chunk, buttons if i == len(chunks) - 1 else None)
        return sent_id

    async def edit(self, chat_id: int | str, message_id: int, html_chunk: str,
                   buttons: list[list[dict[str, str]]] | None = None) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": html_chunk,
                                  "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
        markup = _markup(buttons)
        if markup:
            params["reply_markup"] = markup
        try:
            await self.call("editMessageText", **params)
        except RuntimeError as e:
            if "not modified" in str(e).lower():
                return
            if "parse" not in str(e).lower() and "can't" not in str(e).lower():
                raise
            params["text"] = strip_tags(html_chunk)
            params.pop("parse_mode", None)
            await self.call("editMessageText", **params)

    async def send_voice(self, chat_id: int | str, audio: bytes, caption: str | None = None) -> int | None:
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:1000]
        r = await self.client.post(f"{self.base}/sendVoice", data=data,
                                   files={"voice": ("reply.ogg", audio, "audio/ogg")})
        res = r.json()
        if not res.get("ok"):
            raise RuntimeError(f"sendVoice: {res.get('description')}")
        return res["result"].get("message_id")

    async def typing(self, chat_id: int | str, action: str = "typing") -> None:
        try:
            await self.call("sendChatAction", chat_id=chat_id, action=action)
        except Exception:  # noqa: BLE001
            pass

    async def download(self, file_id: str) -> bytes:
        info = await self.call("getFile", file_id=file_id)
        r = await self.client.get(f"{self.file_base}/{info['file_path']}")
        r.raise_for_status()
        return r.content

    # ------------------------------------------------------------ polling
    async def run(self) -> None:
        self.running = True
        backoff = 2
        while self.running:
            try:
                if self.me is None:
                    self.me = await self.call("getMe")
                    await self._set_commands()
                    log.info("Telegram bot @%s connected", self.me.get("username"))
                offset = prefs.get("telegram_offset", 0) or 0
                updates = await self.call("getUpdates", offset=offset, timeout=50,
                                          allowed_updates=["message", "callback_query"])
                self.last_error = None
                backoff = 2
                for u in updates:
                    prefs.set("telegram_offset", u["update_id"] + 1)
                    asyncio.create_task(self._safe_handle(u))
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                log.warning("telegram polling error: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _set_commands(self) -> None:
        cmds = [
            {"command": "new", "description": "گفتگوی جدید"},
            {"command": "today", "description": "برنامه امروز"},
            {"command": "tasks", "description": "کارهای باز"},
            {"command": "plan", "description": "برنامه‌ریزی کارها"},
            {"command": "stats", "description": "آمار کار مفید"},
            {"command": "habits", "description": "عادت‌ها"},
            {"command": "ielts", "description": "وضعیت آیلتس"},
            {"command": "vocab", "description": "مرور لغت"},
            {"command": "report", "description": "گزارش هفتگی"},
            {"command": "undo", "description": "برگرداندن آخرین تغییر"},
            {"command": "stop", "description": "توقف تایمر تمرکز"},
            {"command": "help", "description": "راهنما"},
        ]
        try:
            await self.call("setMyCommands", commands=cmds)
        except Exception:  # noqa: BLE001
            pass

    async def _safe_handle(self, u: dict[str, Any]) -> None:
        try:
            await self.handle(u)
        except Exception:  # noqa: BLE001
            log.exception("failed to handle telegram update")

    async def _attachments(self, msg: dict[str, Any]) -> list[Any]:
        from .agent.core import Attachment

        out: list[Attachment] = []
        if msg.get("photo"):
            biggest = max(msg["photo"], key=lambda p: p.get("file_size", 0) or p.get("width", 0))
            out.append(Attachment("image", await self.download(biggest["file_id"]), "image/jpeg", "photo.jpg"))
        doc = msg.get("document")
        if doc:
            mime = str(doc.get("mime_type", ""))
            name = doc.get("file_name", "file")
            if mime.startswith("image/"):
                out.append(Attachment("image", await self.download(doc["file_id"]), mime, name))
            elif mime == "application/pdf" or name.lower().endswith(".pdf") or mime.startswith("text/") or \
                    name.lower().endswith((".txt", ".md", ".csv")):
                if (doc.get("file_size") or 0) > 20 * 1024 * 1024:
                    raise ValueError("فایل خیلی بزرگ است (حداکثر ۲۰ مگابایت)")
                out.append(Attachment("document", await self.download(doc["file_id"]), mime or "application/pdf", name))
        voice = msg.get("voice") or msg.get("audio") or msg.get("video_note")
        if voice:
            out.append(Attachment("audio", await self.download(voice["file_id"]),
                                  voice.get("mime_type", "audio/ogg"), "voice.ogg"))
        return out

    async def handle(self, u: dict[str, Any]) -> None:
        if "callback_query" in u:
            await self._handle_callback(u["callback_query"])
            return
        msg = u.get("message")
        if not msg:
            return
        chat = msg["chat"]
        chat_id = chat["id"]
        text = msg.get("text") or msg.get("caption") or ""
        owner = owner_chat_id()

        if owner is None or str(owner) != str(chat_id):
            await self._handle_pairing(chat, msg.get("from", {}), text)
            return

        if text.startswith("/"):
            cmd = text.split()[0].split("@")[0].lower()
            if cmd in ("/start", "/help"):
                await self.send(chat_id, HELP)
                return
            if cmd == "/new":
                from . import jobs, sessions
                from .db import session

                with session() as s:
                    _, ended = sessions.new_session(s)
                if ended is not None:
                    jobs.session_ended(ended)
                await self.send(chat_id, NEW_SESSION_TEXT)
                return
            if cmd in COMMANDS:
                text = COMMANDS[cmd]

        meta: dict[str, Any] = {}
        fwd = _forward_label(msg)
        if fwd:
            meta["forwarded_from"] = fwd
        try:
            attachments = await self._attachments(msg)
        except ValueError as e:
            await self.send(chat_id, f"⚠️ {e}")
            return
        if not text and not attachments:
            return
        await self.reply(chat_id, text, attachments, meta)

    async def reply(self, chat_id: int | str, text: str, attachments: list[Any], meta: dict[str, Any]) -> None:
        from . import outbox
        from .agent.core import handle_user_message

        await self.typing(chat_id)
        streamer = TelegramStreamer(self, chat_id)
        await streamer.start()
        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            _, reply = await handle_user_message(text, "telegram", attachments, meta=meta,
                                                 on_delta=streamer.update, on_status=streamer.status)
        finally:
            typing_task.cancel()
        buttons = outbox.buttons_for(reply["id"], reply.get("meta"))
        tg_id = await streamer.finish(reply["content"], buttons)
        from .notify import send_voice_reply, update_meta

        if tg_id:
            update_meta(reply["id"], telegram_message_id=tg_id)
        else:
            await outbox.send("text", {"text": reply["content"], "buttons": buttons}, reply["id"])
        await send_voice_reply(reply, user_sent_voice=any(a.kind == "audio" for a in attachments))

    async def _keep_typing(self, chat_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(4.5)
                await self.typing(chat_id)
        except asyncio.CancelledError:
            pass

    async def _handle_callback(self, cq: dict[str, Any]) -> None:
        data = cq.get("data") or ""
        chat_id = cq.get("message", {}).get("chat", {}).get("id")
        if str(chat_id) != str(owner_chat_id()):
            return
        m = re.fullmatch(r"(qr|sz|ud):(\d+)(?::(\d+))?", data)
        if not m:
            return
        kind, mid, arg = m.group(1), int(m.group(2)), m.group(3)
        with session() as s:
            src = s.get(Message, mid)
            meta = dict(src.meta or {}) if src else {}

        async def ack(text: str | None = None) -> None:
            try:
                await self.call("answerCallbackQuery", callback_query_id=cq["id"], **({"text": text} if text else {}))
            except Exception:  # noqa: BLE001
                pass

        async def drop_keyboard() -> None:
            try:
                await self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=cq["message"]["message_id"],
                                reply_markup={"inline_keyboard": []})
            except Exception:  # noqa: BLE001
                pass

        if kind == "qr":
            options = meta.get("quick_replies") or []
            idx = int(arg or 0)
            await ack()
            if idx >= len(options):
                return
            await drop_keyboard()
            await self.reply(chat_id, options[idx], [], {"quick_reply_to": mid})
        elif kind == "sz":
            from .actions import snooze_message

            text = snooze_message(mid, int(arg or 60))
            await ack("⏰ " + text)
            await drop_keyboard()
        elif kind == "ud":
            from .actions import undo_message

            text = undo_message(mid)
            await ack("↩️")
            await drop_keyboard()
            from . import outbox

            await outbox.send("text", {"text": text})

    async def _handle_pairing(self, chat: dict[str, Any], user: dict[str, Any], text: str) -> None:
        if chat.get("type") != "private":
            return
        code = pairing_code()
        parts = text.strip().split()
        if owner_chat_id() is None and len(parts) == 2 and parts[0].lower().startswith("/start") and parts[1] == code:
            prefs.set("telegram_chat_id", chat["id"])
            prefs.set("telegram_user", {"id": user.get("id"), "username": user.get("username"),
                                        "name": " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x)})
            if not prefs.get("user_name") and user.get("first_name"):
                prefs.set("user_name", user.get("first_name"))
            await self.send(chat["id"], "✅ وصل شدیم! از این به بعد پیگیری‌ها و یادآوری‌ها رو اینجا می‌فرستم.\n\n" + HELP)
            from .bus import bus

            bus.publish("data_changed", ["settings"])
            return
        if owner_chat_id() is None:
            await self.send(chat["id"], "🔐 برای اتصال، کد اتصال رو از صفحه تنظیمات وب بردار و این‌طوری بفرست:\n`/start 123456`")
        else:
            await self.send(chat["id"], "این ربات شخصیه 🙂")

    async def close(self) -> None:
        self.running = False
        await self.client.aclose()


# ---------------------------------------------------------------- module api
bot: TelegramBot | None = None


def configured() -> bool:
    return bool(get_settings().telegram_bot_token) or bot is not None


def owner_chat_id() -> int | str | None:
    env = get_settings().telegram_chat_id
    if env:
        return env
    return prefs.get("telegram_chat_id")


def pairing_code() -> str:
    code = prefs.get("pairing_code")
    if not code:
        code = f"{secrets.randbelow(900000) + 100000}"
        prefs.set("pairing_code", code)
    return str(code)


def status() -> dict[str, Any]:
    from . import outbox

    st = get_settings()
    return {
        "configured": bool(st.telegram_bot_token),
        "connected": bool(bot and bot.me),
        "bot_username": (bot.me or {}).get("username") if bot else None,
        "owner_bound": owner_chat_id() is not None,
        "owner": prefs.get("telegram_user"),
        "pairing_code": pairing_code() if owner_chat_id() is None else None,
        "last_error": bot.last_error if bot else None,
        "outbox": outbox.status(),
    }


def start() -> asyncio.Task | None:
    global bot
    st = get_settings()
    if not st.telegram_bot_token:
        log.info("TELEGRAM_BOT_TOKEN not set — Telegram disabled")
        return None
    bot = TelegramBot(st.telegram_bot_token, st.telegram_api_base, st.telegram_proxy)
    if owner_chat_id() is None:
        log.warning("Telegram pairing code: /start %s  (send this to your bot)", pairing_code())
    return asyncio.create_task(bot.run())


async def stop() -> None:
    if bot is not None:
        await bot.close()
