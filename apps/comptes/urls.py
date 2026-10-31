from django.urls import path

from .views import (
    ActiverCompteView,
    ChangementMotDePasseView,
    ConnexionView,
    DeconnexionView,
    DefinirMotDePasseActivationView,
    MeView,
    RafraichirView,
    StatutActivationView,
    VerifierCodeActivationView,
)

urlpatterns = [
    path("login/", ConnexionView.as_view(), name="login"),
    path("refresh/", RafraichirView.as_view(), name="refresh"),
    path("logout/", DeconnexionView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path(
        "mot-de-passe/",
        ChangementMotDePasseView.as_view(),
        name="changer-mot-de-passe",
    ),
    path(
        "activation/verifier-code/",
        VerifierCodeActivationView.as_view(),
        name="activation-verifier-code",
    ),
    path(
        "activation/definir-mot-de-passe/",
        DefinirMotDePasseActivationView.as_view(),
        name="activation-definir-mot-de-passe",
    ),
    path(
        "activation/activer/",
        ActiverCompteView.as_view(),
        name="activation-activer",
    ),
    path(
        "activation/statut/",
        StatutActivationView.as_view(),
        name="activation-statut",
    ),
]
