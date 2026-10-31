from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import FournisseurSupervisionViewSet, TableauDeBordView

router = DefaultRouter()
router.register(
    "fournisseurs",
    FournisseurSupervisionViewSet,
    basename="super-admin-fournisseur",
)

urlpatterns = [
    path(
        "tableau-de-bord/",
        TableauDeBordView.as_view(),
        name="super-admin-tableau-de-bord",
    ),
] + router.urls
