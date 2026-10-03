from django.urls import path

from apps.dashboard.views import (
    ChartsView,
    KpiView,
    MyWorkView,
    PriorityAttentionView,
    RecentActivityView,
    SidebarCountsView,
    TeamOverviewView,
)

urlpatterns = [
    path("kpis/", KpiView.as_view(), name="dashboard-kpis"),
    path("charts/", ChartsView.as_view(), name="dashboard-charts"),
    path("my-work/", MyWorkView.as_view(), name="dashboard-my-work"),
    path("priority-attention/", PriorityAttentionView.as_view(), name="dashboard-attention"),
    path("sidebar-counts/", SidebarCountsView.as_view(), name="dashboard-sidebar-counts"),
    path("team-overview/", TeamOverviewView.as_view(), name="dashboard-team-overview"),
    path("recent-activity/", RecentActivityView.as_view(), name="dashboard-recent-activity"),
]
