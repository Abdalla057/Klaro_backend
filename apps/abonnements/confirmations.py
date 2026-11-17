"""Confirmation atomique des demandes d'abonnement.

Ce module contient le composant financier le plus sensible du système. Il est
le seul chemin autorisé pour transformer une demande vérifiée en abonnement
actif et en paiement confirmé.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .exceptions import AnomaliePaiement
from .models import (
    Abonnement,
    DemandeAbonnement,
    PaiementAbonnement,
)
from .demandes import journaliser_demande
from .services import _journaliser

STATUTS_PAIEMENT_EN_COURS = (
    PaiementAbonnement.Statut.INITIE,
    PaiementAbonnement.Statut.EN_ATTENTE,
    PaiementAbonnement.Statut.EN_VERIFICATION,
)


def verifier_eligibilite_confirmation(
    demande,
    reference_transaction="",
    provider_transaction_id="",
    preuve_paiement=None,
):
    """Vérifie toutes les préconditions avant toute écriture financière."""
    if demande.statut == DemandeAbonnement.Statut.CONFIRMEE:
        return
    if demande.statut not in DemandeAbonnement.STATUTS_TRAITABLES:
        raise ValidationError(
            "Cette demande n'est plus dans un état permettant sa confirmation."
        )
    if demande.est_expiree:
        raise ValidationError(
            "Cette demande a expiré. Une nouvelle demande est nécessaire."
        )
    if demande.montant is None or demande.montant <= 0:
        raise ValidationError("Le montant de la demande est invalide.")
    if not demande.devise:
        raise ValidationError("La devise de la demande est inconnue.")
    reference = (reference_transaction or demande.reference_transaction or "").strip()
    reference_prestataire = (provider_transaction_id or "").strip()
    preuve = preuve_paiement if preuve_paiement is not None else demande.preuve_paiement
    if not reference and not reference_prestataire and not preuve:
        raise ValidationError(
            "Un paiement confirmé doit être justifié par une référence de "
            "transaction, un identifiant prestataire ou un justificatif de "
            "paiement."
        )
    if demande.abonnement_genere_id:
        raise ValidationError("Un abonnement a déjà été généré pour cette demande.")
    if demande.paiements.filter(
        statut=PaiementAbonnement.Statut.CONFIRME
    ).exists():
        raise ValidationError("Un paiement confirmé existe déjà pour cette demande.")

    if demande.type_operation == DemandeAbonnement.TypeOperation.RENOUVELLEMENT:
        cible = demande.abonnement_cible
        if cible is None:
            raise ValidationError(
                "La demande de renouvellement ne cible aucun abonnement."
            )
        if cible.fournisseur_id != demande.fournisseur_id:
            raise ValidationError(
                "L'abonnement ciblé n'appartient pas au fournisseur de la demande."
            )
        if cible.statut == Abonnement.Statut.ANNULE:
            raise ValidationError("L'abonnement ciblé est annulé.")
        if (
            cible.statut == Abonnement.Statut.ACTIF
            and cible.date_fin > timezone.now()
            and cible.plan_id != demande.plan_id
        ):
            raise ValidationError(
                "Le plan ne peut être modifié qu'à l'expiration de l'abonnement."
            )
    else:
        abonnement_payant_actif = (
            Abonnement.objects.select_for_update()
            .filter(
                fournisseur_id=demande.fournisseur_id,
                statut=Abonnement.Statut.ACTIF,
            )
            .exclude(est_essai=True)
            .first()
        )
        if abonnement_payant_actif is not None:
            raise ValidationError(
                "Un abonnement payant est déjà actif pour ce fournisseur."
            )


def verifier_unicite_transaction(
    prestataire="",
    provider_transaction_id="",
    reference_transaction="",
    demande=None,
):
    """Empêche qu'une même transaction externe alimente deux demandes."""
    if prestataire and provider_transaction_id:
        existant = PaiementAbonnement.objects.filter(
            prestataire=prestataire,
            provider_transaction_id=provider_transaction_id,
            statut=PaiementAbonnement.Statut.CONFIRME,
        )
        if demande is not None:
            existant = existant.exclude(demande_abonnement=demande)
        if existant.exists():
            raise AnomaliePaiement(
                {
                    "detail": "Cette transaction a déjà été confirmée.",
                    "code": "transaction_deja_confirmee",
                }
            )

    if reference_transaction:
        existant = PaiementAbonnement.objects.filter(
            reference_transaction=reference_transaction,
            statut=PaiementAbonnement.Statut.CONFIRME,
        )
        if demande is not None:
            existant = existant.exclude(demande_abonnement=demande)
        if existant.exists():
            raise AnomaliePaiement(
                {
                    "detail": "Cette référence de transaction est déjà utilisée.",
                    "code": "reference_transaction_deja_utilisee",
                }
            )


