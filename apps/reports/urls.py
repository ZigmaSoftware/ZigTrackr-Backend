from django.urls import path

from apps.reports.views import (
    AgingReportView,
    BugExportView,
    ClosureReportView,
    DailyBugReportView,
    EmployeeReportView,
    ModuleReportView,
    OverdueReportView,
    PriorityReportView,
    ProjectReportView,
    RootCauseReportView,
)

urlpatterns = [
    path("daily/", DailyBugReportView.as_view(), name="report-daily"),
    path("employee/", EmployeeReportView.as_view(), name="report-employee"),
    path("project/", ProjectReportView.as_view(), name="report-project"),
    path("module/", ModuleReportView.as_view(), name="report-module"),
    path("priority/", PriorityReportView.as_view(), name="report-priority"),
    path("aging/", AgingReportView.as_view(), name="report-aging"),
    path("overdue/", OverdueReportView.as_view(), name="report-overdue"),
    path("closure/", ClosureReportView.as_view(), name="report-closure"),
    path("root-cause/", RootCauseReportView.as_view(), name="report-root-cause"),
    path("export/bugs/", BugExportView.as_view(), name="report-export-bugs"),
]
