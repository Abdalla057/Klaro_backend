from django.db.models import ProtectedError, RestrictedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def api_exception_handler(exc, context):
    """Convertit les erreurs d'intégrité Django en réponses HTTP explicites."""
    if isinstance(exc, (ProtectedError, RestrictedError)):
        return Response(
            {
                "detail": (
                    "Suppression impossible : des données liées existent "
                    "encore (ventes, mouvements de stock, commandes…)."
                ),
                "code": "protected",
            },
            status=status.HTTP_409_CONFLICT,
        )
    return drf_exception_handler(exc, context)
