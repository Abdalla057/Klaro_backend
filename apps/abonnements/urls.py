from rest_framework.routers import DefaultRouter

from .views import (
    AbonnementViewSet,
    PaiementAbonnementViewSet,
    PlanAbonnementViewSet,
)
from .views_demandes import DemandeAbonnementFournisseurViewSet

router = DefaultRouter()
router.register("plans", PlanAbonnementViewSet, basename="plan-abonnement")
router.register("abonnements", AbonnementViewSet, basename="abonnement")
router.register(
    "paiements",
    PaiementAbonnementViewSet,
    basename="paiement-abonnement",
)
router.register(
    "demandes",
    DemandeAbonnementFournisseurViewSet,
    basename="demande-abonnement",
)

urlpatterns = router.urls
