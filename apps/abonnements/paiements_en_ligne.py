"""Abstraction des prestataires de paiement et traitement des webhooks.

Aucune logique propre à un prestataire ne doit fuiter dans les vues. Les
implémentations concrètes seront ajoutées dans ``providers`` au fil des
contrats marchands, sans modifier le flux de confirmation.
"""

import hashlib
import hmac
import json
from abc import ABC, abstractmethod

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .confirmations import confirmer_demande_abonnement
from .demandes import journaliser_demande
from .exceptions import PrestataireNonConfigure, SignatureWebhookInvalide
from .models import (
    DemandeAbonnement,
    EvenementAbonnement,
    PaiementAbonnement,
    WebhookEvenement,
)

STATUTS_SUCCES = {
    "success",
    "succes",
    "succeeded",
    "confirmed",
    "confirme",
    "paid",
    "paye",
    "completed",
    "complete",
    "approved",
}
STATUTS_ECHEC = {
    "failed",
    "failure",
    "echec",
    "cancelled",
    "canceled",
    "annule",
    "refused",
    "refuse",
    "declined",
    "expired",
    "expire",
}


class PrestatairePaiement(ABC):
    """Interface commune à tous les prestataires de paiement."""

    code = ""

    @abstractmethod
    def initier_paiement(self, demande):
        """Retourne au minimum l'URL de paiement et les références utiles."""

    def verifier_signature(self, payload_brut, entetes):
        """Vérifie la signature HMAC-SHA256 configurée pour ce prestataire."""
        return verifier_signature_hmac(self.code, payload_brut, entetes)

    def verifier_statut(self, reference_prestataire):
        raise PrestataireNonConfigure(
            "La vérification de statut n'est pas encore configurée."
        )

    def normaliser_statut(self, statut):
        valeur = str(statut or "").strip().lower()
        if valeur in STATUTS_SUCCES:
            return "SUCCES"
        if valeur in STATUTS_ECHEC:
            return "ECHEC"
        return "INCONNU"

    def annuler_paiement(self, reference_prestataire):
        raise PrestataireNonConfigure(
            "L'annulation chez ce prestataire n'est pas disponible."
        )


class WavePaiement(PrestatairePaiement):
    code = "wave"

    def initier_paiement(self, demande):
        raise PrestataireNonConfigure(
            "Wave n'est pas encore configuré. Ajoutez les clés marchandes."
        )


class OrangeMoneyPaiement(PrestatairePaiement):
    code = "orange_money"

    def initier_paiement(self, demande):
        raise PrestataireNonConfigure(
            "Orange Money n'est pas encore configuré. Ajoutez les clés marchandes."
        )


PRESTATAIRES = {
    WavePaiement.code: WavePaiement(),
    OrangeMoneyPaiement.code: OrangeMoneyPaiement(),
}


def normaliser_code_prestataire(code):
    return str(code or "").strip().lower().replace("-", "_")


def obtenir_prestataire(code):
    prestataire = PRESTATAIRES.get(normaliser_code_prestataire(code))
    if prestataire is None:
        raise PrestataireNonConfigure(
            "Ce prestataire de paiement n'est pas pris en charge."
        )
    return prestataire


def _secret_webhook(code):
    secrets = getattr(settings, "PAIEMENT_WEBHOOK_SECRETS", {}) or {}
    secret = secrets.get(code) or ""
    if not secret:
        raise PrestataireNonConfigure(
            "Le secret de vérification de ce prestataire n'est pas configuré."
        )
    return secret


def verifier_signature_hmac(code, payload_brut, entetes):
    """Vérifie une signature HMAC-SHA256, selon les en-têtes usuels.

    Cette implémentation générique doit être adaptée si un prestataire impose
    un algorithme ou un format d'en-tête différent dans sa documentation.
    """
    secret = _secret_webhook(code)
    if isinstance(payload_brut, str):
        payload_brut = payload_brut.encode("utf-8")

    signature = None
    for nom in (
        "X-Klaro-Signature",
        "X-Signature",
        f"X-{code.replace('_', '-').title()}-Signature",
        "X-Webhook-Signature",
    ):
        signature = entetes.get(nom) or entetes.get(nom.lower())
        if signature:
            break
    if not signature:
        return False

    signature = signature.removeprefix("sha256=").strip()
    attendu = hmac.new(
        secret.encode("utf-8"),
        payload_brut,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, attendu)


def _premier_chemin(donnees, chemins):
    for chemin in chemins:
        courant = donnees
        for cle in chemin.split("."):
            if not isinstance(courant, dict):
                courant = None
                break
            courant = courant.get(cle)
        if courant not in (None, ""):
            return courant
    return ""


