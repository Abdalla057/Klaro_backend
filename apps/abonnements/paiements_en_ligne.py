"""Abstraction des prestataires de paiement et traitement des webhooks.

Chaque prestataire possède sa propre configuration par pays. Les webhooks sont
routés avec le pays afin d'utiliser le bon compte marchand et le bon secret.
"""

import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.fournisseurs.constants import Pays, prestataires_pour_pays

from .confirmations import confirmer_demande_abonnement
from .demandes import journaliser_demande
from .exceptions import (
    ErreurPrestataire,
    PrestataireNonConfigure,
    SignatureWebhookInvalide,
)
from .models import (
    DemandeAbonnement,
    EvenementAbonnement,
    PaiementAbonnement,
    WebhookEvenement,
)
from .tarifs import quantizer_montant

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


def normaliser_code_prestataire(code):
    return str(code or "").strip().lower().replace("-", "_")


def _pays_valide(pays):
    valeur = str(pays or "").strip().upper()
    if valeur not in Pays.values:
        raise PrestataireNonConfigure("Le pays de paiement est invalide.")
    return valeur


def _configurations():
    return getattr(settings, "PAIEMENT_PRESTATAIRES", {}) or {}


def configuration_prestataire(code, pays):
    return (
        _configurations()
        .get(normaliser_code_prestataire(code), {})
        .get(_pays_valide(pays), {})
        or {}
    )


def _valeur_config(config, cle, defaut=""):
    valeur = config.get(cle)
    return valeur if valeur not in (None, "") else defaut


def _entete(entetes, nom):
    if not entetes:
        return ""
    return entetes.get(nom) or entetes.get(nom.lower()) or ""


class PrestatairePaiement(ABC):
    """Interface commune à tous les prestataires de paiement."""

    code = ""
    nom = ""
    moyen_paiement = PaiementAbonnement.MoyenPaiement.MOBILE_MONEY

    def est_disponible(self, pays):
        return bool(configuration_prestataire(self.code, pays))

    @abstractmethod
    def initier_paiement(self, demande):
        """Retourne l'URL de paiement et les références utiles."""

    @abstractmethod
    def verifier_signature(self, payload_brut, entetes, pays):
        """Vérifie la signature de la notification pour ce pays."""

    @abstractmethod
    def extraire_notification(self, donnees):
        """Normalise la notification du prestataire."""

    def verifier_statut(self, paiement):
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

    def annuler_paiement(self, paiement):
        raise PrestataireNonConfigure(
            "L'annulation chez ce prestataire n'est pas disponible."
        )


