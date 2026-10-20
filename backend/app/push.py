"""Browser (PWA) push notifications — a fallback channel when Telegram is unavailable."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from . import prefs
from .config import get_settings
from .db import PushSubscription, session

log = logging.getLogger(__name__)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def vapid_keys() -> tuple[str, str]:
    """(private_pem, public_key_b64url) — generated once and kept in the database."""
    priv = prefs.get("vapid_private_pem")
    pub = prefs.get("vapid_public_key")
    if priv and pub:
        return priv, pub
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub = _b64url(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    prefs.set("vapid_private_pem", priv)
    prefs.set("vapid_public_key", pub)
    return priv, pub


def subscribe(sub: dict[str, Any], user_agent: str = "") -> int:
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("subscription نامعتبر است")
    with session() as s:
        row = s.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
        if row is None:
            row = PushSubscription(endpoint=endpoint, keys=keys, user_agent=user_agent[:300])
            s.add(row)
        else:
            row.keys = keys
        s.flush()
        return row.id


def unsubscribe(endpoint: str) -> None:
    with session() as s:
        s.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).delete()


def count() -> int:
    with session() as s:
        return s.query(PushSubscription).count()


def _send_one(sub: dict[str, Any], payload: str, priv_pem: str) -> int:
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush

    try:
        webpush(subscription_info=sub, data=payload, vapid_private_key=Vapid01.from_pem(priv_pem.encode()),
                vapid_claims={"sub": get_settings().vapid_subject}, ttl=3600)
        return 200
    except WebPushException as e:
        code = getattr(getattr(e, "response", None), "status_code", 0) or 0
        log.info("push failed (%s): %s", code, e)
        return code
    except Exception as e:  # noqa: BLE001 - network errors etc.
        log.info("push failed: %s", e)
        return 0


async def notify_all(title: str, body: str, tag: str = "peygir", url: str = "/#/chat") -> int:
    with session() as s:
        subs = [(r.id, {"endpoint": r.endpoint, "keys": r.keys}) for r in s.query(PushSubscription).all()]
    if not subs:
        return 0
    priv, _ = vapid_keys()
    payload = json.dumps({"title": title, "body": body, "tag": tag, "url": url}, ensure_ascii=False)
    sent = 0
    for sid, sub in subs:
        code = await asyncio.to_thread(_send_one, sub, payload, priv)
        if code in (404, 410):  # subscription expired
            with session() as s:
                s.query(PushSubscription).filter(PushSubscription.id == sid).delete()
        elif code < 300:
            sent += 1
    return sent
