from django.urls import path

from .views_demandes import WebhookPaiementView

urlpatterns = [
    path(
        "webhooks/<str:prestataire>/",
        WebhookPaiementView.as_view(),
        name="webhook-paiement",
    ),
]
