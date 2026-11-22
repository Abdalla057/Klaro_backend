from django.urls import path

from .views_demandes import WebhookPaiementView

urlpatterns = [
    path(
        "webhooks/<str:prestataire>/<str:pays>/",
        WebhookPaiementView.as_view(),
        name="webhook-paiement",
    ),
]
