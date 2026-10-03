from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.bugs.views import (
    AttachmentDeleteView,
    AttachmentDownloadView,
    BugAttachmentView,
    BugViewSet,
)

router = DefaultRouter()
router.register(r"bugs", BugViewSet, basename="bug")

urlpatterns = [
    path("bugs/<uuid:unique_id>/attachments/", BugAttachmentView.as_view(),
         name="bug-attachments"),
    path("bugs/attachments/<uuid:unique_id>/download/", AttachmentDownloadView.as_view(),
         name="attachment-download"),
    path("bugs/attachments/<uuid:unique_id>/", AttachmentDeleteView.as_view(),
         name="attachment-delete"),
    *router.urls,
]
