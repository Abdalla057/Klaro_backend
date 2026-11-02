from rest_framework.routers import DefaultRouter

from .views import (
    AbonnementViewSet,
    PaiementAbonnementViewSet,
    PlanAbonnementViewSet,
)

router = DefaultRouter()
router.register("plans", PlanAbonnementViewSet, basename="plan-abonnement")
router.register("abonnements", AbonnementViewSet, basename="abonnement")
router.register(
    "paiements",
    PaiementAbonnementViewSet,
    basename="paiement-abonnement",
)

urlpatterns = router.urls
