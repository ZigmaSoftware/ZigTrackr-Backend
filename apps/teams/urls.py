from django.urls import path

from apps.teams.views import AssignmentBoardView, DeveloperWorkloadView

urlpatterns = [
    path("workload/", DeveloperWorkloadView.as_view(), name="team-workload"),
    path("assignment-board/", AssignmentBoardView.as_view(), name="team-assignment-board"),
]
