from decimal import Decimal
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.finances.services import enregistrer_encaissement_financier

from .models import Encaissement


def encaissements_accessibles_par(utilisateur):
    """Retourne les encaissements visibles selon le rôle de l'utilisateur."""
    if hasattr(utilisateur, "fournisseur_profile"):
        return Encaissement.objects.filter(
            client__fournisseur=utilisateur.fournisseur_profile
        ).order_by("-date_creation")
    if hasattr(utilisateur, "agent_profile"):
        # Un agent voit tous les encaissements des clients de son fournisseur :
        # n'importe quel agent peut confirmer un paiement en attente.
        return Encaissement.objects.filter(
            client__fournisseur=utilisateur.agent_profile.fournisseur
        ).order_by("-date_creation")
    if hasattr(utilisateur, "client_profile"):
        return Encaissement.objects.filter(
            client=utilisateur.client_profile
        ).order_by("-date_creation")
    return Encaissement.objects.none()


def creer_encaissement(client=None, **donnees):
    """Crée un encaissement en attente pour le client connecté."""
    if client is None:
        raise PermissionDenied(
            "Seul un client peut initier un encaissement "
            "(choisir le montant à payer)."
        )
    return Encaissement.objects.create(client=client, **donnees)


def _verifier_agent(agent):
    if agent is None:
        raise PermissionDenied("Seul un agent peut confirmer un encaissement.")


@transaction.atomic
def creer_encaissement_par_agent(agent, client, montant, commentaire=""):
    """Enregistre un encaissement directement confirmé par un agent."""
    _verifier_agent(agent)
    if client.fournisseur_id != agent.fournisseur_id:
        raise PermissionDenied(
            "Cet agent ne peut pas encaisser pour un client d'un autre fournisseur."
        )
    if not client.compte_active:
        raise ValidationError(
            {"detail": "Le compte client doit être activé avant tout encaissement."}
        )
    if montant <= Decimal("0"):
        raise ValidationError({"montant": "Le montant doit être supérieur à zéro."})

    encaissement = Encaissement.objects.create(
        client=client,
        agent=agent,
        montant=montant,
        statut=Encaissement.Statut.CONFIRME,
        date_confirmation=timezone.now(),
        commentaire=commentaire,
    )
    enregistrer_encaissement_financier(encaissement, agent)
    return encaissement


@transaction.atomic
def confirmer_encaissement(encaissement, agent):
    """Confirme un encaissement en attente et met à jour le solde du client."""
    _verifier_agent(agent)
    try:
        encaissement.confirmer(agent)
    except DjangoValidationError as exc:
        raise ValidationError({"detail": exc.messages}) from exc
    enregistrer_encaissement_financier(
        encaissement,
        agent,
        appliquer_solde=False,
    )
    return encaissement


@transaction.atomic
def confirmer_encaissement_par_code(code, agent):
    """Confirme un encaissement à partir du code du QR code scanné par l'agent."""
    _verifier_agent(agent)
    if not code:
        raise ValidationError({"detail": "Le champ 'code' est requis."})
    try:
        code = UUID(str(code))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(
            {"detail": "Code de confirmation invalide."}
        ) from exc

    try:
        encaissement = Encaissement.objects.get(code_confirmation=code)
    except Encaissement.DoesNotExist as exc:
        raise NotFound("Encaissement introuvable.") from exc

    return confirmer_encaissement(encaissement, agent)


@transaction.atomic
def annuler_encaissement(encaissement, utilisateur):
    """Annule un encaissement en attente (client propriétaire ou fournisseur)."""
    est_proprietaire = (
        hasattr(utilisateur, "client_profile")
        and encaissement.client_id == utilisateur.client_profile.id
    )
    est_fournisseur = (
        hasattr(utilisateur, "fournisseur_profile")
        and encaissement.client.fournisseur_id
        == utilisateur.fournisseur_profile.id
    )
    if not (est_proprietaire or est_fournisseur):
        raise PermissionDenied("Vous ne pouvez pas annuler cet encaissement.")

    try:
        encaissement.annuler()
    except DjangoValidationError as exc:
        raise ValidationError({"detail": exc.messages}) from exc
    return encaissement
