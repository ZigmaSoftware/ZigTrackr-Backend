"""Development settings."""

from .base import *  # noqa: F403

DEBUG = True

# Vite uses 5174 when 5173 is occupied. Keep both loopback spellings explicit
# so cookie-authenticated POSTs pass Django's Origin check in local development.
_vite_fallback_origins = ("http://localhost:5174", "http://127.0.0.1:5174")
CORS_ALLOWED_ORIGINS = list(dict.fromkeys([*CORS_ALLOWED_ORIGINS, *_vite_fallback_origins]))  # noqa: F405
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys([*CSRF_TRUSTED_ORIGINS, *_vite_fallback_origins]))  # noqa: F405
