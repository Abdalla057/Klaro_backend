from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    DemandeDecaissementViewSet,
    SoldeClientView,
    StatistiquesFinancieresView,
    TransactionFinanciereViewSet,
)

router = DefaultRouter()
router.register(
    "transactions",
    TransactionFinanciereViewSet,
    basename="transaction-financiere",
)
router.register(
    "decaissements",
    DemandeDecaissementViewSet,
    basename="demande-decaissement",
)

urlpatterns = [
    path("solde/", SoldeClientView.as_view(), name="solde-client"),
    path(
        "statistiques/",
        StatistiquesFinancieresView.as_view(),
        name="statistiques-financieres",
    ),
    path("", include(router.urls)),
]
