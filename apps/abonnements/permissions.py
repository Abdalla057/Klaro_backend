from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .exceptions import AbonnementExpire
from .services import abonnement_actif


def _fournisseur_de(utilisateur):
    fournisseur = getattr(utilisateur, "fournisseur_profile", None)
    if fournisseur is not None:
        return fournisseur

    client = getattr(utilisateur, "client_profile", None)
    if client is not None:
        return client.fournisseur

    agent = getattr(utilisateur, "agent_profile", None)
    if agent is not None:
        return agent.fournisseur

    return None


class AbonnementActifPourEcriture(BasePermission):
    """Autorise la lecture mais bloque les écritures sans abonnement actif.

    Le fournisseur concerné est résolu depuis le profil de l'utilisateur :
    fournisseur, client ou agent. Les super administrateurs (aucun profil
    métier) ne sont pas concernés.

    Les actions d'activation de compte et de renvoi de code utilisent leur
    propre permission ``AllowAny`` : elles restent accessibles même si
    l'abonnement du fournisseur est inactif, afin que le titulaire puisse
    consulter son espace. En revanche, aucune écriture métier (client, agent,
    encaissement) n'est autorisée.
    """

    message = "Votre abonnement doit être actif pour effectuer cette action."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True

        fournisseur = _fournisseur_de(request.user)
        if fournisseur is None:
            return True

        if fournisseur.est_suspendu:
            raise PermissionDenied(
                "L'activité de ce fournisseur est suspendue. "
                "Aucune écriture n'est autorisée."
            )

        if abonnement_actif(fournisseur) is None:
            raise AbonnementExpire(
                {
                    "detail": (
                        "Votre abonnement est expiré ou suspendu. "
                        "Renouvelez-le pour effectuer cette action."
                    ),
                    "code": "abonnement_inactif",
                }
            )
        return True
