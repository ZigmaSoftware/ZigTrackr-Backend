"""JWT delivered in HttpOnly cookies (spec 20.5, 46).

Spec 46 states twice that JWTs must not be stored in localStorage. A token in
an HttpOnly cookie is unreadable by JavaScript, which closes the XSS
token-theft path. The trade-off is that browsers attach cookies automatically,
which reintroduces CSRF -- so this class *enforces* CSRF on every unsafe
cookie-authenticated request. That enforcement is not optional and must not be
disabled to "make the frontend work": the SPA reads the readable `csrftoken`
cookie and echoes it in the X-CSRFToken header.
"""

from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware
from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication

SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")


class _CSRFCheck(CsrfViewMiddleware):
    def _reject(self, request, reason):
        return reason


class CookieJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        raw_token = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
        from_cookie = raw_token is not None

        # Header auth is allowed only in DEBUG so Swagger and curl stay usable
        # during development. Production is cookie-only.
        if not from_cookie and settings.DEBUG:
            header = self.get_header(request)
            if header is not None:
                raw_token = self.get_raw_token(header)

        if not raw_token:
            return None

        validated_token = self.get_validated_token(raw_token)
        user = self.get_user(validated_token)

        if not user.is_active or getattr(user, "is_deleted", False):
            raise exceptions.AuthenticationFailed("User account is inactive.")

        if from_cookie:
            self._enforce_csrf(request)

        return (user, validated_token)

    def _enforce_csrf(self, request):
        if request.method in SAFE_METHODS:
            return
        check = _CSRFCheck(lambda req: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise exceptions.PermissionDenied(f"CSRF Failed: {reason}")
