from rest_framework.routers import DefaultRouter

from apps.mail_intake.views import MailIntakeViewSet

router = DefaultRouter()
router.register("", MailIntakeViewSet, basename="mail-intake")

urlpatterns = router.urls
