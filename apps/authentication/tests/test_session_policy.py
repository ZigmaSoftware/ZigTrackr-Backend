"""Real cookie/CSRF requests exercise idle, absolute and revoked sessions."""

import time
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

PASSWORD = "Session@12345"


@override_settings(AUTH_SESSION_IDLE_SECONDS=120, AUTH_SESSION_MAX_SECONDS=3600)
class SessionPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="session_user", password=PASSWORD)

    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        self.client.get("/api/v1/auth/csrf/")
        self.csrf = self.client.cookies["csrftoken"].value
        response = self.client.post(
            "/api/v1/auth/login/", {"username": "session_user", "password": PASSWORD},
            content_type="application/json", HTTP_X_CSRFTOKEN=self.csrf,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.policy = response.json()["data"]["session"]
        self.access = self.client.cookies[settings.AUTH_COOKIE_NAME].value
        self.refresh = self.client.cookies[settings.REFRESH_COOKIE_NAME].value
        self.sid = AccessToken(self.access)["sid"]

    def post(self, path):
        return self.client.post(f"/api/v1/auth/{path}/", HTTP_X_CSRFTOKEN=self.csrf)

    def age_session(self, **values):
        session = SessionStore(session_key=self.sid)
        session.update(values)
        session.save()

    def test_metadata_and_cookies_are_bounded_by_absolute_expiry(self):
        self.assertEqual(self.policy["idle_timeout_seconds"], 120)
        self.assertLessEqual(RefreshToken(self.refresh)["exp"], self.policy["absolute_expires_at"])
        self.assertLessEqual(AccessToken(self.access)["exp"], self.policy["absolute_expires_at"])
        self.assertEqual(self.client.get("/api/v1/auth/me/")["Cache-Control"], "no-store")

    def test_idle_expiry_rejects_unexpired_access_refresh_and_activity(self):
        self.age_session(last_activity=int(time.time()) - 120)
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)
        self.assertEqual(self.post("activity").status_code, 401)
        self.assertEqual(self.post("refresh").status_code, 401)

    def test_polling_and_refresh_do_not_extend_idle_or_absolute_expiry(self):
        earlier = int(time.time()) - 90
        self.age_session(last_activity=earlier)
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 200)
        result = self.post("refresh")
        self.assertEqual(result.status_code, 200, result.content)
        token = RefreshToken(self.client.cookies[settings.REFRESH_COOKIE_NAME].value)
        self.assertEqual(token["sid"], self.sid)
        self.assertEqual(token["exp"], RefreshToken(self.refresh, verify=False)["exp"])
        self.assertEqual(SessionStore(session_key=self.sid)["last_activity"], earlier)

    def test_activity_extends_idle_but_not_absolute_expiry(self):
        self.age_session(last_activity=int(time.time()) - 90)
        data = self.post("activity").json()["data"]
        self.assertGreater(data["idle_expires_at"], self.policy["idle_expires_at"] - 90)
        self.assertEqual(data["absolute_expires_at"], self.policy["absolute_expires_at"])

    def test_absolute_expiry_cannot_be_extended_by_activity_or_refresh(self):
        self.age_session(absolute_expiry=int(time.time()) - 1)
        self.assertEqual(self.post("activity").status_code, 401)
        self.assertEqual(self.post("refresh").status_code, 401)

    def test_logout_revokes_captured_access_and_refresh_and_is_idempotent(self):
        self.assertEqual(self.post("logout").status_code, 200)
        self.assertEqual(self.post("logout").status_code, 200)
        self.client.cookies[settings.AUTH_COOKIE_NAME] = self.access
        self.client.cookies[settings.REFRESH_COOKIE_NAME] = self.refresh
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)
        self.assertEqual(self.post("refresh").status_code, 401)

    def test_logout_works_with_expired_access_and_revokes_rotated_session(self):
        self.assertEqual(self.post("refresh").status_code, 200)
        # An in-flight refresh may have rotated this browser's old cookie.
        self.client.cookies[settings.REFRESH_COOKIE_NAME] = self.refresh
        access = AccessToken(self.access)
        access["exp"] = int(time.time()) - 1
        self.client.cookies[settings.AUTH_COOKIE_NAME] = str(access)
        self.assertEqual(self.post("logout").status_code, 200)
        self.assertIsNone(SessionStore(session_key=self.sid).get("staff_user_id"))

    def test_refresh_logout_and_activity_require_csrf(self):
        for path in ("refresh", "logout", "activity"):
            with self.subTest(path=path):
                result = self.client.post(f"/api/v1/auth/{path}/")
                self.assertEqual(result.status_code, 403)

    def test_inactive_account_cannot_refresh(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.post("refresh").status_code, 401)

    def test_password_change_revokes_existing_access_immediately(self):
        self.user.set_password("NewSession@12345")
        self.user.save(update_fields=["password"])
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)
        self.assertEqual(self.post("refresh").status_code, 401)

    def test_legacy_tokens_without_session_are_not_silently_upgraded(self):
        legacy = RefreshToken.for_user(self.user)
        self.client.cookies[settings.AUTH_COOKIE_NAME] = str(legacy.access_token)
        self.client.cookies[settings.REFRESH_COOKIE_NAME] = str(legacy)
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)
        self.assertEqual(self.post("refresh").status_code, 401)

    def test_client_heartbeat_does_not_recreate_a_revoked_session(self):
        SessionStore(session_key=self.sid).delete()
        self.assertEqual(self.post("activity").status_code, 401)

    def test_absolute_limit_is_preserved_when_tokens_rotate_later(self):
        deadline = self.policy["absolute_expires_at"]
        # Fast-forward the token issuer only; the session remains active.
        with patch("rest_framework_simplejwt.tokens.aware_utcnow") as now:
            from datetime import datetime, timezone
            now.return_value = datetime.fromtimestamp(deadline - 60, timezone.utc)
            result = self.post("refresh")
        self.assertEqual(result.status_code, 200, result.content)
        # The simulated issuer clock is ahead of the verifier's real clock.
        token = RefreshToken(self.client.cookies[settings.REFRESH_COOKIE_NAME].value, verify=False)
        self.assertEqual(token["exp"], deadline)
        self.assertEqual(token["sid"], self.sid)
