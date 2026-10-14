"""Revocable, server-enforced staff sessions using Django's existing session table.

Only the explicit activity endpoint extends idle time. Polls, refreshes and
WebSocket traffic must not keep an unattended browser signed in.
"""

import time
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.utils.crypto import constant_time_compare
from rest_framework.exceptions import AuthenticationFailed


def create_session(user):
    now = int(time.time())
    session = SessionStore()
    session["staff_user_id"] = str(user.pk)
    session["password_hash"] = user.get_session_auth_hash()
    session["last_activity"] = now
    session["absolute_expiry"] = now + settings.AUTH_SESSION_MAX_SECONDS
    session.set_expiry(datetime.fromtimestamp(session["absolute_expiry"], dt_timezone.utc))
    session.create()
    return session


def validate_session(token, user):
    sid = token.get("sid")
    if not isinstance(sid, str) or len(sid) != 32:
        # Old JWTs must be replaced by a fresh login, not silently upgraded.
        raise AuthenticationFailed("Session expired. Please sign in again.")
    session = SessionStore(session_key=sid)
    now = int(time.time())
    if (
        session.get("staff_user_id") != str(user.pk)
        or not constant_time_compare(session.get("password_hash", ""), user.get_session_auth_hash())
        or now >= session.get("absolute_expiry", 0)
        or now >= session.get("last_activity", 0) + settings.AUTH_SESSION_IDLE_SECONDS
    ):
        raise AuthenticationFailed("Session expired. Please sign in again.")
    return session


def session_metadata(session):
    return {
        "idle_timeout_seconds": settings.AUTH_SESSION_IDLE_SECONDS,
        "warning_seconds": min(60, settings.AUTH_SESSION_IDLE_SECONDS // 2),
        "idle_expires_at": session["last_activity"] + settings.AUTH_SESSION_IDLE_SECONDS,
        "absolute_expires_at": session["absolute_expiry"],
        "server_time": int(time.time()),
    }


def revoke_session(token):
    sid = token.get("sid")
    if isinstance(sid, str) and len(sid) == 32:
        SessionStore(session_key=sid).delete()