def _extraire_notification(donnees):
    event_id = _premier_chemin(
        donnees,
        ["event_id", "eventId", "webhook_id", "id", "data.event_id"],
    )
    transaction_id = _premier_chemin(
        donnees,
        [
            "transaction_id",
            "transactionId",
            "payment_id",
            "paymentId",
            "data.transaction_id",
            "data.id",
        ],
    )
    reference = _premier_chemin(
        donnees,
        [
            "reference_demande",
            "reference",
            "client_reference",
            "metadata.reference",
            "data.reference_demande",
            "data.reference",
        ],
    )
    statut = _premier_chemin(
        donnees,
        ["status", "state", "payment_status", "data.status"],
    )
    montant = _premier_chemin(
        donnees,
        ["amount", "montant", "value", "data.amount", "data.montant"],
    )
    devise = _premier_chemin(
        donnees,
        ["currency", "devise", "data.currency", "data.devise"],
    )
    return {
        "event_id": str(event_id or ""),
        "transaction_id": str(transaction_id or ""),
        "reference": str(reference or ""),
        "statut": str(statut or ""),
        "montant": montant,
        "devise": str(devise or ""),
    }


def _identifiant_evenement_derive(code, notification, payload_brut):
    """Déduplication de secours si le prestataire ne fournit aucun identifiant."""
    base = "|".join(
        [
            code,
            notification["transaction_id"],
            notification["reference"],
            notification["statut"],
            str(notification["montant"]),
            str(notification["devise"]),
            payload_brut.decode("utf-8", errors="replace"),
        ]
    )
    return "sha256:" + hashlib.sha256(base.encode("utf-8")).hexdigest()


def _montant_comparable(montant):
    from decimal import Decimal, InvalidOperation

    try:
        valeur = Decimal(str(montant))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not valeur.is_finite():
        return None
    return valeur


def _marquer_evenement(evenement, statut, message="", signature_verifiee=None):
    evenement.statut_traitement = statut
    evenement.message_erreur = message
    evenement.date_traitement = timezone.now()
    champs = ["statut_traitement", "message_erreur", "date_traitement"]
    if signature_verifiee is not None:
        evenement.signature_verifiee = signature_verifiee
        champs.append("signature_verifiee")
    evenement.save(update_fields=champs)
    return evenement


