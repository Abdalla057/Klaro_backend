from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CategorieProduitViewSet,
    CommandeAchatViewSet,
    ConfigurationNotificationView,
    DetteCommercialeViewSet,
    FactureParLienView,
    FactureViewSet,
    FournisseurMarchandiseViewSet,
    MouvementStockViewSet,
    NotificationClientViewSet,
    NotificationExterneViewSet,
    ProduitViewSet,
    ReceptionViewSet,
    StatistiquesCommerceAdminView,
    StatistiquesCommerceView,
    VenteViewSet,
)

router = DefaultRouter()
router.register("categories", CategorieProduitViewSet, basename="categorie")
router.register("produits", ProduitViewSet, basename="produit")
router.register(
    "fournisseurs-marchandises",
    FournisseurMarchandiseViewSet,
    basename="fournisseur-marchandise",
)
router.register(
    "commandes-achat",
    CommandeAchatViewSet,
    basename="commande-achat",
)
router.register("receptions", ReceptionViewSet, basename="reception")
router.register(
    "mouvements-stock",
    MouvementStockViewSet,
    basename="mouvement-stock",
)
router.register("ventes", VenteViewSet, basename="vente")
router.register("factures", FactureViewSet, basename="facture")
router.register(
    "notifications",
    NotificationClientViewSet,
    basename="notification",
)
router.register(
    "notifications-externes",
    NotificationExterneViewSet,
    basename="notification-externe",
)
router.register("dettes", DetteCommercialeViewSet, basename="dette")

urlpatterns = [
    path(
        "factures/lien/<str:token>/pdf/",
        FactureParLienView.as_view(),
        name="facture-lien-pdf",
    ),
    path(
        "statistiques/",
        StatistiquesCommerceView.as_view(),
        name="statistiques-commerce",
    ),
    path(
        "admin/statistiques/",
        StatistiquesCommerceAdminView.as_view(),
        name="admin-statistiques-commerce",
    ),
    path(
        "configuration-notifications/",
        ConfigurationNotificationView.as_view(),
        name="configuration-notifications",
    ),
    path("", include(router.urls)),
]
