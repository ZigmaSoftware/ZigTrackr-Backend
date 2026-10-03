"""Root URL configuration.

MEDIA_ROOT is deliberately not served here, even in DEBUG. Static serving of
uploads bypasses Django entirely and therefore bypasses every permission check;
attachments are reachable only through the permission-gated download view
(spec 31).
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

API_V1 = "api/v1/"

urlpatterns = [
    path("django-admin/", admin.site.urls),

    # ---- API v1 ----
    path(f"{API_V1}auth/", include("apps.authentication.urls")),

    # Prefix-scoped routes must precede the routers mounted at the bare API
    # root. The masters router registers `teams/<unique_id>/`, which would
    # otherwise match `teams/workload/` and try to look up "workload" as a UUID.
    path(f"{API_V1}dashboard/", include("apps.dashboard.urls")),
    path(f"{API_V1}reports/", include("apps.reports.urls")),
    path(f"{API_V1}teams/", include("apps.teams.urls")),
    path(f"{API_V1}audit/", include("apps.audit.urls")),
    path(f"{API_V1}notifications/", include("apps.notifications.urls")),
    path(f"{API_V1}tickets/", include("apps.tickets.urls")),
    path(f"{API_V1}mail-intake/", include("apps.mail_intake.urls")),

    path(f"{API_V1}", include("apps.accounts.urls")),
    path(f"{API_V1}", include("apps.masters.urls")),
    path(f"{API_V1}", include("apps.bugs.urls")),
    # ---- SCHEMA / DOCS ----
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