def traiter_webhook_paiement(code_prestataire, payload_brut, entetes, effectue_par=None):
    """Traite une notification externe avec vérification et idempotence."""
    code = normaliser_code_prestataire(code_prestataire)
    prestataire = obtenir_prestataire(code)

    if isinstance(payload_brut, str):
        payload_brut = payload_brut.encode("utf-8")
    texte = payload_brut.decode("utf-8", errors="replace")
    try:
        donnees = json.loads(texte) if texte else {}
    except json.JSONDecodeError:
        donnees = {}
    notification = _extraire_notification(donnees)
    if not notification["event_id"]:
        notification["event_id"] = _identifiant_evenement_derive(
            code,
            notification,
            payload_brut,
        )

    try:
        with transaction.atomic():
            evenement = WebhookEvenement.objects.select_for_update().filter(
                prestataire=code,
                provider_event_id=notification["event_id"],
            ).first()
            if evenement is not None:
                evenement.nombre_tentatives += 1
                if evenement.statut_traitement != (
                    WebhookEvenement.StatutTraitement.ERREUR
                ):
                    evenement.save(update_fields=["nombre_tentatives"])
                    return evenement, True
                evenement.statut_traitement = (
                    WebhookEvenement.StatutTraitement.RECU
                )
                evenement.message_erreur = ""
                evenement.date_traitement = None
                evenement.save(
                    update_fields=[
                        "nombre_tentatives",
                        "statut_traitement",
                        "message_erreur",
                        "date_traitement",
                    ]
                )
            else:
                evenement = WebhookEvenement.objects.create(
                    prestataire=code,
                    provider_event_id=notification["event_id"],
                    provider_transaction_id=notification["transaction_id"],
                    reference_demande=notification["reference"],
                    payload_brut_protege=texte[:20000],
                    statut_traitement=WebhookEvenement.StatutTraitement.RECU,
                )
    except IntegrityError:
        evenement = WebhookEvenement.objects.get(
            prestataire=code,
            provider_event_id=notification["event_id"],
        )
        return evenement, True

    try:
        signature_valide = prestataire.verifier_signature(payload_brut, entetes)
    except PrestataireNonConfigure as exc:
        _marquer_evenement(
            evenement,
            WebhookEvenement.StatutTraitement.ERREUR,
            str(exc.detail if hasattr(exc, "detail") else exc),
            signature_verifiee=False,
        )
        raise
    if not signature_valide:
        _marquer_evenement(
            evenement,
            WebhookEvenement.StatutTraitement.ERREUR,
            "Signature invalide.",
            signature_verifiee=False,
        )
        raise SignatureWebhookInvalide()

    evenement.signature_verifiee = True
    evenement.save(update_fields=["signature_verifiee"])

    if not notification["reference"]:
        return (
            _marquer_evenement(
                evenement,
                WebhookEvenement.StatutTraitement.ANOMALIE,
                "Référence de demande absente.",
            ),
            False,
        )

    demande = (
        DemandeAbonnement.objects.select_related("plan", "fournisseur")
        .filter(reference=notification["reference"])
        .first()
    )
    if demande is None:
        return (
            _marquer_evenement(
                evenement,
                WebhookEvenement.StatutTraitement.ANOMALIE,
                "Aucune demande ne correspond à cette référence.",
            ),
            False,
        )

    montant_recu = _montant_comparable(notification["montant"])
    if montant_recu is None or montant_recu != demande.montant:
        return (
            _marquer_evenement(
                evenement,
                WebhookEvenement.StatutTraitement.ANOMALIE,
                "Le montant reçu ne correspond pas au montant figé de la demande.",
            ),
            False,
        )
    if notification["devise"] and notification["devise"].upper() != demande.devise:
        return (
            _marquer_evenement(
                evenement,
                WebhookEvenement.StatutTraitement.ANOMALIE,
                "La devise reçue ne correspond pas à la demande.",
            ),
            False,
        )

    statut_normalise = prestataire.normaliser_statut(notification["statut"])
    if statut_normalise == "INCONNU":
        return (
            _marquer_evenement(
                evenement,
                WebhookEvenement.StatutTraitement.IGNORE,
                f"Statut prestataire non reconnu : {notification['statut']}.",
            ),
            False,
        )

    if statut_normalise == "ECHEC":
        with transaction.atomic():
            PaiementAbonnement.objects.select_for_update().filter(
                demande_abonnement=demande,
                statut__in=[
                    PaiementAbonnement.Statut.INITIE,
                    PaiementAbonnement.Statut.EN_ATTENTE,
                    PaiementAbonnement.Statut.EN_VERIFICATION,
                ],
            ).update(
                statut=PaiementAbonnement.Statut.ECHEC,
                date_modification=timezone.now(),
            )
            journaliser_demande(
                demande,
                EvenementAbonnement.Type.ANOMALIE_PAIEMENT,
                nouveau_statut=demande.statut,
                motif=f"Paiement en échec chez {code}.",
                montant=demande.montant,
                reference_paiement=notification["transaction_id"],
                metadonnees={"prestataire": code},
            )
        return (
            _marquer_evenement(
                evenement,
                WebhookEvenement.StatutTraitement.IGNORE,
                "Paiement en échec : aucune activation.",
            ),
            False,
        )

    if demande.statut == DemandeAbonnement.Statut.CONFIRMEE:
        paiement_confirme = demande.paiements.filter(
            statut=PaiementAbonnement.Statut.CONFIRME
        ).first()
        même_transaction = (
            paiement_confirme is not None
            and notification["transaction_id"]
            and paiement_confirme.provider_transaction_id
            == notification["transaction_id"]
        )
        return (
            _marquer_evenement(
                evenement,
                (
                    WebhookEvenement.StatutTraitement.IGNORE
                    if même_transaction
                    else WebhookEvenement.StatutTraitement.ANOMALIE
                ),
                (
                    "Événement déjà traité."
                    if même_transaction
                    else "Paiement supplémentaire après confirmation."
                ),
            ),
            False,
        )

    try:
        with transaction.atomic():
            confirmer_demande_abonnement(
                demande,
                reference_transaction=notification["transaction_id"],
                prestataire=code,
                provider_transaction_id=notification["transaction_id"],
                effectue_par=effectue_par,
                motif=f"Confirmation automatique via webhook {code}.",
            )
    except Exception as exc:  # noqa: BLE001 - isoler l'échec pour traçabilité
        return (
            _marquer_evenement(
                evenement,
                WebhookEvenement.StatutTraitement.ERREUR,
                str(getattr(exc, "detail", exc))[:2000],
            ),
            False,
        )

    return (
        _marquer_evenement(
            evenement,
            WebhookEvenement.StatutTraitement.TRAITE,
            "Demande confirmée automatiquement.",
        ),
        False,
    )


def reconcilier_paiements_en_attente():
    """Interroge les prestataires pour les paiements restés en attente.

    À exécuter périodiquement pour compenser les webhooks perdus ou retardés.
    """
    rapport = {"traites": 0, "confirmes": 0, "ignores": 0, "erreurs": 0}
    paiements = PaiementAbonnement.objects.select_related(
        "demande_abonnement",
        "demande_abonnement__plan",
        "demande_abonnement__fournisseur",
    ).filter(
        statut__in=[
            PaiementAbonnement.Statut.INITIE,
            PaiementAbonnement.Statut.EN_ATTENTE,
        ],
        demande_abonnement__isnull=False,
    )
    for paiement in paiements:
        rapport["traites"] += 1
        try:
            prestataire = obtenir_prestataire(paiement.prestataire)
            statut = prestataire.verifier_statut(
                paiement.provider_transaction_id
            )
        except PrestataireNonConfigure:
            rapport["ignores"] += 1
            continue
        except Exception:  # noqa: BLE001 - une erreur isolée ne bloque pas le lot
            rapport["erreurs"] += 1
            continue

        if prestataire.normaliser_statut(statut) != "SUCCES":
            rapport["ignores"] += 1
            continue
        try:
            confirmer_demande_abonnement(
                paiement.demande_abonnement,
                reference_transaction=paiement.reference_transaction,
                prestataire=paiement.prestataire,
                provider_transaction_id=paiement.provider_transaction_id,
                motif="Confirmation par réconciliation.",
            )
            rapport["confirmes"] += 1
        except ValidationError:
            rapport["erreurs"] += 1
    return rapport
