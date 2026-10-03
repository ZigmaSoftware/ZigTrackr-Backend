"""Cookie-JWT authentication tests (spec 20.5, 46)."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from apps.accounts.models import Role, UserRole

User = get_user_model()
PASSWORD = "Zigma@12345"


class AuthFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="kiran", full_name="Kiran Kumar",
                                       email="kiran@example.com")
        cls.user.set_password(PASSWORD)
        cls.user.save()
        role = Role.objects.create(code="DEVELOPER", name="Developer")
        UserRole.objects.create(user=cls.user, role=role)

    def setUp(self):
        # The login throttle counts attempts in the shared cache, which would
        # otherwise leak between tests and 429 the later ones.
        cache.clear()
        # enforce_csrf_checks mirrors a real browser: cookie auth means CSRF is
        # live, and a client that skips it would test a fiction.
        self.client = Client(enforce_csrf_checks=True)

    def _csrf(self):
        self.client.get("/api/v1/auth/csrf/")
        return self.client.cookies["csrftoken"].value

    def _login(self, username="kiran", password=PASSWORD):
        csrf = self._csrf()
        return self.client.post(
            "/api/v1/auth/login/",
            data={"username": username, "password": password},
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf,
        )

    def test_login_sets_httponly_cookies(self):
        response = self._login()
        self.assertEqual(response.status_code, 200)
        access = response.cookies[settings.AUTH_COOKIE_NAME]
        refresh = response.cookies[settings.REFRESH_COOKIE_NAME]
        self.assertTrue(access["httponly"], "Access cookie must be HttpOnly")
        self.assertTrue(refresh["httponly"], "Refresh cookie must be HttpOnly")
        # The refresh cookie is scoped to the auth endpoints only.
        self.assertEqual(refresh["path"], settings.REFRESH_COOKIE_PATH)

    def test_login_body_contains_no_token(self):
        """Spec 46: tokens must never reach JavaScript."""
        response = self._login()
        body = response.content.decode()
        self.assertNotIn("access", body.lower().split('"data"')[0])
        self.assertNotIn("refresh_token", body)
        data = response.json()["data"]
        self.assertNotIn("token", data)
        self.assertNotIn("access", data)

    def test_login_returns_roles_and_permissions(self):
        data = self._login().json()["data"]
        self.assertEqual(data["username"], "kiran")
        self.assertEqual(data["roles"][0]["code"], "DEVELOPER")
        self.assertIn("permissions", data)

    def test_invalid_credentials_are_generic(self):
        """Spec 20.5: never reveal whether the username exists."""
        wrong_password = self._login(password="nope")
        unknown_user = self._login(username="ghost", password="nope")
        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(unknown_user.status_code, 401)
        self.assertEqual(wrong_password.json()["message"], unknown_user.json()["message"])

    def test_authenticated_request_uses_cookie(self):
        self._login()
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["username"], "kiran")

    def test_unauthenticated_request_is_rejected(self):
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)

    def test_unsafe_request_without_csrf_is_rejected(self):
        self._login()
        response = self.client.post(
            "/api/v1/auth/password/change/",
            data={"current_password": PASSWORD, "new_password": "Another@12345",
                  "confirm_password": "Another@12345"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.json()["message"])

    def test_logout_invalidates_refresh_token(self):
        """The requirement that justified using simplejwt over a hand-rolled JWT."""
        self._login()
        csrf = self.client.cookies["csrftoken"].value
        self.assertEqual(
            self.client.post("/api/v1/auth/logout/", HTTP_X_CSRFTOKEN=csrf).status_code, 200
        )
        # Re-present the refresh cookie captured before logout.
        refreshed = self.client.post("/api/v1/auth/refresh/", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(refreshed.status_code, 401)

    def test_refresh_rotates_and_burns_the_old_token(self):
        self._login()
        csrf = self.client.cookies["csrftoken"].value
        old_refresh = self.client.cookies[settings.REFRESH_COOKIE_NAME].value

        first = self.client.post("/api/v1/auth/refresh/", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(first.status_code, 200)
        new_refresh = self.client.cookies[settings.REFRESH_COOKIE_NAME].value
        self.assertNotEqual(old_refresh, new_refresh, "Refresh token should rotate")

        # Replaying the pre-rotation token must fail (replay detection).
        self.client.cookies[settings.REFRESH_COOKIE_NAME] = old_refresh
        replay = self.client.post("/api/v1/auth/refresh/", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(replay.status_code, 401)

    def test_password_change_requires_correct_current_password(self):
        self._login()
        csrf = self.client.cookies["csrftoken"].value
        response = self.client.post(
            "/api/v1/auth/password/change/",
            data={"current_password": "wrong", "new_password": "Another@12345",
                  "confirm_password": "Another@12345"},
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_password", response.json()["errors"])

    def test_password_change_rejects_mismatched_confirmation(self):
        self._login()
        csrf = self.client.cookies["csrftoken"].value
        response = self.client.post(
            "/api/v1/auth/password/change/",
            data={"current_password": PASSWORD, "new_password": "Another@12345",
                  "confirm_password": "Different@12345"},
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 400)

    def test_password_change_succeeds_and_sets_new_password(self):
        self._login()
        csrf = self.client.cookies["csrftoken"].value
        response = self.client.post(
            "/api/v1/auth/password/change/",
            data={"current_password": PASSWORD, "new_password": "Another@12345",
                  "confirm_password": "Another@12345"},
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Another@12345"))

    def test_login_accepts_email_as_username(self):
        csrf = self._csrf()
        response = self.client.post(
            "/api/v1/auth/login/",
            data={"username": "kiran@example.com", "password": PASSWORD},
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 200)

    def test_username_and_email_require_exact_case(self):
        self.user.username = "Kiran"
        self.user.email = "Kiran@Example.com"
        self.user.save(update_fields=["username", "email"])
        self.assertEqual(self._login(username="kiran").status_code, 401)
        self.assertEqual(self._login(username="Kiran").status_code, 200)
        self.assertEqual(self._login(username="kiran@example.com").status_code, 401)
        self.assertEqual(self._login(username="Kiran@Example.com").status_code, 200)

    def test_ambiguous_exact_identifier_is_rejected(self):
        other = User.objects.create_user(username="kiran@example.com", password=PASSWORD)
        self.assertEqual(self._login(username="kiran@example.com").status_code, 401)
        self.assertTrue(other.check_password(PASSWORD))

    def test_inactive_user_cannot_authenticate(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self._login().status_code, 401)


class LoginThrottleTests(TestCase):
    """Spec 20.5 / 46: login must be rate limited."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="target", full_name="Target")
        cls.user.set_password(PASSWORD)
        cls.user.save()

    def setUp(self):
        cache.clear()

    def test_repeated_failures_are_throttled(self):
        client = Client()
        statuses = []
        for _ in range(8):
            response = client.post(
                "/api/v1/auth/login/",
                data={"username": "target", "password": "wrong"},
                content_type="application/json",
            )
            statuses.append(response.status_code)
        self.assertIn(429, statuses, "Login should be throttled after repeated failures")
        # The configured rate is 5/min, so the sixth attempt onward is blocked.
        self.assertEqual(statuses[:5], [401] * 5)
        self.assertEqual(statuses[5], 429)
