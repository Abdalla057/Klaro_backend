import json
import logging
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import NotificationExterne

logger = logging.getLogger(__name__)

SALT_FACTURE = "commerce-facture-client"
DUREE_LIEN_FACTURE = getattr(
    settings,
    "FACTURE_LIEN_DUREE_SECONDES",
    60 * 60 * 24 * 30,
)
DELAI_MAX_RETRY_SECONDES = 300


class NotificationNonConfiguree(Exception):
    """Le fournisseur d'envoi n'est pas configuré : aucun faux succès."""


def generer_token_facture(facture):
    return signing.dumps(
        {"facture": facture.pk, "client": facture.vente.client_id},
        salt=SALT_FACTURE,
    )


def lire_token_facture(token):
    return signing.loads(
        token,
        salt=SALT_FACTURE,
        max_age=getattr(
            settings,
            "FACTURE_LIEN_DUREE_SECONDES",
            DUREE_LIEN_FACTURE,
        ),
    )


def lien_public_facture(facture, request=None):
    token = generer_token_facture(facture)
    chemin = f"/api/commerce/factures/lien/{token}/pdf/"
    if request is not None:
        return request.build_absolute_uri(chemin)
    base = getattr(settings, "BACKEND_URL", "").rstrip("/")
    return f"{base}{chemin}" if base else chemin


def _poster_json(url, payload, headers):
    corps = json.dumps(payload).encode("utf-8")
    entetes = {"Content-Type": "application/json", "Accept": "application/json"}
    entetes.update(headers)
    requete = urllib.request.Request(
        url,
        data=corps,
        headers=entetes,
        method="POST",
    )
    timeout = getattr(settings, "NOTIFICATION_HTTP_TIMEOUT", 10)
    with urllib.request.urlopen(requete, timeout=timeout) as reponse:
        contenu = reponse.read().decode("utf-8") or "{}"
    try:
        return json.loads(contenu)
    except json.JSONDecodeError:
        return {}


def envoyer_sms(destinataire, message):
    url = getattr(settings, "SMS_API_URL", "")
    cle = getattr(settings, "SMS_API_KEY", "")
    expediteur = getattr(settings, "SMS_SENDER", "")
    if not url or not cle:
        raise NotificationNonConfiguree(
            "Le service SMS n'est pas configuré (SMS_API_URL/SMS_API_KEY)."
        )
    reponse = _poster_json(
        url,
        {
            "to": destinataire,
            "message": message,
            "sender": expediteur,
        },
        {"Authorization": f"Bearer {cle}"},
    )
    identifiant = (
        reponse.get("message_id")
        or reponse.get("id")
        or reponse.get("data", {}).get("id")
    )
    return str(identifiant) if identifiant else ""


def envoyer_whatsapp(destinataire, message):
    url = getattr(settings, "WHATSAPP_API_URL", "")
    jeton = getattr(settings, "WHATSAPP_TOKEN", "")
    numero = getattr(settings, "WHATSAPP_PHONE_ID", "")
    if not url or not jeton or not numero:
        raise NotificationNonConfiguree(
            "Le service WhatsApp n'est pas configuré "
            "(WHATSAPP_API_URL/WHATSAPP_TOKEN/WHATSAPP_PHONE_ID)."
        )
    url = f"{url.rstrip('/')}/{numero}/messages"
    reponse = _poster_json(
        url,
        {
            "messaging_product": "whatsapp",
            "to": destinataire,
            "type": "text",
            "text": {"body": message},
        },
        {"Authorization": f"Bearer {jeton}"},
    )
    messages = reponse.get("messages") or []
    if messages and messages[0].get("id"):
        return str(messages[0]["id"])
    return ""


def construire_message_externe(notification):
    lien = ""
    if notification.facture_id:
        lien = lien_public_facture(notification.facture)
    lignes = [notification.titre, notification.message]
    if lien:
        lignes.append(f"Facture : {lien}")
    return "\n".join(lignes)


def preparer_notifications_externes(notification):
    """Crée les envois SMS/WhatsApp activés pour le fournisseur.

    Aucun envoi n'est créé si le fournisseur n'a pas explicitement activé le
    canal. La création est idempotente grâce à la contrainte unique.
    """
    configuration = getattr(notification.fournisseur, "configuration_notifications", None)
    if configuration is None:
        return []
    canaux = []
    if configuration.sms_actif:
        canaux.append(NotificationExterne.Canal.SMS)
    if configuration.whatsapp_actif:
        canaux.append(NotificationExterne.Canal.WHATSAPP)

    envois = []
    for canal in canaux:
        envoi, _ = NotificationExterne.objects.get_or_create(
            notification=notification,
            canal=canal,
            defaults={
                "statut": NotificationExterne.Statut.EN_ATTENTE,
                "tentatives_max": getattr(
                    settings,
                    "NOTIFICATION_TENTATIVES_MAX",
                    5,
                ),
            },
        )
        envois.append(envoi)
    return envois


