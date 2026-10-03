"""Base settings for ZigTrackr.

Layout follows the house convention: a settings *package* with base/dev/prod,
section banner comments, and `os.getenv(...)` with inline defaults.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

# ---- PATHS ----
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

# ---- SECURITY ----
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

AUTH_USER_MODEL = "accounts.User"

# ---- APPLICATIONS ----
INSTALLED_APPS = [
    "corsheaders",  # MUST BE FIRST
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # ---- THIRD PARTY ----
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    # ---- LOCAL ----
    "apps.accounts",
    "apps.authentication",
    "apps.masters",
    "apps.bugs",
    "apps.dashboard",
    "apps.teams",
    "apps.reports",
    "apps.audit",
    "apps.notifications",
    "apps.tickets",
    "apps.mail_intake",
    "apps.classification",
]

# ---- MIDDLEWARE ----
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # MUST BE FIRST
    "django.middleware.security.SecurityMiddleware",
    "common.action_metrics.TicketActionMetricsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---- DATABASE (MariaDB) ----
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("DB_NAME", "bug_tracker_db"),
        "USER": os.getenv("DB_USER", "root"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": {
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            "charset": "utf8mb4",
        },
        "CONN_MAX_AGE": 300,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---- AUTHENTICATION ----
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.UsernameOrEmailBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---- DRF ----
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "common.authentication.cookie_jwt.CookieJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "common.pagination.limit_offset.LimitOffsetWithPage",
    "PAGE_SIZE": 25,
    "DEFAULT_RENDERER_CLASSES": [
        "common.responses.renderers.EnvelopeJSONRenderer",
    ],
    "EXCEPTION_HANDLER": "common.exceptions.handler.envelope_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "login": os.getenv("LOGIN_THROTTLE_RATE", "5/min"),
        # The public status lookup is unauthenticated and takes a guessable
        # ticket number, so the rate limit is what makes enumeration impractical.
        "public_ticket_lookup": os.getenv("PUBLIC_LOOKUP_THROTTLE_RATE", "10/min"),
        "public_track_chat": os.getenv("PUBLIC_TRACK_CHAT_THROTTLE_RATE", "30/min"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ZigTrackr API",
    "DESCRIPTION": "Internal bug and issue tracking system.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---- JWT / COOKIES (spec 20.5, 46) ----
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.getenv("JWT_ACCESS_MINUTES", "30"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.getenv("JWT_REFRESH_DAYS", "7"))),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_TYPE_CLAIM": "token_type",
    "JTI_CLAIM": "jti",
}

AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "zbt_access")
REFRESH_COOKIE_NAME = os.getenv("REFRESH_COOKIE_NAME", "zbt_refresh")
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "Lax")
REFRESH_COOKIE_PATH = "/api/v1/auth/"

# ---- CORS / CSRF ----
# Cookie auth is credentialed, so a wildcard origin is both refused by browsers
# and a real vulnerability. Explicit allow-list only.
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
# Where the app is reachable from a requester's inbox. Used to build the ticket
# tracking link; the first CORS origin is the right default because that is
# already the browser origin this backend serves.
PUBLIC_APP_URL = os.getenv(
    "PUBLIC_APP_URL",
    os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")[0].strip(),
)

CORS_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
CSRF_COOKIE_HTTPONLY = False  # the SPA must read it to echo X-CSRFToken

# ---- I18N / TIME ----
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ---- STATIC / MEDIA ----
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# MEDIA_URL is intentionally NOT wired into urlpatterns. Attachments are served
# only through a permission-checked view (spec 31); static serving of MEDIA_ROOT
# would bypass every authorization check.
MEDIA_ROOT = BASE_DIR / "media"

ATTACHMENT_MAX_SIZE_MB = int(os.getenv("ATTACHMENT_MAX_SIZE_MB", "10"))
ATTACHMENT_ALLOWED_EXTENSIONS = [
    e.strip().lower()
    for e in os.getenv(
        "ATTACHMENT_ALLOWED_EXTENSIONS",
        "jpg,jpeg,png,webp,gif,pdf,xlsx,xls,csv,txt,doc,docx,zip,mp4,webm",
    ).split(",")
    if e.strip()
]

# ---- MAIL INTAKE (IMAP) ----
# Phase 1 email intake. The whole feature is gated on MAIL_INTAKE_ENABLED: with it
# false nothing imports a mail library, no command does work, and the rest of the
# application behaves exactly as before.
MAIL_INTAKE_ENABLED = os.getenv("MAIL_INTAKE_ENABLED", "false").lower() == "true"
MAIL_INTAKE_PROVIDER = os.getenv("MAIL_INTAKE_PROVIDER", "gmail")
MAIL_INTAKE_EMAIL = os.getenv("MAIL_INTAKE_EMAIL", "")
MAIL_INTAKE_IMAP_HOST = os.getenv("MAIL_INTAKE_IMAP_HOST", "imap.gmail.com")
MAIL_INTAKE_IMAP_PORT = int(os.getenv("MAIL_INTAKE_IMAP_PORT", "993"))
MAIL_INTAKE_USE_SSL = os.getenv("MAIL_INTAKE_USE_SSL", "true").lower() == "true"
MAIL_INTAKE_USERNAME = os.getenv("MAIL_INTAKE_USERNAME", "")
# Never hard-coded, never committed. A Google App Password, not the account password.
MAIL_INTAKE_APP_PASSWORD = os.getenv("MAIL_INTAKE_APP_PASSWORD", "")
MAIL_INTAKE_FOLDER = os.getenv("MAIL_INTAKE_FOLDER", "INBOX")

# Addresses this mailbox accepts mail for. EMPTY MEANS ACCEPT ALL -- a
# misconfigured alias list must not silently reject every incoming message.
MAIL_INTAKE_ALLOWED_RECIPIENTS = [
    a.strip().lower()
    for a in os.getenv("MAIL_INTAKE_ALLOWED_RECIPIENTS", "").split(",")
    if a.strip()
]

MAIL_INTAKE_MAX_SIZE_MB = int(os.getenv("MAIL_INTAKE_MAX_SIZE_MB", "25"))
MAIL_INTAKE_MAX_ATTEMPTS = int(os.getenv("MAIL_INTAKE_MAX_ATTEMPTS", "3"))
MAIL_INTAKE_BATCH_LIMIT = int(os.getenv("MAIL_INTAKE_BATCH_LIMIT", "50"))
# Username of the non-loginable account that owns system-created tickets. The real
# sender address is kept on the ticket; see apps/accounts/services/system_user.py.
MAIL_INTAKE_SYSTEM_USERNAME = os.getenv("MAIL_INTAKE_SYSTEM_USERNAME", "mail.intake")

# MAIL_INTAKE_POLL_SECONDS is consumed by the Celery Beat schedule below.
# Do not run a second recurring cron poller alongside Beat.

# ---- CELERY / REDIS ----
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_connect_timeout": 1, "socket_timeout": 1}
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [os.getenv("CHANNEL_REDIS_URL", "redis://127.0.0.1:6379/2")]},
    },
}
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "") or None
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = os.getenv("CELERY_TIMEZONE", "Asia/Kolkata")
CELERY_ENABLE_UTC = True
CELERY_TASK_ACKS_LATE = os.getenv("CELERY_TASK_ACKS_LATE", "true").lower() == "true"
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "240"))
CELERY_TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "210"))
CELERY_BEAT_SCHEDULE = {
    "poll-support-mail": {
        "task": "apps.mail_intake.tasks.poll_support_mail",
        "schedule": float(os.getenv("MAIL_INTAKE_POLL_SECONDS", "30")),
        "options": {"queue": "mail-intake"},
    },
    "recover-outbound-mail": {
        "task": "apps.tickets.tasks.recover_outbound_mail",
        "schedule": 60.0,
        "options": {"queue": "mail-outbound"},
    },
}

# ---- OUTBOUND MAIL (SMTP) ----
# Defaults to the console backend so development and tests never open a socket.
# Production sets EMAIL_BACKEND to the SMTP backend in .env.
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "false").lower() == "true"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", MAIL_INTAKE_USERNAME)
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", MAIL_INTAKE_APP_PASSWORD)
# Mandatory, not optional: without it smtplib inherits the OS default, and one
# hung SMTP server stalls the entire intake run.
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", MAIL_INTAKE_EMAIL or "no-reply@localhost")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Acknowledgement mail carries the [TKT-YYMM-NNNN] subject that makes reply
# threading work. Disabling it does not break intake, only the reply path.
TICKET_ACK_ENABLED = os.getenv("TICKET_ACK_ENABLED", "true").lower() == "true"

# ---- CLASSIFICATION (spec 17, 19) ----
# Thresholds live here so they are configuration rather than magic numbers, but
# nothing inside the engine reads settings directly -- ClassificationConfig in
# apps/classification/config.py wraps these and is what the classifier receives.
CLASSIFY_AUTO_MIN_SCORE = int(os.getenv("CLASSIFY_AUTO_MIN_SCORE", "80"))
CLASSIFY_REVIEW_MIN_SCORE = int(os.getenv("CLASSIFY_REVIEW_MIN_SCORE", "50"))
# Access is security-sensitive, so it needs a stricter bar than the other types.
ACCESS_AUTO_CLASSIFY_MIN_SCORE = int(os.getenv("ACCESS_AUTO_CLASSIFY_MIN_SCORE", "90"))
FUZZY_MATCH_MIN_SCORE = int(os.getenv("FUZZY_MATCH_MIN_SCORE", "88"))
CLASSIFY_CONFLICT_MARGIN = int(os.getenv("CLASSIFY_CONFLICT_MARGIN", "15"))
CLASSIFY_SCORE_DECAY = float(os.getenv("CLASSIFY_SCORE_DECAY", "0.5"))
CLASSIFY_BODY_FIELD_WEIGHT = float(os.getenv("CLASSIFY_BODY_FIELD_WEIGHT", "0.6"))
CLASSIFY_BODY_CHAR_LIMIT = int(os.getenv("CLASSIFY_BODY_CHAR_LIMIT", "4000"))
CLASSIFY_MIN_FUZZY_PATTERN_LENGTH = int(os.getenv("CLASSIFY_MIN_FUZZY_PATTERN_LENGTH", "5"))
MODULE_MAPPING_MIN_SCORE = int(os.getenv("MODULE_MAPPING_MIN_SCORE", "60"))
MODULE_MAPPING_TIE_MARGIN = int(os.getenv("MODULE_MAPPING_TIE_MARGIN", "10"))

# Swapped for an AIClassifier in Phase 2 -- one env var, no code change here.
CLASSIFICATION_PROVIDER = os.getenv(
    "CLASSIFICATION_PROVIDER",
    "apps.classification.services.rule_classifier.RuleClassifier",
)

# Priority/severity codes the confirm dialog pre-selects for an email-born bug.
# Deliberately NOT derived from the classification score: urgency and
# classification confidence are unrelated quantities.
MAIL_DEFAULT_PRIORITY_CODE = os.getenv("MAIL_DEFAULT_PRIORITY_CODE", "MEDIUM")
MAIL_DEFAULT_SEVERITY_CODE = os.getenv("MAIL_DEFAULT_SEVERITY_CODE", "MAJOR")

# ---- LOGGING ----
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
