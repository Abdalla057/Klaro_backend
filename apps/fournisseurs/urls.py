from rest_framework.routers import DefaultRouter

from .views import FournisseurViewSet

router = DefaultRouter()
router.register("", FournisseurViewSet, basename="fournisseur")

urlpatterns = router.urls