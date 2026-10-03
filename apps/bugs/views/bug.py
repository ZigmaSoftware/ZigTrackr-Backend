"""Bug endpoints: CRUD plus the controlled workflow actions (spec 42)."""

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.bugs.constants import BugStatus
from apps.bugs.models import Bug, BugUpdate
from apps.bugs.selectors import base_bug_queryset
from apps.bugs.serializers import (
    AssignSerializer,
    EmailBugAssignmentSerializer,
    BugAssignmentHistorySerializer,
    BugAttachmentSerializer,
    BugDetailSerializer,
    BugListSerializer,
    BugReopenHistorySerializer,
    BugStatusHistorySerializer,
    BugTestingHistorySerializer,
    BugUpdateSerializer,
    BugUpdateWriteSerializer,
    BugWriteSerializer,
    CloseSerializer,
    ReopenSerializer,
    ResolveSerializer,
    StatusChangeSerializer,
    TestingSerializer,
)
from apps.bugs.services import (
    add_update,
    assign_bug,
    change_status,
    close_bug,
    create_bug,
    record_test,
    reopen_bug,
    resolve_bug,
    update_bug,
)
from common.permissions.require import RequirePermission, has_permission
from common.permissions.scoping import can_mutate_bug, scope_bug_queryset
from common.responses import EnvelopeMessageMixin, created, ok
from common.viewsets import PermissionByActionMixin

# Targets of the generic /status/ action that have a dedicated, more
# restrictive action endpoint (resolve/close/reopen) and the codename that
# endpoint requires. Spec 14 reserves Close to Team Lead/Admin, for example,
# while ordinary in-workflow moves only need bugs.bug.change_status -- this
# stops the generic endpoint from becoming a side door around that.
STATUS_REQUIRES_CODENAME = {
    BugStatus.RESOLVED: "bugs.bug.resolve",
    BugStatus.CLOSED: "bugs.bug.close",
    BugStatus.REOPENED: "bugs.bug.reopen",
}


