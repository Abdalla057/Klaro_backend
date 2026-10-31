from rest_framework.routers import DefaultRouter

from .views import EncaissementViewSet

router = DefaultRouter()
router.register("", EncaissementViewSet, basename="encaissement")

urlpatterns = router.urls