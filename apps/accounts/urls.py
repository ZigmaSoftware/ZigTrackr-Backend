from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.accounts.views import PermissionMatrixView, PermissionViewSet, RoleViewSet, UserViewSet

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"roles", RoleViewSet, basename="role")
router.register(r"permissions", PermissionViewSet, basename="permission")

urlpatterns = [
    path("permissions/matrix/", PermissionMatrixView.as_view(), name="permission-matrix"),
    *router.urls,
]