class BugViewSet(PermissionByActionMixin, EnvelopeMessageMixin, viewsets.ModelViewSet):
    """One bug domain. Every filtered view (My Bugs, Overdue, Critical,
    Testing, Closed...) is this endpoint with query parameters -- spec 18
    forbids duplicating the workflow per screen.
    """

    lookup_field = "unique_id"
    lookup_url_kwarg = "unique_id"
    permission_classes = [IsAuthenticated]
    filterset_class = None  # set in __init__ to avoid an import cycle at load
    search_fields = []
    ordering_fields = [
        "bug_no", "reported_date", "title", "status", "expected_closure_date",
        "latest_update_at", "age_days", "overdue_days", "days_since_update",
        "priority__rank", "severity__rank", "created_at",
    ]
    ordering = ["-id"]

    permission_map = {
        "list": [RequirePermission("bugs.bug.view")],
        "retrieve": [RequirePermission("bugs.bug.view")],
        "create": [RequirePermission("bugs.bug.add")],
        "update": [RequirePermission("bugs.bug.edit")],
        "partial_update": [RequirePermission("bugs.bug.edit")],
        "assign": [RequirePermission("bugs.bug.assign")],
        "complete_assignment": [RequirePermission("bugs.bug.assign")],
        "change_status": [RequirePermission("bugs.bug.change_status")],
        "updates": [RequirePermission("bugs.update.view", "bugs.update.add")],
        "testing": [RequirePermission("bugs.bug.test")],
        "resolve": [RequirePermission("bugs.bug.resolve")],
        "close": [RequirePermission("bugs.bug.close")],
        "reopen": [RequirePermission("bugs.bug.reopen")],
        # timeline/history/destroy are read paths already fully row-scoped by
        # get_queryset() (destroy always 405s regardless), but PermissionByActionMixin
        # falls through to bare IsAuthenticated for any action missing here --
        # a fail-open default. Explicit entries mean a future action can never
        # silently inherit that default by omission.
        "timeline": [RequirePermission("bugs.bug.view")],
        "history": [RequirePermission("bugs.bug.view")],
        "destroy": [RequirePermission("bugs.bug.delete")],
    }

    envelope_messages = {
        "create": "Bug created successfully.",
        "update": "Bug updated successfully.",
        "partial_update": "Bug updated successfully.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.bugs.filters import BugFilter

        self.filterset_class = BugFilter

    # ---- QUERYSET ----

    def get_queryset(self):
        """Always scoped. This is the single row-level security gate (spec 14)."""
        qs = base_bug_queryset()
        if self.action == "retrieve":
            qs = qs.prefetch_related(
                Prefetch("updates", queryset=BugUpdate.objects.select_related(
                    "updated_by", "owner").order_by("-created_at")),
                "attachments__uploaded_by",
                "status_history__changed_by",
                "assignment_history__from_owner",
                "assignment_history__to_owner",
                "testing_history__tested_by",
                "reopen_history__reopened_by",
            ).select_related("support_ticket")
        return scope_bug_queryset(qs, self.request.user)

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return BugWriteSerializer
        if self.action == "retrieve":
            return BugDetailSerializer
        return BugListSerializer

    def _annotated(self, bug):
        """Re-read through the annotated queryset.

        create/assign/status and the other actions mutate a plain model
        instance, which has no age_days/is_overdue. Returning the annotated row
        keeps every bug payload the same shape, so the frontend never has to
        special-case a response.
        """
        return base_bug_queryset().get(pk=bug.pk)

    def _get_bug_for_write(self):
        """Fetch the bug and confirm the caller may act on this particular row."""
        bug = self.get_object()
        if not can_mutate_bug(self.request.user, bug):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You do not have permission to modify this bug.")
        return bug

    # ---- CRUD ----

    def create(self, request, *args, **kwargs):
        serializer = BugWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bug = create_bug(actor=request.user, request=request, **serializer.validated_data)
        return created(BugDetailSerializer(self._annotated(bug)).data,
                       message="Bug created successfully.")

    def update(self, request, *args, **kwargs):
        bug = self._get_bug_for_write()
        serializer = BugWriteSerializer(bug, data=request.data, partial=kwargs.pop("partial", False))
        serializer.is_valid(raise_exception=True)
        bug = update_bug(bug=bug, actor=request.user, request=request,
                         **serializer.validated_data)
        return ok(BugDetailSerializer(self._annotated(bug)).data, message="Bug updated successfully.")

    def destroy(self, request, *args, **kwargs):
        """Bugs are not deleted (spec 57); use Rejected or soft delete via admin."""
        from rest_framework.exceptions import MethodNotAllowed

        raise MethodNotAllowed(
            "DELETE",
            detail="Bugs cannot be deleted. Reject the bug or change its status instead.",
        )

    # ---- WORKFLOW ACTIONS (spec 42) ----

    @action(detail=True, methods=["post"])
    def assign(self, request, unique_id=None):
        bug = self._get_bug_for_write()
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bug = assign_bug(
            bug=bug, new_owner=serializer.validated_data["owner"], actor=request.user,
            remarks=serializer.validated_data.get("remarks", ""),
            expected_closure_date=serializer.validated_data.get("expected_closure_date"),
            request=request,
        )
        return ok(BugDetailSerializer(self._annotated(bug)).data, message="Bug assigned successfully.")

    @action(detail=True, methods=["post"], url_path="complete-assignment")
    def complete_assignment(self, request, unique_id=None):
        """Fill routing fields and assign an email-created bug in one action."""
        bug = self._get_bug_for_write()
        serializer = EmailBugAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        owner = data.pop("owner")
        remarks = data.pop("remarks", "")
        expected_closure_date = data.pop("expected_closure_date", None)
        bug = update_bug(bug=bug, actor=request.user, request=request, **data)
        bug = assign_bug(
            bug=bug, new_owner=owner, actor=request.user,
            remarks=remarks, expected_closure_date=expected_closure_date,
            request=request,
        )
        return ok(BugDetailSerializer(self._annotated(bug)).data, message="Bug assigned successfully.")

    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, unique_id=None):
        bug = self._get_bug_for_write()
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = data["status"]

        # RESOLVED, CLOSED and REOPENED each have a dedicated action endpoint
        # (resolve/close/reopen) gated on a codename narrower than the plain
        # bugs.bug.change_status this generic endpoint requires -- spec 14
        # reserves Close to Team Lead/Admin, for instance, while Developer
        # holds change_status for the ordinary in-workflow moves. Today those
        # dedicated endpoints are also the only place the required closure/
        # resolution/reopen fields can be supplied, so this generic path is
        # not reachable in practice -- but that safety is an accident of
        # which fields a serializer happens to expose, not a rule. Checking
        # the stronger codename here directly, rather than relying on that
        # accident, is what keeps it a rule if the serializers ever change.
        extra_gate = STATUS_REQUIRES_CODENAME.get(target)
        if extra_gate and not has_permission(request.user, extra_gate):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                f"You do not have permission to move this bug to {BugStatus(target).label}."
            )

        bug = change_status(
            bug=bug, to_status=target, actor=request.user,
            remarks=data.get("remarks", ""), payload=data, request=request,
        )
        return ok(BugDetailSerializer(self._annotated(bug)).data, message="Status updated successfully.")

    @action(detail=True, methods=["get", "post"])
    def updates(self, request, unique_id=None):
        bug = self.get_object()
        if request.method == "GET":
            qs = BugUpdate.objects.filter(bug=bug).select_related("updated_by", "owner")
            page = self.paginate_queryset(qs)
            serializer = BugUpdateSerializer(page if page is not None else qs, many=True)
            if page is not None:
                return self.get_paginated_response(serializer.data)
            return ok(serializer.data, message="Updates retrieved.")

        # The permission_map entry for this action is ANY-of
        # (bugs.update.view OR bugs.update.add), because the GET branch above
        # only needs .view. That means a caller who holds .view alone (e.g.
        # Management) reaches this line -- so POST re-checks the codename that
        # actually matters for writing, on top of the row-level can_mutate_bug
        # check. Belt and suspenders: either check alone would have caught the
        # bug this fixes (Management posting daily updates), but a single
        # shared gate for two different HTTP methods is exactly the kind of
        # mistake that recurs, so both layers check independently.
        if not has_permission(request.user, "bugs.update.add"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You do not have permission to add updates to this bug.")

        if not can_mutate_bug(request.user, bug):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You do not have permission to update this bug.")

        serializer = BugUpdateWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        update = add_update(bug=bug, actor=request.user, request=request,
                            **serializer.validated_data)
        return created(BugUpdateSerializer(update).data, message="Daily update added.")

    @action(detail=True, methods=["post"])
    def testing(self, request, unique_id=None):
        bug = self._get_bug_for_write()
        serializer = TestingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bug = record_test(bug=bug, actor=request.user, request=request,
                          **serializer.validated_data)
        return ok(BugDetailSerializer(self._annotated(bug)).data, message="Test result recorded.")

    @action(detail=True, methods=["post"])
    def resolve(self, request, unique_id=None):
        bug = self._get_bug_for_write()
        serializer = ResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bug = resolve_bug(bug=bug, actor=request.user, request=request,
                          **serializer.validated_data)
        return ok(BugDetailSerializer(self._annotated(bug)).data, message="Bug resolved successfully.")

    @action(detail=True, methods=["post"])
    def close(self, request, unique_id=None):
        bug = self._get_bug_for_write()
        serializer = CloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from apps.tickets.models import SupportTicket
        from apps.tickets.services.outbound_mail import queue_closed_notice

        with transaction.atomic():
            bug = close_bug(bug=bug, actor=request.user, request=request,
                            **serializer.validated_data)
            ticket = SupportTicket.objects.filter(bug=bug, is_deleted=False).first()
            if ticket:
                ticket.status = "CLOSED"
                ticket.save(update_fields=["status", "updated_at"])
                queue_closed_notice(ticket=ticket,
                                    remarks=serializer.validated_data["closure_remarks"])
        return ok(BugDetailSerializer(self._annotated(bug)).data, message="Bug closed successfully.")

    @action(detail=True, methods=["post"])
    def reopen(self, request, unique_id=None):
        bug = self._get_bug_for_write()
        serializer = ReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bug = reopen_bug(bug=bug, actor=request.user, request=request,
                         **serializer.validated_data)
        return ok(BugDetailSerializer(self._annotated(bug)).data, message="Bug reopened successfully.")

    # ---- HISTORY (spec 27, 28) ----

    @action(detail=True, methods=["get"])
    def timeline(self, request, unique_id=None):
        from apps.bugs.services.timeline_service import build_timeline

        bug = self.get_object()
        return ok(build_timeline(bug), message="Timeline retrieved.")

    @action(detail=True, methods=["get"])
    def history(self, request, unique_id=None):
        bug = self.get_object()
        return ok({
            "status": BugStatusHistorySerializer(
                bug.status_history.all().select_related("changed_by"), many=True).data,
            "assignment": BugAssignmentHistorySerializer(
                bug.assignment_history.all().select_related(
                    "from_owner", "to_owner", "assigned_by"), many=True).data,
            "testing": BugTestingHistorySerializer(
                bug.testing_history.all().select_related("tested_by"), many=True).data,
            "reopen": BugReopenHistorySerializer(
                bug.reopen_history.all().select_related("reopened_by"), many=True).data,
        }, message="History retrieved.")