def _promouvoir_ou_creer_paiement_confirme(
    demande,
    abonnement,
    reference_transaction="",
    prestataire="",
    provider_transaction_id="",
    moyen_paiement="",
    effectue_par=None,
    date_paiement=None,
    motif="",
):
    """Crée ou promeut le paiement en cours, avec un seul paiement confirmé."""
    paiement = (
        PaiementAbonnement.objects.select_for_update()
        .filter(
            demande_abonnement=demande,
            statut__in=STATUTS_PAIEMENT_EN_COURS,
        )
        .order_by("-date_creation", "-id")
        .first()
    )
    maintenant = date_paiement or timezone.now()

    if paiement is not None:
        paiement.abonnement = abonnement
        paiement.statut = PaiementAbonnement.Statut.CONFIRME
        paiement.date_confirmation = maintenant
        paiement.confirme_par = effectue_par
        if reference_transaction:
            paiement.reference_transaction = reference_transaction
        if prestataire:
            paiement.prestataire = prestataire
        if provider_transaction_id:
            paiement.provider_transaction_id = provider_transaction_id
        if moyen_paiement:
            paiement.moyen_paiement = moyen_paiement
        if motif:
            paiement.commentaire = f"{paiement.commentaire}\n{motif}".strip()
        paiement.save(
            update_fields=[
                "abonnement",
                "statut",
                "date_confirmation",
                "confirme_par",
                "reference_transaction",
                "prestataire",
                "provider_transaction_id",
                "moyen_paiement",
                "commentaire",
                "date_modification",
            ]
        )
        return paiement

    return PaiementAbonnement.objects.create(
        demande_abonnement=demande,
        abonnement=abonnement,
        fournisseur=demande.fournisseur,
        plan=demande.plan,
        montant=demande.montant,
        devise=demande.devise,
        statut=PaiementAbonnement.Statut.CONFIRME,
        type_operation=demande.type_operation,
        moyen_paiement=moyen_paiement or demande.moyen_paiement,
        prestataire=prestataire or demande.prestataire,
        provider_transaction_id=provider_transaction_id,
        reference_transaction=reference_transaction or demande.reference_transaction,
        date_initiation=demande.date_creation,
        date_confirmation=maintenant,
        confirme_par=effectue_par,
        commentaire=motif,
    )


def _cloturer_essai_actif(fournisseur, effectue_par=None):
    for essai in (
        Abonnement.objects.select_for_update()
        .filter(
            fournisseur=fournisseur,
            statut=Abonnement.Statut.ACTIF,
            est_essai=True,
        )
        .select_related("plan")
    ):
        ancien_statut = essai.statut
        essai.statut = Abonnement.Statut.EXPIRE
        essai.save(update_fields=["statut", "date_modification"])
        _journaliser(
            essai,
            "EXPIRATION",
            ancien_statut=ancien_statut,
            nouveau_statut=Abonnement.Statut.EXPIRE,
            motif="Fin de la période d'essai avant activation d'un plan payant.",
            effectue_par=effectue_par,
        )


