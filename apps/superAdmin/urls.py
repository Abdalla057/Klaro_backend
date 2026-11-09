from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.abonnements.views_demandes import DemandeAbonnementAdminViewSet

from .views import (
    FournisseurSupervisionViewSet,
    SerieChiffreAffairesView,
    StatistiquesAbonnementsView,
    StatistiquesFinancieresView,
    TableauDeBordView,
)

router = DefaultRouter()
router.register(
    "fournisseurs",
    FournisseurSupervisionViewSet,
    basename="super-admin-fournisseur",
)
router.register(
    "demandes-abonnement",
    DemandeAbonnementAdminViewSet,
    basename="super-admin-demande-abonnement",
)

urlpatterns = [
    path(
        "tableau-de-bord/",
        TableauDeBordView.as_view(),
        name="super-admin-tableau-de-bord",
    ),
    path(
        "statistiques/financieres/",
        StatistiquesFinancieresView.as_view(),
        name="super-admin-statistiques-financieres",
    ),
    path(
        "statistiques/revenus/",
        SerieChiffreAffairesView.as_view(),
        name="super-admin-statistiques-revenus",
    ),
    path(
        "statistiques/abonnements/",
        StatistiquesAbonnementsView.as_view(),
        name="super-admin-statistiques-abonnements",
    ),
] + router.urls
