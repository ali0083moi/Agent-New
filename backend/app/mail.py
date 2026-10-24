"""Gmail for the supervisor search, over IMAP with an App Password (no Google Cloud project or API key).

Every 15 minutes:
- INBOX: an email from a professor in the contacts → status "replied", a note, and a message to the user with the gist.
- Sent: an email to a professor → status "contacted" with that date (which schedules the follow-up reminder).
Drafts written by the assistant are saved straight into the Gmail Drafts folder.

The first check only catches up silently (statuses and dates), so connecting doesn't flood the chat with old mail.
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import logging
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage
from typing import Any

from . import prefs, timeutil
from .config import get_settings

log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
FIRST_RUN_DAYS = 60
MAX_NEW = 300


class MailError(Exception):
    pass


def credentials() -> tuple[str, str] | None:
    st = get_settings()
    address = prefs.get("gmail_address") or st.gmail_address
    password = prefs.get("gmail_app_password") or st.gmail_app_password
    return (address, password.replace(" ", "")) if address and password else None


def connect(creds: tuple[str, str] | None = None) -> imaplib.IMAP4_SSL:
    creds = creds or credentials()
    if creds is None:
        raise MailError("Gmail وصل نیست (آدرس و App Password در تنظیمات)")
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST, timeout=30)
        m.login(*creds)
    except imaplib.IMAP4.error as e:
        raise MailError(f"ورود به Gmail ناموفق بود: {e}. App Password درست است و IMAP روشن است؟") from e
    except OSError as e:
        raise MailError(f"اتصال به Gmail برقرار نشد: {e}") from e
    return m


def folders(m: imaplib.IMAP4_SSL) -> dict[str, str]:
    """Gmail's special folders by role (their names depend on the account language)."""
    out = {"inbox": "INBOX"}
    typ, data = m.list()
    for raw in data or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        match = re.match(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)$', line)
        if not match:
            continue
        name = match.group("name").strip()
        for flag, role in (("\\Sent", "sent"), ("\\Drafts", "drafts"), ("\\All", "all")):
            if flag in match.group("flags"):
                out[role] = name
    return out


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _addresses(value: str | None) -> list[str]:
    return [a.lower() for _, a in email.utils.getaddresses([value or ""]) if a]


def _when(value: str | None) -> datetime:
    try:
        d = email.utils.parsedate_to_datetime(value)
        return d.astimezone(timeutil.tz()).replace(tzinfo=None) if d.tzinfo else d
    except (TypeError, ValueError):
        return timeutil.now()


def body_text(msg: email.message.Message, limit: int = 2500) -> str:
    """Plain text of an email, without the quoted earlier messages."""
    part = None
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() == "text/plain" and not p.get_filename():
                part = p
                break
        if part is None:
            for p in msg.walk():
                if p.get_content_type() == "text/html":
                    part = p
                    break
    else:
        part = msg
    if part is None:
        return ""
    raw = part.get_payload(decode=True) or b""
    text = raw.decode(part.get_content_charset() or "utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    lines = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .+ wrote:$", line.strip()):
            break
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:limit]


def _select(m: imaplib.IMAP4_SSL, folder: str) -> int:
    typ, _ = m.select(f'"{folder}"' if " " in folder and not folder.startswith('"') else folder, readonly=True)
    if typ != "OK":
        raise MailError(f"پوشه {folder} باز نشد")
    typ, data = m.response("UIDVALIDITY")
    return int(data[0]) if data and data[0] else 0


