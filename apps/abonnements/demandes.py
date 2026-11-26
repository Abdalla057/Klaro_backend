"""Services métier du cycle de vie des demandes d'abonnement.

Toute création, soumission de preuve, annulation ou expiration passe par ce
module. Les vues ne doivent jamais manipuler directement les statuts.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.fournisseurs.constants import Pays, prestataires_pour_pays
from apps.fournisseurs.models import Fournisseur

from .exceptions import PaiementEnLigneDesactive, PrestataireNonConfigure
from .models import (
    Abonnement,
    DemandeAbonnement,
    EvenementAbonnement,
    PaiementAbonnement,
    PlanAbonnement,
)
from .tarifs import calculer_montant_plan


def _duree_expiration():
    """Délai de validité d'une demande, configurable par environnement."""
    heures = getattr(settings, "ABONNEMENT_DEMANDE_EXPIRATION_HEURES", 24)
    return timedelta(hours=heures)


INITIATION_EN_COURS = "INITIATION_EN_COURS"
DELAI_INITIATION_EXPIREE = timedelta(minutes=5)


def journaliser_demande(
    demande,
    type_evenement,
    ancien_statut="",
    nouveau_statut="",
    motif="",
    effectue_par=None,
    montant=None,
    reference_paiement="",
    identifiant_correlation="",
    metadonnees=None,
):
    """Écrit un événement de journal rattaché à une demande."""
    return EvenementAbonnement.objects.create(
        demande_abonnement=demande,
        type_evenement=type_evenement,
        ancien_statut=ancien_statut,
        nouveau_statut=nouveau_statut,
        motif=motif,
        effectue_par=effectue_par,
        montant=montant,
        reference_paiement=reference_paiement,
        identifiant_correlation=identifiant_correlation or str(demande.pk),
        metadonnees=metadonnees or {},
    )


def _verifier_plan_souscriptible(plan):
    if not plan.actif:
        raise ValidationError("Ce plan est inactif et ne peut pas être souscrit.")
    if (plan.devise or "").upper() != "XOF":
        raise ValidationError(
            "Les plans doivent être exprimés en XOF, devise de référence."
        )
    if plan.est_essai:
        raise ValidationError(
            "L'essai gratuit est attribué automatiquement à l'inscription. "
            "Choisissez un plan payant."
        )
    if plan.prix is None or plan.prix <= 0:
        raise ValidationError("Ce plan ne peut pas faire l'objet d'un paiement.")


def _verifier_absence_abonnement_payant_actif(fournisseur, effectue_par=None):
    abonnement = (
        Abonnement.objects.select_for_update()
        .filter(fournisseur=fournisseur, statut=Abonnement.Statut.ACTIF)
        .select_related("plan")
        .first()
    )
    if abonnement is None:
        return
    abonnement.actualiser_statut(effectue_par=effectue_par)
    if abonnement.statut == Abonnement.Statut.ACTIF and not abonnement.est_essai:
        raise ValidationError(
            "Un abonnement payant est déjà actif. "
            "Utilisez le renouvellement pour le prolonger."
        )


def _verifier_renouvellement(fournisseur, plan, abonnement_cible, effectue_par=None):
    abonnement_cible = (
        Abonnement.objects.select_for_update()
        .select_related("fournisseur", "plan")
        .get(pk=abonnement_cible.pk)
    )
    if abonnement_cible.fournisseur_id != fournisseur.pk:
        raise ValidationError(
            "L'abonnement ciblé n'appartient pas à ce fournisseur."
        )
    if abonnement_cible.statut == Abonnement.Statut.ANNULE:
        raise ValidationError(
            "Un abonnement annulé ne peut pas être renouvelé. Souscrivez un nouveau plan."
        )
    abonnement_cible.actualiser_statut(effectue_par=effectue_par)

    if abonnement_cible.statut == Abonnement.Statut.ACTIF:
        if plan.pk != abonnement_cible.plan_id:
            raise ValidationError(
                "Le plan ne peut être modifié qu'à l'expiration de l'abonnement."
            )

    deja_en_cours = DemandeAbonnement.objects.filter(
        abonnement_cible=abonnement_cible,
        type_operation=DemandeAbonnement.TypeOperation.RENOUVELLEMENT,
        statut__in=DemandeAbonnement.STATUTS_TRAITABLES,
    ).exists()
    if deja_en_cours:
        raise ValidationError(
            "Une demande de renouvellement est déjà en cours pour cet abonnement."
        )
    return abonnement_cible


