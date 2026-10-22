"""Authentication endpoints (spec 20.5, 46)."""

import logging
import time

from django.contrib.auth import authenticate, get_user_model
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken

from apps.audit.models import AuditAction
from apps.authentication.models import LoginAuditLog
from apps.authentication.serializers import LoginSerializer, PasswordChangeSerializer
from apps.authentication.services import (
    build_session_payload,
    issue_tokens,
    revoke_all_user_tokens,
    revoke_refresh_token,
)
from common.authentication.cookies import clear_auth_cookies, set_auth_cookies
from common.authentication.cookie_jwt import CookieJWTAuthentication
from common.authentication.session import revoke_session, session_metadata, validate_session
from common.responses import fail, ok
from common.services.audit import record_audit
from common.throttling import LoginRateThrottle

logger = logging.getLogger(__name__)

# Spec 20.5: the same message regardless of cause, so responses cannot be used
# to discover which usernames exist.
GENERIC_LOGIN_ERROR = "Invalid username or password."


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


class AuthAPIView(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class LoginView(AuthAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        CookieJWTAuthentication()._enforce_csrf(request)
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data["username"]
        password = serializer.validated_data["password"]

        user = authenticate(request, username=username, password=password)

        from common.permissions.company import can_access_company
        if user is None or not can_access_company(user, user):
            LoginAuditLog.objects.create(
                attempted_username=username[:150], was_successful=False,
                failure_reason="invalid_credentials",
                ip_address=_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
            )
            return fail({"detail": [GENERIC_LOGIN_ERROR]},
                        message=GENERIC_LOGIN_ERROR,
                        status=status.HTTP_401_UNAUTHORIZED)

        refresh, access = issue_tokens(user)

        LoginAuditLog.objects.create(
            user=user, attempted_username=username[:150], was_successful=True,
            ip_address=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        )
        record_audit(action=AuditAction.LOGIN, entity=user, actor=user, request=request)

        payload = build_session_payload(user, access)
        response = ok(payload, message="Signed in successfully.")
        set_auth_cookies(response, access, refresh)
        # Issue a CSRF token alongside, so the SPA can immediately make
        # unsafe requests without a separate bootstrap call.
        get_token(request)
        return response


class LogoutView(AuthAPIView):
    # Expired access tokens must not prevent cookie cleanup/revocation.
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        CookieJWTAuthentication()._enforce_csrf(request)
        raw_refresh = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        raw_token = raw_refresh or request.COOKIES.get(settings.AUTH_COOKIE_NAME)
        if raw_token:
            try:
                # Signature/expiry are checked, but an already-rotated refresh
                # must still be able to revoke its shared server session.
                token = UntypedToken(raw_token)
                revoke_session(token)
                user = get_user_model().objects.filter(pk=token.get("user_id")).first()
                if user:
                    record_audit(action=AuditAction.LOGOUT, entity=user, actor=user, request=request)
            except TokenError:
                pass
        revoke_refresh_token(raw_refresh)
        # Always 200: logging out twice is not an error.
        response = ok(None, message="Signed out successfully.")
        clear_auth_cookies(response)
        return response


class RefreshView(AuthAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        CookieJWTAuthentication()._enforce_csrf(request)
        raw_refresh = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        if not raw_refresh:
            return fail({"detail": ["No refresh token supplied."]},
                        message="Session expired. Please sign in again.",
                        status=status.HTTP_401_UNAUTHORIZED)

        try:
            refresh = RefreshToken(raw_refresh)
            # ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION mean the old
            # token is burned here; replaying it later is detected.
            user_id = refresh.payload.get("user_id")
            user = get_user_model().objects.get(id=user_id, is_active=True, is_deleted=False)
            from common.permissions.company import can_access_company
            if not can_access_company(user, user):
                raise AuthenticationFailed("Your company access is inactive.")
            validate_session(refresh, user)
            new_refresh = refresh
            if settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
                try:
                    refresh.blacklist()
                except AttributeError:  # pragma: no cover - blacklist app absent
                    pass
                new_refresh, access = issue_tokens(user, session_id=refresh["sid"])
            else:
                access = refresh.access_token
                access["exp"] = min(access["exp"], refresh["exp"])
        except (TokenError, AuthenticationFailed, get_user_model().DoesNotExist):
            response = fail({"detail": ["Invalid or expired refresh token."]},
                            message="Session expired. Please sign in again.",
                            status=status.HTTP_401_UNAUTHORIZED)
            clear_auth_cookies(response)
            return response

        response = ok(session_metadata(validate_session(access, user)), message="Session refreshed.")
        set_auth_cookies(response, access, new_refresh)
        return response


class MeView(AuthAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return ok(build_session_payload(request.user, request.auth), message="Session loaded.")


class SessionActivityView(AuthAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Serialize against concurrent logout: saving activity must never
        # recreate a session which has just been revoked.
        with transaction.atomic():
            Session.objects.select_for_update().filter(session_key=request.auth["sid"]).first()
            session = validate_session(request.auth, request.user)
            session["last_activity"] = int(time.time())
            session.save()
            return ok(session_metadata(session), message="Session activity recorded.")


class CsrfView(AuthAPIView):
    """Sets the csrftoken cookie so the SPA can bootstrap before login."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        get_token(request)
        return ok(None, message="CSRF cookie set.")


class PasswordChangeView(AuthAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user

        if not user.check_password(serializer.validated_data["current_password"]):
            return fail({"current_password": ["The current password is incorrect."]},
                        message="Validation failed.")

        new_password = serializer.validated_data["new_password"]
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            return fail({"new_password": list(exc.messages)}, message="Validation failed.")

        user.set_password(new_password)
        user.last_password_change_at = timezone.now()
        user.must_change_password = False
        user.save(update_fields=[
            "password", "last_password_change_at", "must_change_password", "updated_at",
        ])

        # End every other session, then re-issue for this one so the caller is
        # not logged out of the device they just used.
        revoke_all_user_tokens(user)
        record_audit(action=AuditAction.PASSWORD_CHANGED, entity=user,
                     actor=user, request=request)

        refresh, access = issue_tokens(user)
        response = ok(None, message="Password changed successfully.")
        set_auth_cookies(response, access, refresh)
        return response
