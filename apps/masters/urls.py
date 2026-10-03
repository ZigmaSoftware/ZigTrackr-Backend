from rest_framework.routers import DefaultRouter

from apps.masters.views import (
    DepartmentViewSet,
    ModuleViewSet,
    PriorityViewSet,
    ProjectViewSet,
    RootCauseTypeViewSet,
    SeverityViewSet,
    SiteViewSet,
    SubmoduleViewSet,
    TeamViewSet,
)

router = DefaultRouter()
router.register(r"projects", ProjectViewSet, basename="project")
router.register(r"modules", ModuleViewSet, basename="module")
router.register(r"submodules", SubmoduleViewSet, basename="submodule")
router.register(r"priorities", PriorityViewSet, basename="priority")
router.register(r"severities", SeverityViewSet, basename="severity")
router.register(r"root-cause-types", RootCauseTypeViewSet, basename="root-cause-type")
router.register(r"departments", DepartmentViewSet, basename="department")
router.register(r"teams", TeamViewSet, basename="team")
router.register(r"sites", SiteViewSet, basename="site")

urlpatterns = router.urls
