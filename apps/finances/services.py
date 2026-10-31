from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.clients.models import Client
from apps.comptes.validators import normaliser_telephone

from .exceptions import EtatInvalide, OperationDejaEffectuee
from .models import DemandeDecaissement, TransactionFinanciere


def _montant_valide(montant):
    try:
        valeur = Decimal(str(montant))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"montant": "Montant invalide."}) from exc
    if not valeur.is_finite() or valeur <= 0:
        raise ValidationError({"montant": "Le montant doit être supérieur à zéro."})
    if valeur.as_tuple().exponent < -2:
        raise ValidationError(
            {"montant": "Le montant ne peut pas avoir plus de deux décimales."}
        )
    return valeur


def _appliquer_mouvement(client, sens, montant):
    delta = montant if sens == TransactionFinanciere.Sens.CREDIT else -montant
    client.solde_du = (client.solde_du or Decimal("0")) + delta
    client.save(update_fields=["solde_du"])
    return client.solde_du


def _creer_transaction(
    *,
    fournisseur,
    client,
    type_transaction,
    sens,
    montant,
    auteur=None,
    agent=None,
    encaissement=None,
    decaissement=None,
    commentaire="",
    cle_idempotence=None,
):
    return TransactionFinanciere.objects.create(
        fournisseur=fournisseur,
        client=client,
        agent=agent,
        encaissement=encaissement,
        decaissement=decaissement,
        type_transaction=type_transaction,
        sens=sens,
        montant=montant,
        statut=TransactionFinanciere.Statut.VALIDEE,
        auteur=auteur,
        commentaire=commentaire,
        cle_idempotence=cle_idempotence or None,
        date_validation=timezone.now(),
    )


def solde_disponible_client(client):
    montant_en_cours = (
        DemandeDecaissement.objects.filter(
            client=client,
            statut__in=DemandeDecaissement.STATUTS_EN_COURS,
        ).aggregate(total=Sum("montant"))["total"]
        or Decimal("0")
    )
    return (client.solde_du or Decimal("0")) - montant_en_cours


def calculer_solde_client(client):
    mouvements = TransactionFinanciere.objects.filter(
        client=client,
        statut=TransactionFinanciere.Statut.VALIDEE,
    )
    credits = (
        mouvements.filter(sens=TransactionFinanciere.Sens.CREDIT).aggregate(
            total=Sum("montant")
        )["total"]
        or Decimal("0")
    )
    debits = (
        mouvements.filter(sens=TransactionFinanciere.Sens.DEBIT).aggregate(
            total=Sum("montant")
        )["total"]
        or Decimal("0")
    )
    return credits - debits


@transaction.atomic
def attribuer_solde_initial(
    fournisseur,
    client,
    montant,
    auteur=None,
    commentaire="",
    cle_idempotence=None,
):
    montant = _montant_valide(montant)
    client = (
        Client.objects.select_for_update()
        .select_related("fournisseur", "utilisateur")
        .get(pk=client.pk)
    )
    if client.fournisseur_id != fournisseur.pk:
        raise PermissionDenied(
            "Vous ne pouvez pas attribuer un solde à un client d'un autre fournisseur."
        )
    if not client.compte_active:
        raise ValidationError(
            {"detail": "Le compte client doit être activé avant toute attribution."}
        )

    if cle_idempotence:
        existante = TransactionFinanciere.objects.filter(
            cle_idempotence=cle_idempotence
        ).first()
        if existante is not None:
            if (
                existante.client_id != client.pk
                or existante.type_transaction
                != TransactionFinanciere.Type.ATTRIBUTION_INITIALE
            ):
                raise ValidationError(
                    {"cle_idempotence": "Cette clé a déjà été utilisée."}
                )
            return existante

    if TransactionFinanciere.objects.filter(
        client=client,
        type_transaction=TransactionFinanciere.Type.ATTRIBUTION_INITIALE,
        statut=TransactionFinanciere.Statut.VALIDEE,
    ).exists():
        raise OperationDejaEffectuee(
            "Un solde initial a déjà été attribué à ce client."
        )

    mouvement = _creer_transaction(
        fournisseur=fournisseur,
        client=client,
        type_transaction=TransactionFinanciere.Type.ATTRIBUTION_INITIALE,
        sens=TransactionFinanciere.Sens.CREDIT,
        montant=montant,
        auteur=auteur,
        commentaire=commentaire,
        cle_idempotence=cle_idempotence,
    )
    _appliquer_mouvement(client, mouvement.sens, mouvement.montant)
    return mouvement