def _planifier_nouvelle_tentative(envoi):
    """Programme un nouvel essai avec un backoff exponentiel borné."""
    if envoi.tentatives >= envoi.tentatives_max:
        envoi.statut = NotificationExterne.Statut.ECHOUE
        envoi.date_prochaine_tentative = None
        return
    delai = min(
        DELAI_MAX_RETRY_SECONDES,
        15 * (2 ** max(envoi.tentatives - 1, 0)),
    )
    envoi.statut = NotificationExterne.Statut.EN_ATTENTE
    envoi.date_prochaine_tentative = timezone.now() + timedelta(seconds=delai)


def _tenter_envoi(envoi):
    envoi.tentatives += 1
    message = construire_message_externe(envoi.notification)
    destinataire = envoi.notification.client.telephone
    try:
        if envoi.canal == NotificationExterne.Canal.SMS:
            identifiant = envoyer_sms(destinataire, message)
        else:
            identifiant = envoyer_whatsapp(destinataire, message)
    except NotificationNonConfiguree as exc:
        envoi.statut = NotificationExterne.Statut.ECHOUE
        envoi.derniere_erreur = str(exc)
        envoi.date_prochaine_tentative = None
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # pragma: no cover - lecture best effort
            detail = ""
        envoi.derniere_erreur = f"HTTP {exc.code} {detail}".strip()
        if 400 <= exc.code < 500 and exc.code != 429:
            envoi.statut = NotificationExterne.Statut.ECHOUE
            envoi.date_prochaine_tentative = None
        else:
            _planifier_nouvelle_tentative(envoi)
    except Exception as exc:  # pragma: no cover - dépend du prestataire
        envoi.derniere_erreur = f"{type(exc).__name__}: {exc}"
        _planifier_nouvelle_tentative(envoi)
    else:
        envoi.statut = NotificationExterne.Statut.ENVOYE
        envoi.provider_message_id = identifiant or ""
        envoi.derniere_erreur = ""
        envoi.date_prochaine_tentative = None
        envoi.date_envoi = timezone.now()
    envoi.save(
        update_fields=[
            "statut",
            "tentatives",
            "derniere_erreur",
            "provider_message_id",
            "date_prochaine_tentative",
            "date_envoi",
        ]
    )
    return envoi


def dispatcher_notifications(notification_id):
    from .models import NotificationClient

    notification = (
        NotificationClient.objects.select_related(
            "client",
            "fournisseur",
            "facture",
            "vente",
        )
        .filter(pk=notification_id)
        .first()
    )
    if notification is None:
        return []
    maintenant = timezone.now()
    envois = []
    for envoi in notification.envois.select_related(
        "notification",
        "notification__client",
        "notification__facture",
        "notification__vente",
    ).filter(
        statut=NotificationExterne.Statut.EN_ATTENTE,
    ).filter(
        Q(date_prochaine_tentative__isnull=True)
        | Q(date_prochaine_tentative__lte=maintenant)
    ):
        envois.append(_tenter_envoi(envoi))
    return envois


@transaction.atomic
def traiter_envois_dus(limite=None):
    """Traite les envois arrivés à échéance (outbox, sans Celery).

    ``select_for_update(skip_locked=True)`` permet à plusieurs workers de
    tourner sans se bloquer ni envoyer deux fois la même notification.
    """
    maintenant = timezone.now()
    queryset = (
        NotificationExterne.objects.select_for_update(skip_locked=True)
        .filter(statut=NotificationExterne.Statut.EN_ATTENTE)
        .filter(
            Q(date_prochaine_tentative__isnull=True)
            | Q(date_prochaine_tentative__lte=maintenant)
        )
        .order_by("date_creation", "id")
    )
    if limite:
        queryset = queryset[:limite]
    identifiants = list(queryset.values_list("pk", flat=True))

    envois = []
    for identifiant in identifiants:
        envoi = (
            NotificationExterne.objects.select_related(
                "notification",
                "notification__client",
                "notification__facture",
                "notification__vente",
            )
            .filter(pk=identifiant)
            .first()
        )
        if envoi is not None:
            envois.append(_tenter_envoi(envoi))
    return envois


def dispatcher_notifications_sur_commit(notification_id):
    """Callback post-commit qui ne doit jamais faire échouer la vente."""
    try:
        dispatcher_notifications(notification_id)
    except Exception:  # pragma: no cover - sécurité post-commit
        logger.exception(
            "Échec inattendu lors de l'envoi des notifications %s",
            notification_id,
        )


def renvoyer_notifications_externes(notification):
    """Relance les envois échoués d'une notification."""
    notification.envois.filter(
        statut=NotificationExterne.Statut.ECHOUE
    ).update(
        statut=NotificationExterne.Statut.EN_ATTENTE,
        tentatives=0,
        date_prochaine_tentative=None,
        derniere_erreur="",
    )
    return dispatcher_notifications(notification.pk)
