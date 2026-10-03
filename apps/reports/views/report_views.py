"""Report endpoints (spec 33-36, 54)."""

import datetime

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.bugs.filters import BugFilter
from apps.bugs.selectors import base_bug_queryset
from apps.reports.selectors import (
    aging_report,
    closure_report,
    daily_bug_report,
    employee_report,
    module_report,
    overdue_report,
    priority_report,
    project_report,
    root_cause_report,
    submodule_report,
)
from common.permissions.require import RequirePermission
from common.permissions.scoping import scope_bug_queryset
from common.responses import ok
from common.utils.excel import build_workbook, workbook_response


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


class ReportView(APIView):
    """Base for the report endpoints. Each subclass supplies build()."""

    permission_classes = [IsAuthenticated, RequirePermission("reports.report.view")]
    report_name = "report"

    def build(self, request):
        raise NotImplementedError

    def get(self, request):
        return ok(self.build(request), message="Report generated.")


class DailyBugReportView(ReportView):
    report_name = "daily-bug-report"

    def build(self, request):
        return daily_bug_report(request.user, _parse_date(request.query_params.get("date")))


class EmployeeReportView(ReportView):
    report_name = "employee-report"

    def build(self, request):
        return employee_report(request.user)


class ProjectReportView(ReportView):
    report_name = "project-report"

    def build(self, request):
        return project_report(request.user)


class ModuleReportView(ReportView):
    report_name = "module-report"

    def build(self, request):
        module = request.query_params.get("module")
        if module:
            return submodule_report(request.user, module)
        return module_report(request.user, project=request.query_params.get("project"))


class PriorityReportView(ReportView):
    report_name = "priority-report"

    def build(self, request):
        return priority_report(request.user)


class AgingReportView(ReportView):
    report_name = "aging-report"

    def build(self, request):
        return aging_report(request.user)


class OverdueReportView(ReportView):
    report_name = "overdue-report"

    def build(self, request):
        return overdue_report(request.user)


class ClosureReportView(ReportView):
    report_name = "closure-report"

    def build(self, request):
        return closure_report(
            request.user,
            date_from=_parse_date(request.query_params.get("date_from")),
            date_to=_parse_date(request.query_params.get("date_to")),
        )


class RootCauseReportView(ReportView):
    report_name = "root-cause-analysis"

    def build(self, request):
        return root_cause_report(
            request.user,
            project=request.query_params.get("project"),
            module=request.query_params.get("module"),
            owner=request.query_params.get("owner"),
            priority=request.query_params.get("priority"),
            severity=request.query_params.get("severity"),
            date_from=_parse_date(request.query_params.get("date_from")),
            date_to=_parse_date(request.query_params.get("date_to")),
        )


class BugExportView(APIView):
    """Excel export of the bug list, honouring the active filters (spec 54)."""

    permission_classes = [IsAuthenticated, RequirePermission("reports.report.export")]

    COLUMNS = [
        ("Bug No", lambda b: b.bug_no),
        ("Reported Date", lambda b: b.reported_date),
        ("Project", lambda b: b.project.name if b.project_id else ""),
        ("Module", lambda b: b.module.name if b.module_id else ""),
        ("Submodule", lambda b: b.submodule.name if b.submodule_id else ""),
        ("Title", lambda b: b.title),
        ("Priority", lambda b: b.priority.name if b.priority_id else ""),
        ("Severity", lambda b: b.severity.name if b.severity_id else ""),
        ("Status", lambda b: b.get_status_display()),
        ("Owner", lambda b: b.owner.display_name if b.owner_id else ""),
        ("Reported By", lambda b: b.reported_by.display_name if b.reported_by_id else ""),
        ("Age (days)", lambda b: b.age_days),
        ("Expected Closure", lambda b: b.expected_closure_date),
        ("Overdue Days", lambda b: b.overdue_days),
        ("Last Update", lambda b: b.latest_update_date),
        ("Root Cause Type", lambda b: b.root_cause_type.name if b.root_cause_type_id else ""),
        ("Root Cause", lambda b: b.root_cause),
        ("Resolution", lambda b: b.resolution),
        ("Closed Date", lambda b: b.closed_date),
        ("Closed By", lambda b: b.closed_by.display_name if b.closed_by_id else ""),
    ]

    def get(self, request):
        qs = scope_bug_queryset(base_bug_queryset(), request.user)
        # Apply the same filters as the list screen so an export matches what
        # the user is looking at (spec 54).
        qs = BugFilter(request.query_params, queryset=qs).qs

        headers = [label for label, _ in self.COLUMNS]
        rows = [[accessor(bug) for _, accessor in self.COLUMNS] for bug in qs[:10000]]
        workbook = build_workbook([("Bugs", headers, rows)])
        return workbook_response(workbook, "bug-list")
