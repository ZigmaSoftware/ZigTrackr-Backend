"""Developer-owned work details and the completeness gate for starting work."""

from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditAction
from apps.tickets.constants import TicketType
from apps.tickets.models import SupportTicket
from apps.tickets.services.policy import can_edit_details
from common.exceptions.domain import WorkflowValidationError
from common.services.audit import record_field_changes

DETAIL_FIELDS = (
    "company", "project", "module", "submodule", "priority", "severity",
    "department", "site", "environment", "expected_closure_date", "title", "description",
)
BUG_FIELDS = DETAIL_FIELDS


def current_details(ticket):
    bug = ticket.bug if ticket.bug_id else None
    values = {field: getattr(ticket, field, None) for field in DETAIL_FIELDS}
    if bug:
        for field in BUG_FIELDS:
            if values[field] in (None, ""):
                values[field] = getattr(bug, field, None)
    if values["company"] is None and values["project"]:
        values["company"] = values["project"].company
    return values


def validate_details(values, *, ticket_type, complete=False):
    errors = {}
    if complete:
        required = ["company"]
        if ticket_type == TicketType.BUG:
            required += ["project", "priority", "severity"]
        for field in required:
            if not values.get(field):
                errors[field] = ["Select this detail before starting work."]
    company, project, module, submodule = (values.get(field) for field in ("company", "project", "module", "submodule"))
    for field in ("company", "project", "module", "submodule", "priority", "severity", "department", "site"):
        value = values.get(field)
        if value and (value.is_deleted or not value.is_active):
            errors[field] = ["Select an active master record."]
    if project and (not company or project.company_id != company.pk):
        errors["project"] = ["Select a project belonging to this company."]
    if module and (not project or module.project_id != project.pk):
        errors["module"] = ["Select a module belonging to this project."]
    if submodule and (not module or submodule.module_id != module.pk):
        errors["submodule"] = ["Select a submodule belonging to this module."]
    for field in ("department", "site", "priority", "severity"):
        value = values.get(field)
        if value and (not company or value.company_id != company.pk):
            errors[field] = ["Select a record belonging to this company."]
    if errors:
        raise WorkflowValidationError(errors)


def ensure_work_details(ticket):
    validate_details(current_details(ticket), ticket_type=ticket.ticket_type, complete=True)


@transaction.atomic
def save_work_details(*, ticket, actor, details, request=None, complete=False):
    locked = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
    if not can_edit_details(locked, actor):
        raise PermissionDenied("Only the assigned developer can edit details before rectification.")
    values = {**current_details(locked), **details}
    if not values["company"] or values["company"].pk != locked.company_id:
        raise WorkflowValidationError({"company": ["A ticket cannot be moved to another company."]})
    validate_details(values, ticket_type=locked.ticket_type, complete=complete)
    changes = {}
    for field in DETAIL_FIELDS:
        before, after = getattr(locked, field), values[field]
        if before != after:
            changes[field] = (before, after)
            setattr(locked, field, after)
    if changes:
        locked.updated_by = actor.unique_id
        locked.save(update_fields=[*changes, "updated_by", "updated_at"])
        record_field_changes(entity=locked, actor=actor, changes=changes,
                             action=AuditAction.TICKET_DETAILS_UPDATED, request=request)
    if locked.bug_id:
        from apps.bugs.services.bug_service import update_bug

        update_bug(bug=locked.bug, actor=actor, request=request,
                   **{field: values[field] for field in BUG_FIELDS})
    return locked