@transaction.atomic
def enregistrer_encaissement_financier(encaissement, agent, appliquer_solde=True):
    from apps.encaissements.models import Encaissement

    encaissement = (
        Encaissement.objects.select_for_update()
        .select_related("client", "client__fournisseur")
        .get(pk=encaissement.pk)
    )
    existante = TransactionFinanciere.objects.filter(
        encaissement=encaissement
    ).first()
    if existante is not None:
        return existante
    if encaissement.statut != Encaissement.Statut.CONFIRME:
        raise EtatInvalide(
            "Seul un encaissement confirmé peut être comptabilisé."
        )
    if agent.fournisseur_id != encaissement.client.fournisseur_id:
        raise PermissionDenied(
            "Cet agent ne peut pas comptabiliser cet encaissement."
        )

    client = Client.objects.select_for_update().get(pk=encaissement.client_id)
    mouvement = _creer_transaction(
        fournisseur=encaissement.client.fournisseur,
        client=client,
        agent=agent,
        encaissement=encaissement,
        type_transaction=TransactionFinanciere.Type.ENCAISSEMENT,
        sens=TransactionFinanciere.Sens.DEBIT,
        montant=encaissement.montant,
        auteur=agent.utilisateur,
        commentaire=encaissement.commentaire,
    )
    if appliquer_solde:
        _appliquer_mouvement(client, mouvement.sens, mouvement.montant)
    return mouvement


@transaction.atomic
def creer_demande_decaissement(client, montant, commentaire="", cle_idempotence=None):
    montant = _montant_valide(montant)
    client = (
        Client.objects.select_for_update()
        .select_related("fournisseur", "utilisateur")
        .get(pk=client.pk)
    )
    if not client.compte_active:
        raise ValidationError(
            {"detail": "Le compte client doit être activé pour demander un décaissement."}
        )

    if cle_idempotence:
        existante = DemandeDecaissement.objects.filter(
            cle_idempotence=cle_idempotence,
            client=client,
        ).first()
        if existante is not None:
            return existante

    disponible = solde_disponible_client(client)
    if montant > disponible:
        raise ValidationError(
            {
                "montant": (
                    "Le montant demandé dépasse le solde disponible "
                    f"({disponible})."
                )
            }
        )

    return DemandeDecaissement.objects.create(
        fournisseur=client.fournisseur,
        client=client,
        montant=montant,
        commentaire=commentaire,
        cle_idempotence=cle_idempotence or None,
    )


@transaction.atomic
def confirmer_demande_decaissement(demande, agent):
    demande = DemandeDecaissement.objects.select_for_update().get(pk=demande.pk)
    if demande.statut != DemandeDecaissement.Statut.EN_ATTENTE:
        raise EtatInvalide(
            "Seule une demande en attente peut être confirmée par l'agent."
        )
    if agent.fournisseur_id != demande.fournisseur_id:
        raise PermissionDenied(
            "Cet agent ne peut pas confirmer une demande d'un autre fournisseur."
        )
    demande.agent = agent
    demande.statut = DemandeDecaissement.Statut.CONFIRMATION_AGENT
    demande.date_confirmation_agent = timezone.now()
    demande.confirme_par = agent.utilisateur
    demande.save(
        update_fields=[
            "agent",
            "statut",
            "date_confirmation_agent",
            "confirme_par",
        ]
    )
    return demande


@transaction.atomic
def approuver_demande_decaissement(demande, fournisseur):
    demande = DemandeDecaissement.objects.select_for_update().get(pk=demande.pk)
    if demande.fournisseur_id != fournisseur.pk:
        raise PermissionDenied(
            "Vous ne pouvez pas approuver une demande d'un autre fournisseur."
        )
    if demande.statut not in (
        DemandeDecaissement.Statut.CONFIRMATION_AGENT,
        DemandeDecaissement.Statut.EN_ATTENTE_VALIDATION,
    ):
        raise EtatInvalide(
            "La demande doit d'abord être confirmée par un agent."
        )
    demande.statut = DemandeDecaissement.Statut.APPROUVE
    demande.date_decision = timezone.now()
    demande.decide_par = fournisseur.utilisateur
    demande.save(update_fields=["statut", "date_decision", "decide_par"])
    return demande


@transaction.atomic
def payer_demande_decaissement(demande, fournisseur):
    demande = (
        DemandeDecaissement.objects.select_for_update()
        .select_related("client", "fournisseur")
        .get(pk=demande.pk)
    )
    if demande.fournisseur_id != fournisseur.pk:
        raise PermissionDenied(
            "Vous ne pouvez pas payer une demande d'un autre fournisseur."
        )
    if demande.statut != DemandeDecaissement.Statut.APPROUVE:
        raise EtatInvalide(
            "Seule une demande approuvée peut être payée."
        )

    client = Client.objects.select_for_update().get(pk=demande.client_id)
    if (client.solde_du or Decimal("0")) < demande.montant:
        raise ValidationError(
            {"detail": "Le solde du client est insuffisant pour ce décaissement."}
        )

    mouvement = _creer_transaction(
        fournisseur=demande.fournisseur,
        client=client,
        type_transaction=TransactionFinanciere.Type.DECAISSEMENT,
        sens=TransactionFinanciere.Sens.DEBIT,
        montant=demande.montant,
        auteur=fournisseur.utilisateur,
        decaissement=demande,
        commentaire=demande.commentaire,
    )
    _appliquer_mouvement(client, mouvement.sens, mouvement.montant)

    demande.statut = DemandeDecaissement.Statut.PAYE
    demande.date_paiement = timezone.now()
    demande.paye_par = fournisseur.utilisateur
    demande.save(update_fields=["statut", "date_paiement", "paye_par"])
    return demande