@transaction.atomic
def creer_demande_abonnement(
    fournisseur,
    plan,
    cle_idempotence,
    type_operation=DemandeAbonnement.TypeOperation.SOUSCRIPTION,
    abonnement_cible=None,
    moyen_paiement="",
    prestataire="",
    reference_transaction="",
    preuve_paiement=None,
    commentaire_fournisseur="",
    cree_par=None,
    date_expiration=None,
):
    """Crée une demande après avoir figé côté serveur le montant attendu.

    La clé d'idempotence est unique par fournisseur : un double clic ou un
    nouvel envoi de la même requête retourne la demande initiale.
    """
    if not cle_idempotence:
        raise ValidationError(
            {"cle_idempotence": "Une clé d'idempotence est obligatoire."}
        )

    fournisseur = Fournisseur.objects.select_for_update().get(pk=fournisseur.pk)
    _verifier_plan_souscriptible(plan)

    existante = (
        DemandeAbonnement.objects.select_for_update()
        .filter(
            fournisseur=fournisseur,
            cle_idempotence=cle_idempotence,
        )
        .first()
    )
    if existante is not None:
        return existante

    if type_operation == DemandeAbonnement.TypeOperation.RENOUVELLEMENT:
        if abonnement_cible is None:
            raise ValidationError(
                {"abonnement_cible": "Un renouvellement doit cibler un abonnement."}
            )
        abonnement_cible = _verifier_renouvellement(
            fournisseur,
            plan,
            abonnement_cible,
            effectue_par=cree_par,
        )
    elif type_operation == DemandeAbonnement.TypeOperation.SOUSCRIPTION:
        abonnement_cible = None
        _verifier_absence_abonnement_payant_actif(
            fournisseur,
            effectue_par=cree_par,
        )
    else:
        raise ValidationError(
            {"type_operation": "Type d'opération non pris en charge."}
        )

    maintenant = timezone.now()
    tarification = calculer_montant_plan(plan, fournisseur.pays)
    demande = DemandeAbonnement.objects.create(
        fournisseur=fournisseur,
        plan=plan,
        pays=tarification["pays"],
        type_operation=type_operation,
        abonnement_cible=abonnement_cible,
        montant=tarification["montant"],
        montant_reference=tarification["montant_reference"],
        taux_conversion=tarification["taux_conversion"],
        devise=tarification["devise"],
        moyen_paiement=moyen_paiement or "",
        prestataire=prestataire or "",
        reference_transaction=reference_transaction or "",
        preuve_paiement=preuve_paiement,
        statut=DemandeAbonnement.Statut.EN_ATTENTE,
        cle_idempotence=cle_idempotence,
        commentaire_fournisseur=commentaire_fournisseur or "",
        cree_par=cree_par,
        date_expiration=date_expiration or (maintenant + _duree_expiration()),
    )
    journaliser_demande(
        demande,
        EvenementAbonnement.Type.DEMANDE_CREEE,
        nouveau_statut=DemandeAbonnement.Statut.EN_ATTENTE,
        motif="Création de la demande d'abonnement.",
        effectue_par=cree_par,
        montant=demande.montant,
        metadonnees={
            "type_operation": demande.type_operation,
            "plan": plan.code,
            "pays": demande.pays,
            "devise": demande.devise,
            "montant_reference": str(demande.montant_reference),
            "taux_conversion": str(demande.taux_conversion),
        },
    )
    return demande


