from rest_framework.exceptions import APIException


class AbonnementExpire(APIException):
    """Le fournisseur doit renouveler son abonnement pour continuer."""

    status_code = 402
    default_detail = "Votre abonnement est inactif. Renouvelez-le pour continuer."
    default_code = "abonnement_inactif"


class QuotaDepasse(APIException):
    """La limite du plan est atteinte pour la ressource demandée."""

    status_code = 409
    default_detail = "Le quota de votre abonnement est atteint."
    default_code = "quota_depasse"


class PlanUtilise(APIException):
    """Le plan ne peut pas être supprimé car des abonnements y font référence."""

    status_code = 409
    default_detail = {
        "detail": (
            "Ce plan est utilisé par des abonnements. "
            "Désactivez-le au lieu de le supprimer."
        ),
        "code": "plan_utilise",
    }
    default_code = "plan_utilise"


class InscriptionIndisponible(APIException):
    """Aucun plan d'essai n'est configuré : les inscriptions sont fermées."""

    status_code = 503
    default_detail = (
        "Les inscriptions sont temporairement indisponibles. "
        "Veuillez contacter l'administrateur."
    )
    default_code = "inscription_indisponible"
