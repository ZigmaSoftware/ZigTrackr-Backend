"""Production settings. Fail fast on missing configuration."""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False

# DRF login/public throttles must share state across Daphne processes.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("CACHE_REDIS_URL", "redis://127.0.0.1:6379/1"),
    },
}
REST_FRAMEWORK["NUM_PROXIES"] = 1

# ---- FAIL FAST ----
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
ALLOWED_HOSTS = [h.strip() for h in os.environ["DJANGO_ALLOWED_HOSTS"].split(",") if h.strip()]

# ---- HTTPS / COOKIES ----
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
AUTH_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# ---- MAIL CONFIGURATION FAIL FAST ----
# IMAP credentials are only required when intake is enabled. SMTP is required
# whenever requester notices are enabled, including manual ticket actions.
if MAIL_INTAKE_ENABLED:  # noqa: F405
    MAIL_INTAKE_APP_PASSWORD = os.environ["MAIL_INTAKE_APP_PASSWORD"]
    MAIL_INTAKE_USERNAME = os.environ["MAIL_INTAKE_USERNAME"]
    MAIL_INTAKE_EMAIL = os.environ["MAIL_INTAKE_EMAIL"]
# The console backend prints mail to stdout instead of sending it.
if TICKET_ACK_ENABLED and "console" in EMAIL_BACKEND:  # noqa: F405
    raise ImproperlyConfigured(
        "TICKET_ACK_ENABLED is true but EMAIL_BACKEND is the console backend. "
        "Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend."
    )