@transaction.atomic
def soumettre_preuve_paiement(
    demande,
    reference_transaction="",
    preuve_paiement=None,
    moyen_paiement="",
    prestataire="",
    commentaire="",
    effectue_par=None,
):
    """Place une demande en vérification après soumission du paiement manuel.

    Un simple justificatif ne confirme jamais le paiement : la demande reste
    en vérification jusqu'à la décision du super administrateur.
    """
    demande = (
        DemandeAbonnement.objects.select_for_update()
        .select_related("fournisseur", "plan")
        .get(pk=demande.pk)
    )
    if demande.statut not in DemandeAbonnement.STATUTS_TRAITABLES:
        raise ValidationError(
            "Cette demande ne peut plus recevoir de preuve de paiement."
        )
    if not reference_transaction and not preuve_paiement:
        raise ValidationError(
            "Fournissez au moins une référence de transaction ou un justificatif."
        )
    if demande.est_expiree:
        raise ValidationError("Cette demande a expiré. Créez une nouvelle demande.")

    if reference_transaction:
        demande.reference_transaction = reference_transaction
    if preuve_paiement:
        demande.preuve_paiement = preuve_paiement
    if moyen_paiement:
        demande.moyen_paiement = moyen_paiement
    if prestataire:
        demande.prestataire = prestataire
    if commentaire:
        demande.commentaire_fournisseur = commentaire

    ancien_statut = demande.statut
    demande.statut = DemandeAbonnement.Statut.EN_VERIFICATION
    demande.save(
        update_fields=[
            "reference_transaction",
            "preuve_paiement",
            "moyen_paiement",
            "prestataire",
            "commentaire_fournisseur",
            "statut",
            "date_modification",
        ]
    )
    journaliser_demande(
        demande,
        EvenementAbonnement.Type.PAIEMENT_SOUMIS,
        ancien_statut=ancien_statut,
        nouveau_statut=demande.statut,
        motif="Preuve de paiement soumise par le fournisseur.",
        effectue_par=effectue_par,
        montant=demande.montant,
        reference_paiement=demande.reference_transaction,
    )
    return demande


def _annuler_paiements_en_cours(demande, statut, motif, effectue_par=None):
    paiements = PaiementAbonnement.objects.select_for_update().filter(
        demande_abonnement=demande,
        statut__in=[
            PaiementAbonnement.Statut.INITIE,
            PaiementAbonnement.Statut.EN_ATTENTE,
            PaiementAbonnement.Statut.EN_VERIFICATION,
        ],
    )
    for paiement in paiements:
        paiement.statut = statut
        paiement.commentaire = f"{paiement.commentaire}\n{motif}".strip()
        paiement.save(update_fields=["statut", "commentaire", "date_modification"])


@transaction.atomic
def annuler_demande_abonnement(demande, motif="", effectue_par=None):
    """Annulation par le fournisseur, uniquement avant décision définitive."""
    demande = (
        DemandeAbonnement.objects.select_for_update()
        .select_related("fournisseur", "plan")
        .get(pk=demande.pk)
    )
    if demande.statut == DemandeAbonnement.Statut.ANNULEE:
        return demande
    if not demande.est_annulable:
        raise ValidationError("Cette demande ne peut plus être annulée.")

    ancien_statut = demande.statut
    demande.statut = DemandeAbonnement.Statut.ANNULEE
    demande.traite_par = effectue_par
    demande.date_traitement = timezone.now()
    demande.motif_traitement = motif or "Annulation par le fournisseur."
    demande.save(
        update_fields=[
            "statut",
            "traite_par",
            "date_traitement",
            "motif_traitement",
            "date_modification",
        ]
    )
    _annuler_paiements_en_cours(
        demande,
        PaiementAbonnement.Statut.ANNULE,
        "Annulation de la demande.",
        effectue_par=effectue_par,
    )
    journaliser_demande(
        demande,
        EvenementAbonnement.Type.DEMANDE_ANNULEE,
        ancien_statut=ancien_statut,
        nouveau_statut=demande.statut,
        motif=demande.motif_traitement,
        effectue_par=effectue_par,
        montant=demande.montant,
    )
    return demande