def _new_uids(m: imaplib.IMAP4_SSL, last: int | None, since_days: int) -> list[int]:
    if last:
        typ, data = m.uid("search", None, f"UID {last + 1}:*")
    else:
        since = (timeutil.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        typ, data = m.uid("search", None, f"SINCE {since}")
    uids = [int(x) for x in (data[0] or b"").split()] if typ == "OK" else []
    return [u for u in uids if not last or u > last][-MAX_NEW:]


def _headers(m: imaplib.IMAP4_SSL, uids: list[int]) -> list[tuple[int, email.message.Message]]:
    out = []
    for i in range(0, len(uids), 50):
        chunk = ",".join(str(u) for u in uids[i:i + 50])
        typ, data = m.uid("fetch", chunk, "(UID BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE)])")
        for item in data or []:
            if isinstance(item, tuple):
                uid = int(re.search(rb"UID (\d+)", item[0]).group(1))
                out.append((uid, email.message_from_bytes(item[1])))
    return out


def _full(m: imaplib.IMAP4_SSL, uid: int) -> email.message.Message | None:
    typ, data = m.uid("fetch", str(uid), "(BODY.PEEK[])")
    for item in data or []:
        if isinstance(item, tuple):
            return email.message_from_bytes(item[1])
    return None


def scan(m: imaplib.IMAP4_SSL, contacts: dict[str, int], state: dict[str, Any]) -> dict[str, Any]:
    """New replies from / emails to known contacts since the last check. Updates `state` in place."""
    first = not state.get("initialized")
    found: dict[str, list[dict[str, Any]]] = {"replies": [], "sent": []}
    roles = folders(m)
    for role, key in (("inbox", "replies"), ("sent", "sent")):
        folder = roles.get(role)
        if not folder:
            continue
        validity = _select(m, folder)
        st = state.setdefault(role, {})
        if st.get("validity") != validity:
            st.clear()
            st["validity"] = validity
        uids = _new_uids(m, st.get("last_uid"), FIRST_RUN_DAYS)
        for uid, h in _headers(m, uids):
            people = _addresses(h.get("From")) if role == "inbox" else _addresses(h.get("To")) + _addresses(h.get("Cc"))
            hits = [contacts[a] for a in people if a in contacts]
            if hits:
                item = {"contact_id": hits[0], "subject": _decode(h.get("Subject")), "date": _when(h.get("Date")), "uid": uid}
                if role == "inbox" and not first:
                    full = _full(m, uid)
                    item["text"] = body_text(full) if full is not None else ""
                found[key].append(item)
        if uids:
            st["last_uid"] = max(uids)
    state["initialized"] = True
    found["first_run"] = first
    return found


def apply(found: dict[str, Any]) -> dict[str, int]:
    """Update contacts from what was found; schedule a message for each new reply."""
    from . import projects
    from .db import Contact, Followup, session

    counts = {"replies": 0, "sent": 0}
    with session() as s:
        for it in sorted(found["sent"], key=lambda x: x["date"]):
            c = s.get(Contact, it["contact_id"])
            if c is None:
                continue
            if c.last_contact_at is None or it["date"] > c.last_contact_at:
                if c.status in ("to_contact", "contacted", "no_reply"):
                    projects.apply_status(c, "contacted", it["date"])
                else:
                    c.last_contact_at = it["date"]
                c.notes = (c.notes + "\n" if c.notes else "") + f"[{timeutil.jalali_str(it['date'])}] ایمیل فرستادی: {it['subject']}"
                counts["sent"] += 1
        for it in sorted(found["replies"], key=lambda x: x["date"]):
            c = s.get(Contact, it["contact_id"])
            if c is None:
                continue
            if c.status in ("to_contact", "contacted", "no_reply"):
                projects.apply_status(c, "replied", it["date"])
            c.notes = (c.notes + "\n" if c.notes else "") + f"[{timeutil.jalali_str(it['date'])}] جواب ایمیلی: {it['subject']}"
            counts["replies"] += 1
            if not found.get("first_run"):
                s.add(Followup(kind="contact_reply", due_at=timeutil.now(), contact_id=c.id,
                               dedupe_key=f"mail:{it['uid']}:{c.id}",
                               payload={"subject": it["subject"], "text": it.get("text", ""), "date": timeutil.iso(it["date"])}))
    return counts


def _check_sync() -> dict[str, Any]:
    from .db import Contact, session

    with session() as s:
        contacts = {c.email.strip().lower(): c.id for c in s.query(Contact).all() if c.email and "@" in c.email}
    state = prefs.get("mail_state") or {}
    m = connect()
    try:
        found = scan(m, contacts, state)
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass
    counts = apply(found)
    state["last_check"] = timeutil.iso(timeutil.now())
    state["error"] = ""
    prefs.set("mail_state", state)
    return {"ok": True, **counts, "first_run": found["first_run"]}


async def check() -> dict[str, Any]:
    if credentials() is None:
        return {"ok": True, "skipped": True}
    try:
        res = await asyncio.to_thread(_check_sync)
    except MailError as e:
        state = prefs.get("mail_state") or {}
        state["error"], state["last_check"] = str(e), timeutil.iso(timeutil.now())
        prefs.set("mail_state", state)
        return {"ok": False, "error": str(e)}
    if res.get("replies") or res.get("sent"):
        from .bus import bus

        bus.publish("data_changed", ["contacts", "followups"])
    return res


def _draft_sync(to: str, subject: str, body: str, cc: str = "") -> str:
    creds = credentials()
    m = connect(creds)
    try:
        box = folders(m).get("drafts")
        if not box:
            raise MailError("پوشه Drafts در Gmail پیدا نشد")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = creds[0], to, subject
        if cc:
            msg["Cc"] = cc
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg.set_content(body)
        typ, data = m.append(box, "(\\Draft)", imaplib.Time2Internaldate(datetime.now().astimezone()), msg.as_bytes())
        if typ != "OK":
            raise MailError(f"ذخیره پیش‌نویس ناموفق بود: {data}")
        return box
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass


async def save_draft(to: str, subject: str, body: str, cc: str = "") -> str:
    return await asyncio.to_thread(_draft_sync, to, subject, body, cc)


async def test(address: str, password: str) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        m = connect((address, password.replace(" ", "")))
        try:
            return {"ok": True, "folders": folders(m)}
        finally:
            m.logout()

    return await asyncio.to_thread(run)
