"""Optional password protection for the web UI (set APP_PASSWORD)."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, TimestampSigner

from ..config import get_settings

COOKIE = "peygir_session"
MAX_AGE = 60 * 60 * 24 * 60  # 60 days


def _signer() -> TimestampSigner:
    return TimestampSigner(get_settings().secret_key + get_settings().app_password)


def auth_required() -> bool:
    return bool(get_settings().app_password)


def check_password(password: str) -> bool:
    return hmac.compare_digest(password.encode(), get_settings().app_password.encode())


def make_token() -> str:
    return _signer().sign(b"owner").decode()


def is_authenticated(request: Request) -> bool:
    if not auth_required():
        return True
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    try:
        _signer().unsign(token, max_age=MAX_AGE)
        return True
    except BadSignature:
        return False


def require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="unauthorized")