@transaction.atomic
def refuser_demande_abonnement(demande, motif, effectue_par=None):
    """Refus administratif : aucun abonnement n'est créé ni prolongé."""
    if not motif:
        raise ValidationError({"motif": "Le motif du refus est obligatoire."})
    demande = (
        DemandeAbonnement.objects.select_for_update()
        .select_related("fournisseur", "plan")
        .get(pk=demande.pk)
    )
    if demande.statut == DemandeAbonnement.Statut.REFUSEE:
        return demande
    if demande.statut not in DemandeAbonnement.STATUTS_TRAITABLES:
        raise ValidationError("Cette demande ne peut plus être refusée.")

    ancien_statut = demande.statut
    demande.statut = DemandeAbonnement.Statut.REFUSEE
    demande.traite_par = effectue_par
    demande.date_traitement = timezone.now()
    demande.motif_traitement = motif
    demande.save(
        update_fields=[
            "statut",
            "traite_par",
            "date_traitement",
            "motif_traitement",
            "date_modification",
        ]
    )
    _annuler_paiements_en_cours(
        demande,
        PaiementAbonnement.Statut.REFUSE,
        f"Refus de la demande : {motif}",
        effectue_par=effectue_par,
    )
    journaliser_demande(
        demande,
        EvenementAbonnement.Type.DEMANDE_REFUSEE,
        ancien_statut=ancien_statut,
        nouveau_statut=demande.statut,
        motif=motif,
        effectue_par=effectue_par,
        montant=demande.montant,
    )
    journaliser_demande(
        demande,
        EvenementAbonnement.Type.PAIEMENT_REFUSE,
        ancien_statut=ancien_statut,
        nouveau_statut=demande.statut,
        motif=motif,
        effectue_par=effectue_par,
        montant=demande.montant,
        reference_paiement=demande.reference_transaction,
    )
    return demande


def expirer_demandes_abonnement(effectue_par=None):
    """Expire les demandes non payées dont la date limite est dépassée."""
    maintenant = timezone.now()
    identifiants = list(
        DemandeAbonnement.objects.filter(
            statut__in=DemandeAbonnement.STATUTS_TRAITABLES,
            date_expiration__isnull=False,
            date_expiration__lte=maintenant,
        ).values_list("pk", flat=True)
    )
    expirees = 0
    for identifiant in identifiants:
        with transaction.atomic():
            demande = (
                DemandeAbonnement.objects.select_for_update()
                .filter(pk=identifiant)
                .first()
            )
            if (
                demande is None
                or demande.statut not in DemandeAbonnement.STATUTS_TRAITABLES
                or demande.date_expiration is None
                or demande.date_expiration > maintenant
            ):
                continue
            ancien_statut = demande.statut
            demande.statut = DemandeAbonnement.Statut.EXPIREE
            demande.date_traitement = maintenant
            demande.motif_traitement = "Délai de paiement dépassé."
            demande.save(
                update_fields=[
                    "statut",
                    "date_traitement",
                    "motif_traitement",
                    "date_modification",
                ]
            )
            _annuler_paiements_en_cours(
                demande,
                PaiementAbonnement.Statut.ECHEC,
                "Demande expirée avant confirmation.",
                effectue_par=effectue_par,
            )
            journaliser_demande(
                demande,
                EvenementAbonnement.Type.DEMANDE_EXPIREE,
                ancien_statut=ancien_statut,
                nouveau_statut=demande.statut,
                motif=demande.motif_traitement,
                effectue_par=effectue_par,
                montant=demande.montant,
            )
            expirees += 1
    return expirees


def paiement_en_ligne_actif_pour_pays(pays):
    if not getattr(settings, "ABONNEMENT_PAIEMENT_EN_LIGNE_ACTIF", False):
        return False
    pays_actifs = getattr(
        settings,
        "ABONNEMENT_PAIEMENT_PAYS_ACTIFS",
        {Pays.SENEGAL},
    )
    if isinstance(pays_actifs, str):
        pays_actifs = {
            valeur.strip().upper()
            for valeur in pays_actifs.split(",")
            if valeur.strip()
        }
    return str(pays) in {str(valeur).upper() for valeur in pays_actifs}


