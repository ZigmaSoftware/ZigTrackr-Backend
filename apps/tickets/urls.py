from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.tickets.views import (
    PublicTicketLookupView,
    PublicTrackActivityView,
    PublicTrackChatView,
    PublicTrackChatActionView,
    PublicTrackChatReceiptView,
    PublicTrackReopenView,
    PublicTrackTicketView,
    PublicTrackVerifyView,
    SupportTicketViewSet,
    TicketAttachmentDeleteView,
    TicketAttachmentDownloadView,
    TicketAttachmentView,
    TicketMailAttachmentDownloadView,
)

router = DefaultRouter()
router.register("", SupportTicketViewSet, basename="ticket")

# Listed before the router: it is registered at "", so its detail route would
# otherwise match "attachments/<uuid>/" and treat "attachments" as a ticket id.
urlpatterns = [
    # Unauthenticated, rate limited. Before the router for the same reason as
    # the attachment paths.
    path("public/lookup/", PublicTicketLookupView.as_view(), name="ticket-public-lookup"),
    path("public/track/verify/", PublicTrackVerifyView.as_view(), name="ticket-public-verify"),
    path("public/track/ticket/", PublicTrackTicketView.as_view(), name="ticket-public-detail"),
    path("public/track/activity/", PublicTrackActivityView.as_view(), name="ticket-public-activity"),
    path("public/track/chat/messages/", PublicTrackChatView.as_view(), name="ticket-public-chat"),
    path("public/track/chat/receipts/", PublicTrackChatReceiptView.as_view(), name="ticket-public-chat-receipts"),
    path("public/track/chat/messages/<uuid:message_id>/", PublicTrackChatActionView.as_view(), name="ticket-public-chat-action"),
    path("public/track/reopen/", PublicTrackReopenView.as_view(), name="ticket-public-reopen"),
    path("<uuid:unique_id>/attachments/", TicketAttachmentView.as_view(),
         name="ticket-attachments"),
    path("attachments/<uuid:unique_id>/download/", TicketAttachmentDownloadView.as_view(),
         name="ticket-attachment-download"),
    path("attachments/<uuid:unique_id>/", TicketAttachmentDeleteView.as_view(),
         name="ticket-attachment-delete"),
    path("mail-attachments/<uuid:unique_id>/download/",
         TicketMailAttachmentDownloadView.as_view(), name="ticket-mail-attachment-download"),
] + router.urls