@transaction.atomic
def refuser_demande_decaissement(demande, fournisseur, motif):
    demande = DemandeDecaissement.objects.select_for_update().get(pk=demande.pk)
    if demande.fournisseur_id != fournisseur.pk:
        raise PermissionDenied(
            "Vous ne pouvez pas refuser une demande d'un autre fournisseur."
        )
    if demande.statut in (
        DemandeDecaissement.Statut.PAYE,
        DemandeDecaissement.Statut.REFUSE,
        DemandeDecaissement.Statut.ANNULE,
    ):
        raise EtatInvalide("Cette demande ne peut plus être refusée.")
    demande.statut = DemandeDecaissement.Statut.REFUSE
    demande.motif_refus = motif
    demande.date_decision = timezone.now()
    demande.decide_par = fournisseur.utilisateur
    demande.save(
        update_fields=[
            "statut",
            "motif_refus",
            "date_decision",
            "decide_par",
        ]
    )
    return demande


@transaction.atomic
def annuler_demande_decaissement(demande, utilisateur):
    demande = DemandeDecaissement.objects.select_for_update().get(pk=demande.pk)
    est_client = (
        hasattr(utilisateur, "client_profile")
        and demande.client_id == utilisateur.client_profile.pk
    )
    est_fournisseur = (
        hasattr(utilisateur, "fournisseur_profile")
        and demande.fournisseur_id == utilisateur.fournisseur_profile.pk
    )
    if not (est_client or est_fournisseur):
        raise PermissionDenied("Vous ne pouvez pas annuler cette demande.")
    if demande.statut in (
        DemandeDecaissement.Statut.PAYE,
        DemandeDecaissement.Statut.REFUSE,
        DemandeDecaissement.Statut.ANNULE,
    ):
        raise EtatInvalide("Cette demande ne peut plus être annulée.")
    demande.statut = DemandeDecaissement.Statut.ANNULE
    demande.save(update_fields=["statut"])
    return demande


def _fournisseur_de(utilisateur):
    return getattr(utilisateur, "fournisseur_profile", None)


def transactions_accessibles_par(utilisateur):
    if hasattr(utilisateur, "fournisseur_profile"):
        return TransactionFinanciere.objects.filter(
            fournisseur=utilisateur.fournisseur_profile
        )
    if hasattr(utilisateur, "agent_profile"):
        return TransactionFinanciere.objects.filter(
            agent=utilisateur.agent_profile
        )
    if hasattr(utilisateur, "client_profile"):
        return TransactionFinanciere.objects.filter(
            client=utilisateur.client_profile
        )
    return TransactionFinanciere.objects.none()


def demandes_accessibles_par(utilisateur):
    if hasattr(utilisateur, "fournisseur_profile"):
        return DemandeDecaissement.objects.filter(
            fournisseur=utilisateur.fournisseur_profile
        )
    if hasattr(utilisateur, "agent_profile"):
        return DemandeDecaissement.objects.filter(
            fournisseur=utilisateur.agent_profile.fournisseur
        )
    if hasattr(utilisateur, "client_profile"):
        return DemandeDecaissement.objects.filter(
            client=utilisateur.client_profile
        )
    return DemandeDecaissement.objects.none()


@transaction.atomic
def enregistrer_regularisation(
    fournisseur,
    client,
    montant,
    sens,
    motif,
    auteur=None,
    cle_idempotence=None,
):
    montant = _montant_valide(montant)
    if sens not in TransactionFinanciere.Sens.values:
        raise ValidationError({"sens": "Sens de régularisation invalide."})
    client = (
        Client.objects.select_for_update()
        .select_related("fournisseur")
        .get(pk=client.pk)
    )
    if client.fournisseur_id != fournisseur.pk:
        raise PermissionDenied(
            "Vous ne pouvez pas régulariser un client d'un autre fournisseur."
        )
    if cle_idempotence:
        existante = TransactionFinanciere.objects.filter(
            cle_idempotence=cle_idempotence
        ).first()
        if existante is not None:
            return existante
    mouvement = _creer_transaction(
        fournisseur=fournisseur,
        client=client,
        type_transaction=TransactionFinanciere.Type.REGULARISATION,
        sens=sens,
        montant=montant,
        auteur=auteur,
        commentaire=motif,
        cle_idempotence=cle_idempotence,
    )
    _appliquer_mouvement(client, mouvement.sens, mouvement.montant)
    return mouvement