class WavePaiement(PrestatairePaiement):
    code = "wave"
    nom = "Wave"

    def est_disponible(self, pays):
        config = configuration_prestataire(self.code, pays)
        return bool(config.get("api_key") and config.get("webhook_secret"))

    def _config(self, pays):
        config = configuration_prestataire(self.code, pays)
        if not config:
            raise PrestataireNonConfigure(
                "Wave n'est pas configuré pour ce pays."
            )
        if not config.get("api_key"):
            raise PrestataireNonConfigure(
                "La clé API Wave n'est pas configurée pour ce pays."
            )
        return config

    @staticmethod
    def _montant_api(montant, devise):
        valeur = quantizer_montant(montant, devise)
        if str(devise).upper() in {"XOF", "GNF"}:
            return str(int(valeur))
        return format(valeur, "f")

    @staticmethod
    def _url_retour(config, demande, statut):
        frontend = str(
            _valeur_config(config, "frontend_url", settings.FRONTEND_URL)
        ).rstrip("/")
        base = _valeur_config(
            config,
            f"{statut}_url",
            f"{frontend}/fournisseur/abonnement",
        )
        separateur = "&" if "?" in base else "?"
        return (
            f"{base}{separateur}"
            + urlencode(
                {
                    "paiement": statut,
                    "demande": demande.reference,
                }
            )
        )

    def initier_paiement(self, demande):
        config = self._config(demande.pays)
        base_url = str(
            _valeur_config(config, "base_url", "https://api.wave.com")
        ).rstrip("/")
        url = f"{base_url}/v1/checkout/sessions"
        payload = {
            "amount": self._montant_api(demande.montant, demande.devise),
            "currency": demande.devise,
            "client_reference": demande.reference,
            "success_url": self._url_retour(config, demande, "retour"),
            "error_url": self._url_retour(config, demande, "erreur"),
        }
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(
                timeout=float(_valeur_config(config, "timeout_seconds", 15))
            ) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ErreurPrestataire(
                "Impossible de joindre Wave. Réessayez dans quelques instants."
            ) from exc

        if response.status_code >= 400:
            raise ErreurPrestataire(
                "Wave a refusé la création de la session de paiement."
            )
        try:
            donnees = response.json()
        except ValueError as exc:
            raise ErreurPrestataire(
                "Wave a retourné une réponse de paiement invalide."
            ) from exc

        paiement_url = str(donnees.get("wave_launch_url") or "")
        if not paiement_url:
            raise ErreurPrestataire(
                "Wave n'a pas retourné d'URL de paiement."
            )
        return {
            "provider_session_id": str(donnees.get("id") or ""),
            "provider_transaction_id": str(
                donnees.get("transaction_id") or ""
            ),
            "provider_checkout_url": paiement_url,
            "paiement_url": paiement_url,
            "provider_statut": str(
                donnees.get("payment_status")
                or donnees.get("checkout_status")
                or ""
            ),
            "reference_transaction": str(
                donnees.get("client_reference") or demande.reference
            ),
        }

    def verifier_signature(self, payload_brut, entetes, pays):
        config = configuration_prestataire(self.code, pays)
        secret = str(config.get("webhook_secret") or "")
        if not secret:
            raise PrestataireNonConfigure(
                "Le secret webhook Wave n'est pas configuré pour ce pays."
            )
        if isinstance(payload_brut, str):
            payload_brut = payload_brut.encode("utf-8")

        entete = _entete(entetes, "Wave-Signature")
        if not entete:
            return False

        horodatage = ""
        signatures = []
        for partie in str(entete).split(","):
            cle, separateur, valeur = partie.strip().partition("=")
            if not separateur:
                continue
            if cle == "t":
                horodatage = valeur
            elif cle == "v1":
                signatures.append(valeur)
        if not horodatage or not signatures:
            return False

        try:
            horodatage_int = int(horodatage)
        except (TypeError, ValueError):
            return False
        tolerance = int(
            getattr(settings, "PAIEMENT_WEBHOOK_TOLERANCE_SECONDES", 300)
        )
        if abs(int(time.time()) - horodatage_int) > tolerance:
            return False

        signe = f"{horodatage}.".encode("utf-8") + payload_brut
        attendu = hmac.new(
            secret.encode("utf-8"),
            signe,
            hashlib.sha256,
        ).hexdigest()
        return any(hmac.compare_digest(attendu, signature) for signature in signatures)

    def extraire_notification(self, donnees):
        donnees = donnees if isinstance(donnees, dict) else {}
        data = donnees.get("data")
        data = data if isinstance(data, dict) else {}
        type_evenement = str(donnees.get("type") or "")
        statut = str(
            data.get("payment_status")
            or data.get("status")
            or ""
        )
        if not statut:
            if type_evenement.endswith("completed"):
                statut = "succeeded"
            elif "failed" in type_evenement:
                statut = "failed"
        return {
            "event_id": str(
                donnees.get("id")
                or donnees.get("event_id")
                or ""
            ),
            "event_type": type_evenement,
            "transaction_id": str(data.get("transaction_id") or ""),
            "provider_session_id": str(data.get("id") or ""),
            "reference": str(
                data.get("client_reference")
                or donnees.get("client_reference")
                or ""
            ),
            "statut": statut,
            "montant": data.get("amount"),
            "devise": str(data.get("currency") or ""),
        }

    def verifier_statut(self, paiement):
        session_id = str(paiement.provider_session_id or "")
        if not session_id:
            raise PrestataireNonConfigure(
                "Aucune session Wave n'est associée à ce paiement."
            )
        config = self._config(paiement.pays)
        base_url = str(
            _valeur_config(config, "base_url", "https://api.wave.com")
        ).rstrip("/")
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(
                timeout=float(_valeur_config(config, "timeout_seconds", 15))
            ) as client:
                response = client.get(
                    f"{base_url}/v1/checkout/sessions/{session_id}",
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise ErreurPrestataire(
                "Impossible de vérifier le paiement auprès de Wave."
            ) from exc
        if response.status_code >= 400:
            raise ErreurPrestataire(
                "Wave n'a pas pu retourner le statut du paiement."
            )
        try:
            donnees = response.json()
        except ValueError as exc:
            raise ErreurPrestataire(
                "Wave a retourné un statut de paiement invalide."
            ) from exc
        return str(
            donnees.get("payment_status")
            or donnees.get("checkout_status")
            or ""
        )


class PayTechPaiement(PrestatairePaiement):
    code = "paytech"
    nom = "PayTech"
    moyen_paiement = PaiementAbonnement.MoyenPaiement.MOBILE_MONEY

    def est_disponible(self, pays):
        config = configuration_prestataire(self.code, pays)
        return bool(config.get("api_key") and config.get("api_secret"))

    def _config(self, pays):
        config = configuration_prestataire(self.code, pays)
        if not config:
            raise PrestataireNonConfigure(
                "PayTech n'est pas configuré pour ce pays."
            )
        if not config.get("api_key") or not config.get("api_secret"):
            raise PrestataireNonConfigure(
                "Les clés PayTech ne sont pas configurées pour ce pays."
            )
        return config

    @staticmethod
    def _montant_api(montant, devise):
        valeur = quantizer_montant(montant, devise)
        if str(devise).upper() in {"XOF", "GNF"}:
            return str(int(valeur))
        return format(valeur, "f")

    @staticmethod
    def _url_retour(config, demande, statut):
        frontend = str(
            _valeur_config(config, "frontend_url", settings.FRONTEND_URL)
        ).rstrip("/")
        cle = "success_url" if statut == "succes" else "cancel_url"
        base = str(_valeur_config(config, cle, f"{frontend}/fournisseur/abonnement"))
        separateur = "&" if "?" in base else "?"
        return (
            f"{base}{separateur}"
            + urlencode(
                {
                    "paiement": statut,
                    "demande": demande.reference,
                }
            )
        )

    @staticmethod
    def _message_erreur(reponse, defaut):
        try:
            donnees = reponse.json()
        except ValueError:
            return defaut
        if not isinstance(donnees, dict):
            return defaut
        message = str(donnees.get("message") or "").strip()
        erreurs = donnees.get("error") or donnees.get("errors") or []
        if isinstance(erreurs, dict):
            details = " ".join(str(valeur) for valeur in erreurs.values() if valeur)
        elif isinstance(erreurs, (list, tuple)):
            details = " ".join(str(valeur) for valeur in erreurs if valeur)
        else:
            details = str(erreurs).strip()
        return " - ".join(part for part in (message, details) if part) or defaut

    def initier_paiement(self, demande):
        config = self._config(demande.pays)
        base_url = str(
            _valeur_config(config, "base_url", "https://paytech.sn/api")
        ).rstrip("/")
        ipn_url = str(
            _valeur_config(
                config,
                "ipn_url",
                f"{str(settings.BACKEND_URL).rstrip('/')}/api/paiements/webhooks/"
                f"paytech/{demande.pays}/",
            )
        ).strip()
        ipn = urlparse(ipn_url)
        if ipn.scheme != "https" or not ipn.netloc:
            raise PrestataireNonConfigure(
                "PayTech exige une URL IPN HTTPS publique. Définissez "
                f"PAYTECH_{demande.pays}_IPN_URL dans backend/.env."
            )
        payload = {
            "item_name": f"Abonnement {demande.plan.nom}",
            "item_price": self._montant_api(demande.montant, demande.devise),
            "currency": demande.devise,
            "ref_command": demande.reference,
            "command_name": (
                f"Abonnement {demande.plan.nom} - {demande.reference}"
            ),
            "env": str(_valeur_config(config, "environnement", "test")),
            "ipn_url": ipn_url,
            "success_url": self._url_retour(config, demande, "succes"),
            "cancel_url": self._url_retour(config, demande, "annulation"),
            "custom_field": json.dumps(
                {
                    "demande_id": str(demande.id),
                    "reference": demande.reference,
                }
            ),
        }
        cible = str(_valeur_config(config, "target_payment", "")).strip()
        if cible:
            payload["target_payment"] = cible
        headers = {
            "API_KEY": str(config["api_key"]),
            "API_SECRET": str(config["api_secret"]),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(
                timeout=float(_valeur_config(config, "timeout_seconds", 15))
            ) as client:
                response = client.post(
                    f"{base_url}/payment/request-payment",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise ErreurPrestataire(
                "Impossible de joindre PayTech. Réessayez dans quelques instants."
            ) from exc
        if response.status_code >= 400:
            raise ErreurPrestataire(
                self._message_erreur(
                    response,
                    "PayTech a refusé la création de la session de paiement.",
                )
            )
        try:
            donnees = response.json()
        except ValueError as exc:
            raise ErreurPrestataire(
                "PayTech a retourné une réponse de paiement invalide."
            ) from exc
        if not isinstance(donnees, dict) or str(donnees.get("success")) not in {
            "1",
            "True",
            "true",
        }:
            raise ErreurPrestataire(
                str(donnees.get("message") or "PayTech a refusé le paiement.")
            )
        paiement_url = str(
            donnees.get("redirect_url") or donnees.get("redirectUrl") or ""
        )
        if not paiement_url:
            raise ErreurPrestataire(
                "PayTech n'a pas retourné d'URL de paiement."
            )
        jeton = str(donnees.get("token") or "")
        return {
            "provider_session_id": jeton,
            "provider_transaction_id": jeton,
            "provider_checkout_url": paiement_url,
            "paiement_url": paiement_url,
            "provider_statut": "",
            "reference_transaction": str(
                donnees.get("ref_command") or demande.reference
            ),
        }

    def verifier_signature(self, payload_brut, entetes, pays):
        config = configuration_prestataire(self.code, pays)
        api_key = str(config.get("api_key") or "")
        api_secret = str(config.get("api_secret") or "")
        if not api_key or not api_secret:
            raise PrestataireNonConfigure(
                "Les clés PayTech ne sont pas configurées pour ce pays."
            )
        donnees = _corps_formulaire(payload_brut)
        hmac_recu = str(donnees.get("hmac_compute") or "").strip()
        if hmac_recu:
            message = "|".join(
                [
                    str(
                        donnees.get("final_item_price")
                        or donnees.get("item_price")
                        or ""
                    ),
                    str(donnees.get("ref_command") or ""),
                    api_key,
                ]
            )
            attendu = hmac.new(
                api_secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(attendu, hmac_recu)
        cle_recue = str(donnees.get("api_key_sha256") or "").strip()
        secret_recu = str(donnees.get("api_secret_sha256") or "").strip()
        if not cle_recue or not secret_recu:
            return False
        cle_attendue = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        secret_attendu = hashlib.sha256(api_secret.encode("utf-8")).hexdigest()
        return hmac.compare_digest(cle_attendue, cle_recue) and hmac.compare_digest(
            secret_attendu, secret_recu
        )

    def extraire_notification(self, donnees):
        donnees = donnees if isinstance(donnees, dict) else {}
        type_evenement = str(donnees.get("type_event") or "").strip().lower()
        statut = str(donnees.get("statut") or "").strip()
        if not statut:
            if type_evenement == "sale_complete":
                statut = "success"
            elif type_evenement in {"sale_canceled", "refund_complete"}:
                statut = "failed"
        jeton = str(donnees.get("token") or "")
        montant = (
            donnees.get("final_item_price")
            or donnees.get("final_item_price_xof")
            or donnees.get("item_price")
            or donnees.get("item_price_xof")
        )
        return {
            "event_id": jeton,
            "event_type": type_evenement,
            "transaction_id": jeton,
            "provider_session_id": jeton,
            "reference": str(donnees.get("ref_command") or ""),
            "statut": statut,
            "montant": montant,
            "devise": str(donnees.get("currency") or ""),
        }

    def verifier_statut(self, paiement):
        jeton = str(paiement.provider_session_id or "")
        if not jeton:
            raise PrestataireNonConfigure(
                "Aucun token PayTech n'est associé à ce paiement."
            )
        config = self._config(paiement.pays)
        base_url = str(
            _valeur_config(config, "base_url", "https://paytech.sn/api")
        ).rstrip("/")
        headers = {
            "API_KEY": str(config["api_key"]),
            "API_SECRET": str(config["api_secret"]),
            "Accept": "application/json",
        }
        try:
            with httpx.Client(
                timeout=float(_valeur_config(config, "timeout_seconds", 15))
            ) as client:
                response = client.get(
                    f"{base_url}/payment/get-status",
                    params={"token_payment": jeton},
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise ErreurPrestataire(
                "Impossible de vérifier le paiement auprès de PayTech."
            ) from exc
        if response.status_code >= 400:
            raise ErreurPrestataire(
                "PayTech n'a pas pu retourner le statut du paiement."
            )
        try:
            donnees = response.json()
        except ValueError as exc:
            raise ErreurPrestataire(
                "PayTech a retourné un statut de paiement invalide."
            ) from exc
        if not isinstance(donnees, dict) or str(donnees.get("success")) not in {
            "1",
            "True",
            "true",
        }:
            raise ErreurPrestataire(
                str(donnees.get("message") or "PayTech n'a pas retourné de statut.")
            )
        imbriquees = donnees.get("data")
        imbriquees = imbriquees if isinstance(imbriquees, dict) else {}
        statut = (
            donnees.get("state")
            or donnees.get("status")
            or donnees.get("payment_status")
            or imbriquees.get("state")
            or imbriquees.get("status")
        )
        return str(statut or "")


class OrangeMoneyPaiement(PrestatairePaiement):
    code = "orange_money"
    nom = "Orange Money"

    def est_disponible(self, pays):
        return False

    def initier_paiement(self, demande):
        raise PrestataireNonConfigure(
            "L'intégration Orange Money sera activée après validation du "
            "contrat technique par pays."
        )

    def verifier_signature(self, payload_brut, entetes, pays):
        raise PrestataireNonConfigure(
            "L'intégration Orange Money n'est pas encore activée."
        )

    def extraire_notification(self, donnees):
        raise PrestataireNonConfigure(
            "L'intégration Orange Money n'est pas encore activée."
        )


PRESTATAIRES = {
    PayTechPaiement.code: PayTechPaiement(),
    WavePaiement.code: WavePaiement(),
    OrangeMoneyPaiement.code: OrangeMoneyPaiement(),
}


def obtenir_prestataire(code, pays=None):
    code = normaliser_code_prestataire(code)
    prestataire = PRESTATAIRES.get(code)
    if prestataire is None:
        raise PrestataireNonConfigure(
            "Ce prestataire de paiement n'est pas pris en charge."
        )
    if pays is not None:
        pays = _pays_valide(pays)
        if code not in prestataires_pour_pays(pays):
            raise PrestataireNonConfigure(
                "Ce prestataire n'est pas disponible dans ce pays."
            )
    return prestataire


def prestataires_disponibles(pays):
    pays = _pays_valide(pays)
    return [
        prestataire
        for code in prestataires_pour_pays(pays)
        if (prestataire := PRESTATAIRES.get(code)) is not None
        and prestataire.est_disponible(pays)
    ]


def _secret_webhook(code, pays):
    """Compatibilité avec l'ancienne configuration globale."""
    config = configuration_prestataire(code, pays)
    secret = config.get("webhook_secret")
    if secret:
        return secret
    secrets = getattr(settings, "PAIEMENT_WEBHOOK_SECRETS", {}) or {}
    secret = secrets.get(code) or ""
    if not secret:
        raise PrestataireNonConfigure(
            "Le secret de vérification de ce prestataire n'est pas configuré."
        )
    return secret


def verifier_signature_hmac(code, payload_brut, entetes, pays=Pays.SENEGAL):
    """Ancien schéma HMAC conservé pour les intégrations génériques."""
    secret = _secret_webhook(normaliser_code_prestataire(code), pays)
    if isinstance(payload_brut, str):
        payload_brut = payload_brut.encode("utf-8")
    signature = ""
    for nom in (
        "X-Klaro-Signature",
        "X-Signature",
        "X-Webhook-Signature",
    ):
        signature = _entete(entetes, nom)
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


def _identifiant_evenement_derive(code, pays, notification, payload_brut):
    base = "|".join(
        [
            code,
            pays,
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


def _corps_formulaire(payload_brut):
    if isinstance(payload_brut, bytes):
        texte = payload_brut.decode("utf-8", errors="replace")
    else:
        texte = str(payload_brut or "")
    donnees = {}
    for cle, valeurs in parse_qs(texte, keep_blank_values=True).items():
        if valeurs:
            donnees[cle] = valeurs[0]
    return donnees


def _donnees_notification(payload_brut):
    if isinstance(payload_brut, bytes):
        texte = payload_brut.decode("utf-8", errors="replace")
    else:
        texte = str(payload_brut or "")
    if texte:
        try:
            donnees = json.loads(texte)
            if isinstance(donnees, dict):
                return donnees
        except json.JSONDecodeError:
            pass
    return _corps_formulaire(texte)


def _notification_par_defaut():
    return {
        "event_id": "",
        "event_type": "",
        "transaction_id": "",
        "provider_session_id": "",
        "reference": "",
        "statut": "",
        "montant": None,
        "devise": "",
    }


def traiter_webhook_paiement(
    code_prestataire,
    pays,
    payload_brut,
    entetes,
    effectue_par=None,
):
    """Traite une notification externe avec vérification et idempotence."""
    code = normaliser_code_prestataire(code_prestataire)
    pays = _pays_valide(pays)
    prestataire = obtenir_prestataire(code, pays)

    if isinstance(payload_brut, str):
        payload_brut = payload_brut.encode("utf-8")
    texte = payload_brut.decode("utf-8", errors="replace")
    donnees = _donnees_notification(payload_brut)
    notification = prestataire.extraire_notification(donnees)
    notification = {**_notification_par_defaut(), **notification}
    if not notification["event_id"]:
        notification["event_id"] = _identifiant_evenement_derive(
            code,
            pays,
            notification,
            payload_brut,
        )

    try:
        with transaction.atomic():
            evenement = WebhookEvenement.objects.select_for_update().filter(
                prestataire=code,
                pays=pays,
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
                    pays=pays,
                    provider_event_id=notification["event_id"],
                    provider_transaction_id=notification["transaction_id"],
                    reference_demande=notification["reference"],
                    payload_brut_protege=texte[:20000],
                    statut_traitement=WebhookEvenement.StatutTraitement.RECU,
                )
    except IntegrityError:
        evenement = WebhookEvenement.objects.get(
            prestataire=code,
            pays=pays,
            provider_event_id=notification["event_id"],
        )
        return evenement, True

    try:
        signature_valide = prestataire.verifier_signature(
            payload_brut,
            entetes,
            pays,
        )
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
    if demande.pays != pays:
        return (
            _marquer_evenement(
                evenement,
                WebhookEvenement.StatutTraitement.ANOMALIE,
                "Le pays du webhook ne correspond pas à la demande.",
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
                prestataire=code,
                pays=pays,
                statut__in=[
                    PaiementAbonnement.Statut.INITIE,
                    PaiementAbonnement.Statut.EN_ATTENTE,
                    PaiementAbonnement.Statut.EN_VERIFICATION,
                ],
            ).update(
                statut=PaiementAbonnement.Statut.ECHEC,
                provider_statut=notification["statut"],
                date_modification=timezone.now(),
            )
            journaliser_demande(
                demande,
                EvenementAbonnement.Type.ANOMALIE_PAIEMENT,
                nouveau_statut=demande.statut,
                motif=f"Paiement en échec chez {code}.",
                montant=demande.montant,
                reference_paiement=notification["transaction_id"],
                metadonnees={"prestataire": code, "pays": pays},
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
        meme_transaction = (
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
                    if meme_transaction
                    else WebhookEvenement.StatutTraitement.ANOMALIE
                ),
                (
                    "Événement déjà traité."
                    if meme_transaction
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
                provider_session_id=notification["provider_session_id"],
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
    """Interroge les prestataires pour les paiements restés en attente."""
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
            prestataire = obtenir_prestataire(
                paiement.prestataire,
                paiement.pays,
            )
            statut = prestataire.verifier_statut(paiement)
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
                provider_session_id=paiement.provider_session_id,
                motif="Confirmation par réconciliation.",
            )
            rapport["confirmes"] += 1
        except ValidationError:
            rapport["erreurs"] += 1
    return rapport