def initier_paiement_en_ligne(demande, prestataire_code, effectue_par=None):
    """Initie un paiement chez un prestataire et historise l'opération.

    Le retour du navigateur ne vaut jamais confirmation : seule la vérification
    serveur à serveur (webhook signé ou réconciliation) peut confirmer.
    """
    from .paiements_en_ligne import (
        normaliser_code_prestataire,
        obtenir_prestataire,
    )

    if not getattr(settings, "ABONNEMENT_PAIEMENT_EN_LIGNE_ACTIF", False):
        raise PaiementEnLigneDesactive()

    code = normaliser_code_prestataire(prestataire_code)

    with transaction.atomic():
        demande = (
            DemandeAbonnement.objects.select_for_update()
            .select_related("fournisseur", "plan")
            .get(pk=demande.pk)
        )
        if demande.statut not in DemandeAbonnement.STATUTS_TRAITABLES:
            raise ValidationError("Cette demande ne peut plus être payée en ligne.")
        if demande.est_expiree:
            raise ValidationError("Cette demande a expiré.")
        if not paiement_en_ligne_actif_pour_pays(demande.pays):
            raise PaiementEnLigneDesactive(
                "Le paiement en ligne n'est pas encore disponible dans ce pays."
            )
        if code not in prestataires_pour_pays(demande.pays):
            raise PrestataireNonConfigure(
                "Ce prestataire n'est pas disponible dans le pays de la demande."
            )
        prestataire = obtenir_prestataire(code, demande.pays)

        paiement_existant = (
            PaiementAbonnement.objects.select_for_update()
            .filter(
                demande_abonnement=demande,
                statut__in=[
                    PaiementAbonnement.Statut.INITIE,
                    PaiementAbonnement.Statut.EN_ATTENTE,
                    PaiementAbonnement.Statut.EN_VERIFICATION,
                ],
            )
            .first()
        )
        if paiement_existant is not None:
            prestataire_existant = normaliser_code_prestataire(
                paiement_existant.prestataire
            )
            if prestataire_existant and prestataire_existant != code:
                raise ValidationError(
                    "Un paiement est déjà en cours avec un autre prestataire."
                )
            if paiement_existant.provider_checkout_url:
                return paiement_existant, {
                    "paiement": paiement_existant,
                    "paiement_url": paiement_existant.provider_checkout_url,
                    "rejoue": True,
                }
            recent = (
                paiement_existant.provider_statut == INITIATION_EN_COURS
                and paiement_existant.date_initiation is not None
                and paiement_existant.date_initiation
                > timezone.now() - DELAI_INITIATION_EXPIREE
            )
            if recent:
                raise ValidationError(
                    "Une initiation de paiement est déjà en cours. Réessayez dans quelques instants."
                )
            paiement_existant.delete()

        paiement = PaiementAbonnement.objects.create(
            demande_abonnement=demande,
            fournisseur=demande.fournisseur,
            abonnement=demande.abonnement_cible,
            plan=demande.plan,
            montant=demande.montant,
            devise=demande.devise,
            statut=PaiementAbonnement.Statut.INITIE,
            type_operation=demande.type_operation,
            moyen_paiement=demande.moyen_paiement or PaiementAbonnement.MoyenPaiement.MOBILE_MONEY,
            prestataire=prestataire.code,
            pays=demande.pays,
            provider_statut=INITIATION_EN_COURS,
            date_initiation=timezone.now(),
        )
        paiement_id = paiement.pk

    try:
        resultat = prestataire.initier_paiement(demande)
    except Exception:
        PaiementAbonnement.objects.filter(
            pk=paiement_id,
            provider_statut=INITIATION_EN_COURS,
        ).delete()
        raise

    with transaction.atomic():
        paiement = PaiementAbonnement.objects.select_for_update().get(pk=paiement_id)
        paiement.pays = demande.pays
        paiement.provider_session_id = resultat.get("provider_session_id", "")
        paiement.provider_transaction_id = resultat.get("provider_transaction_id", "")
        paiement.provider_checkout_url = resultat.get(
            "provider_checkout_url", resultat.get("paiement_url", "")
        )
        paiement.provider_statut = resultat.get("provider_statut", "")
        paiement.reference_transaction = resultat.get("reference_transaction", "")
        paiement.save(
            update_fields=[
                "pays",
                "provider_session_id",
                "provider_transaction_id",
                "provider_checkout_url",
                "provider_statut",
                "reference_transaction",
                "date_modification",
            ]
        )
        journaliser_demande(
            demande,
            EvenementAbonnement.Type.PAIEMENT_INITIE,
            nouveau_statut=demande.statut,
            motif="Paiement en ligne initié.",
            effectue_par=effectue_par,
            montant=demande.montant,
            reference_paiement=paiement.reference_transaction,
            metadonnees={
                "prestataire": prestataire.code,
                "pays": demande.pays,
                "provider_session_id": paiement.provider_session_id,
                "provider_transaction_id": paiement.provider_transaction_id,
            },
        )
    return paiement, resultat
