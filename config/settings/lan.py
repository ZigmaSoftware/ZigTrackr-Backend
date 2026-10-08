"""Production behavior for the private HTTP LAN deployment."""

from .prod import *  # noqa: F403

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
AUTH_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
