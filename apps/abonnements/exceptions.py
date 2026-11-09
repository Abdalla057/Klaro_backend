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


class PaiementEnLigneDesactive(APIException):
    """Le paiement en ligne est volontairement désactivé par configuration."""

    status_code = 503
    default_detail = (
        "Le paiement en ligne est temporairement indisponible. "
        "Utilisez le paiement manuel."
    )
    default_code = "paiement_en_ligne_desactive"


class PrestataireNonConfigure(APIException):
    """Le prestataire demandé n'est pas encore configuré côté serveur."""

    status_code = 503
    default_detail = "Ce prestataire de paiement n'est pas encore disponible."
    default_code = "prestataire_non_configure"


class SignatureWebhookInvalide(APIException):
    """La signature cryptographique de la notification est invalide."""

    status_code = 403
    default_detail = "Signature de webhook invalide."
    default_code = "signature_webhook_invalide"


class AnomaliePaiement(APIException):
    """Un écart financier empêche toute activation automatique."""

    status_code = 409
    default_detail = {
        "detail": "Le paiement reçu ne correspond pas à la demande.",
        "code": "anomalie_paiement",
    }
    default_code = "anomalie_paiement"