def activer_ou_renouveler_abonnement(demande, effectue_par=None):
    """Crée ou prolonge l'abonnement après confirmation financière.

    - Renouvellement d'un abonnement actif identique : prolongation de la date
      de fin sans créer de nouvelle ligne.
    - Renouvellement d'un abonnement expiré/suspendu : création d'un nouvel
      abonnement actif.
    - Souscription : clôture de l'essai éventuel puis création du plan payant.
    """
    plan = demande.plan
    maintenant = timezone.now()

    if demande.type_operation == DemandeAbonnement.TypeOperation.RENOUVELLEMENT:
        cible = (
            Abonnement.objects.select_for_update()
            .select_related("plan")
            .get(pk=demande.abonnement_cible_id)
        )
        cible.actualiser_statut(effectue_par=effectue_par)

        if cible.statut == Abonnement.Statut.ACTIF:
            cible.date_fin = cible.date_fin + timedelta(days=plan.duree_jours)
            cible.reference_paiement = (
                demande.reference_transaction or cible.reference_paiement
            )
            cible.save(
                update_fields=[
                    "date_fin",
                    "reference_paiement",
                    "date_modification",
                ]
            )
            _journaliser(
                cible,
                "RENOUVELLEMENT",
                ancien_statut=Abonnement.Statut.ACTIF,
                nouveau_statut=Abonnement.Statut.ACTIF,
                motif=f"Renouvellement confirmé de la demande {demande.reference}.",
                effectue_par=effectue_par,
                montant=demande.montant,
                reference_paiement=demande.reference_transaction,
            )
            return cible

        if cible.statut == Abonnement.Statut.SUSPENDU:
            ancien_statut = cible.statut
            cible.statut = Abonnement.Statut.EXPIRE
            cible.save(update_fields=["statut", "date_modification"])
            _journaliser(
                cible,
                "EXPIRATION",
                ancien_statut=ancien_statut,
                nouveau_statut=Abonnement.Statut.EXPIRE,
                motif="Abonnement suspendu clôturé par un renouvellement confirmé.",
                effectue_par=effectue_par,
            )

    else:
        _cloturer_essai_actif(demande.fournisseur, effectue_par=effectue_par)

    abonnement = Abonnement.objects.create(
        fournisseur=demande.fournisseur,
        plan=plan,
        date_debut=maintenant,
        date_fin=maintenant + timedelta(days=plan.duree_jours),
        statut=Abonnement.Statut.ACTIF,
        est_essai=False,
        montant=demande.montant,
        devise=demande.devise,
        reference_paiement=demande.reference_transaction,
        notes=f"Demande {demande.reference}",
        cree_par=effectue_par,
    )
    _journaliser(
        abonnement,
        "CREATION",
        nouveau_statut=Abonnement.Statut.ACTIF,
        motif=f"Activation confirmée de la demande {demande.reference}.",
        effectue_par=effectue_par,
        montant=demande.montant,
        reference_paiement=demande.reference_transaction,
    )
    return abonnement


@transaction.atomic
def confirmer_demande_abonnement(
    demande,
    reference_transaction="",
    prestataire="",
    provider_transaction_id="",
    moyen_paiement="",
    effectue_par=None,
    motif="",
    date_paiement=None,
):
    """Confirme une demande et active/prolonge l'abonnement, atomiquement.

    L'appel est idempotent : une demande déjà confirmée retourne le paiement
    existant sans nouvelle activation ni prolongation.
    """
    demande = (
        DemandeAbonnement.objects.select_for_update()
        .select_related("fournisseur", "plan")
        .get(pk=demande.pk)
    )

    if demande.statut == DemandeAbonnement.Statut.CONFIRMEE:
        paiement = demande.paiements.filter(
            statut=PaiementAbonnement.Statut.CONFIRME
        ).first()
        return demande, paiement, False

    verifier_eligibilite_confirmation(
        demande,
        reference_transaction=reference_transaction,
        provider_transaction_id=provider_transaction_id,
    )
    verifier_unicite_transaction(
        prestataire=prestataire or demande.prestataire,
        provider_transaction_id=provider_transaction_id,
        reference_transaction=(
            reference_transaction or demande.reference_transaction
        ),
        demande=demande,
    )

    if demande.abonnement_cible_id:
        Abonnement.objects.select_for_update().get(
            pk=demande.abonnement_cible_id
        )

    abonnement = activer_ou_renouveler_abonnement(
        demande,
        effectue_par=effectue_par,
    )
    paiement = _promouvoir_ou_creer_paiement_confirme(
        demande,
        abonnement,
        reference_transaction=reference_transaction,
        prestataire=prestataire,
        provider_transaction_id=provider_transaction_id,
        moyen_paiement=moyen_paiement,
        effectue_par=effectue_par,
        date_paiement=date_paiement,
        motif=motif,
    )

    ancien_statut = demande.statut
    demande.statut = DemandeAbonnement.Statut.CONFIRMEE
    demande.abonnement_genere = abonnement
    demande.traite_par = effectue_par
    demande.date_traitement = timezone.now()
    if reference_transaction:
        demande.reference_transaction = reference_transaction
    if prestataire:
        demande.prestataire = prestataire
    if motif:
        demande.motif_traitement = motif
    demande.save(
        update_fields=[
            "statut",
            "abonnement_genere",
            "traite_par",
            "date_traitement",
            "reference_transaction",
            "prestataire",
            "motif_traitement",
            "date_modification",
        ]
    )

    journaliser_demande(
        demande,
        "PAIEMENT_CONFIRME",
        ancien_statut=ancien_statut,
        nouveau_statut=demande.statut,
        motif=motif or "Paiement confirmé.",
        effectue_par=effectue_par,
        montant=demande.montant,
        reference_paiement=demande.reference_transaction or str(paiement.reference),
        metadonnees={
            "paiement": str(paiement.reference),
            "abonnement": abonnement.pk,
            "prestataire": prestataire or demande.prestataire,
        },
    )

    return demande, paiement, True
