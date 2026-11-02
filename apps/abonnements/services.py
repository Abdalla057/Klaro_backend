from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.fournisseurs.models import Fournisseur

from .exceptions import AbonnementExpire, QuotaDepasse
from .models import (
    Abonnement,
    EvenementAbonnement,
    PaiementAbonnement,
    PlanAbonnement,
)


def _montant_paiement_valide(montant):
    try:
        valeur = Decimal(str(montant))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"montant": "Montant de paiement invalide."}) from exc
    if not valeur.is_finite() or valeur < 0:
        raise ValidationError(
            {"montant": "Le montant du paiement ne peut pas être négatif."}
        )
    if valeur.as_tuple().exponent < -2:
        raise ValidationError(
            {"montant": "Le montant ne peut pas avoir plus de deux décimales."}
        )
    return valeur


def _creer_paiement(
    abonnement,
    type_operation,
    montant=None,
    devise=None,
    reference_transaction="",
    moyen_paiement="",
    cle_idempotence=None,
    commentaire="",
    statut=PaiementAbonnement.Statut.EN_ATTENTE,
    confirme_par=None,
):
    """Historise une opération financière liée à un abonnement.

    Les paiements gratuits (essai ou plan à 0) ne créent pas d'opération :
    ils ne doivent jamais influencer le chiffre d'affaires.
    """
    montant = (
        _montant_paiement_valide(montant)
        if montant is not None
        else (abonnement.montant or Decimal("0"))
    )
    if montant <= 0:
        return None

    if cle_idempotence:
        existant = PaiementAbonnement.objects.filter(
            cle_idempotence=cle_idempotence
        ).first()
        if existant is not None:
            if existant.abonnement_id != abonnement.pk:
                raise ValidationError(
                    {
                        "cle_idempotence": (
                            "Cette clé d'idempotence est déjà utilisée "
                            "pour un autre abonnement."
                        )
                    }
                )
            return existant

    maintenant = timezone.now()
    confirme = statut == PaiementAbonnement.Statut.CONFIRME
    paiement = PaiementAbonnement.objects.create(
        fournisseur=abonnement.fournisseur,
        abonnement=abonnement,
        plan=abonnement.plan,
        montant=montant,
        devise=(devise or abonnement.devise or abonnement.plan.devise),
        statut=statut,
        type_operation=type_operation,
        moyen_paiement=moyen_paiement or "",
        reference_transaction=reference_transaction,
        cle_idempotence=cle_idempotence or None,
        commentaire=commentaire,
        date_confirmation=maintenant if confirme else None,
        confirme_par=confirme_par if confirme else None,
    )
    if confirme and reference_transaction:
        abonnement.reference_paiement = reference_transaction
        abonnement.save(update_fields=["reference_paiement", "date_modification"])
    return paiement


def _compter_agents(fournisseur):
    from apps.agents.models import Agent

    return Agent.objects.filter(fournisseur=fournisseur).count()


def _journaliser(
    abonnement,
    type_evenement,
    ancien_statut="",
    nouveau_statut="",
    motif="",
    effectue_par=None,
    montant=None,
    reference_paiement="",
):
    return EvenementAbonnement.objects.create(
        abonnement=abonnement,
        type_evenement=type_evenement,
        ancien_statut=ancien_statut,
        nouveau_statut=nouveau_statut,
        motif=motif,
        effectue_par=effectue_par,
        montant=montant,
        reference_paiement=reference_paiement,
    )


def _changer_statut(
    abonnement,
    nouveau_statut,
    motif,
    effectue_par=None,
    type_evenement=EvenementAbonnement.Type.REACTIVATION,
    montant=None,
    reference_paiement="",
):
    ancien_statut = abonnement.statut
    abonnement.statut = nouveau_statut
    abonnement.save(update_fields=["statut", "date_modification"])
    _journaliser(
        abonnement,
        type_evenement,
        ancien_statut=ancien_statut,
        nouveau_statut=nouveau_statut,
        motif=motif,
        effectue_par=effectue_par,
        montant=montant,
        reference_paiement=reference_paiement,
    )
    return abonnement


def obtenir_plan_essai():
    """Retourne le plan d'essai actif, ou lève une erreur de configuration."""
    plan = PlanAbonnement.objects.filter(est_essai=True, actif=True).first()
    if plan is None:
        raise ImproperlyConfigured(
            "Aucun plan d'essai actif n'est configuré. "
            "Créez ou réactivez un plan d'essai dans l'administration."
        )
    return plan


@transaction.atomic
def demarrer_essai(fournisseur, effectue_par=None):
    """Attribue l'essai gratuit à un nouveau fournisseur.

    L'opération est idempotente : si un abonnement actif existe déjà, il est
    retourné sans en créer un second.
    """
    plan = obtenir_plan_essai()
    fournisseur = Fournisseur.objects.select_for_update().get(pk=fournisseur.pk)

    abonnement = (
        Abonnement.objects.select_for_update()
        .filter(fournisseur=fournisseur, statut=Abonnement.Statut.ACTIF)
        .select_related("plan")
        .first()
    )
    if abonnement is not None:
        abonnement.actualiser_statut(effectue_par=effectue_par)
        if abonnement.statut == Abonnement.Statut.ACTIF:
            return abonnement

    if Abonnement.objects.filter(fournisseur=fournisseur, est_essai=True).exists():
        raise ValidationError(
            "Ce fournisseur a déjà bénéficié de la période d'essai gratuite."
        )

    debut = timezone.now()
    abonnement = Abonnement.objects.create(
        fournisseur=fournisseur,
        plan=plan,
        date_debut=debut,
        date_fin=debut + timedelta(days=plan.duree_jours),
        statut=Abonnement.Statut.ACTIF,
        est_essai=True,
        montant=0,
        devise=plan.devise,
        cree_par=effectue_par,
    )
    _journaliser(
        abonnement,
        EvenementAbonnement.Type.CREATION,
        nouveau_statut=Abonnement.Statut.ACTIF,
        motif="Démarrage de la période d'essai gratuite.",
        effectue_par=effectue_par,
        montant=0,
    )
    return abonnement


@transaction.atomic
def souscrire(
    fournisseur,
    plan,
    effectue_par=None,
    date_debut=None,
    reference_paiement="",
    notes="",
    montant_paiement=None,
    moyen_paiement="",
    paiement_confirme=False,
    cle_idempotence=None,
):
    """Souscrit un plan payant pour un fournisseur.

    Si un essai est encore actif, il est clôturé immédiatement. Un abonnement
    payant déjà actif ne peut pas être remplacé par cette fonction : il faut
    passer par le renouvellement afin de conserver une trace explicite.

    Une opération financière est historisée (en attente par défaut). Elle ne
    compte dans le chiffre d'affaires qu'après confirmation explicite.
    """
    if not plan.actif:
        raise ValidationError("Ce plan est inactif et ne peut pas être souscrit.")
    if plan.est_essai:
        raise ValidationError(
            "L'essai gratuit est attribué automatiquement à l'inscription. "
            "Choisissez un plan payant."
        )

    fournisseur = Fournisseur.objects.select_for_update().get(pk=fournisseur.pk)
    if Abonnement.objects.filter(
        fournisseur=fournisseur,
        statut=Abonnement.Statut.SUSPENDU,
    ).exists():
        raise ValidationError(
            "Un abonnement suspendu existe pour ce fournisseur. "
            "Réactivez-le ou annulez-le avant de souscrire un nouveau plan."
        )

    abonnement_actuel = (
        Abonnement.objects.select_for_update()
        .filter(fournisseur=fournisseur, statut=Abonnement.Statut.ACTIF)
        .select_related("plan")
        .first()
    )
    if abonnement_actuel is not None:
        abonnement_actuel.actualiser_statut(effectue_par=effectue_par)
        if abonnement_actuel.statut == Abonnement.Statut.ACTIF:
            if not abonnement_actuel.est_essai:
                raise ValidationError(
                    "Un abonnement payant est déjà actif. "
                    "Utilisez le renouvellement pour le prolonger."
                )
            _changer_statut(
                abonnement_actuel,
                Abonnement.Statut.EXPIRE,
                "Fin de la période d'essai avant souscription d'un plan payant.",
                effectue_par=effectue_par,
                type_evenement=EvenementAbonnement.Type.EXPIRATION,
            )

    debut = date_debut or timezone.now()
    fin = debut + timedelta(days=plan.duree_jours)
    if fin <= timezone.now():
        raise ValidationError("La période de l'abonnement est déjà terminée.")

    abonnement = Abonnement.objects.create(
        fournisseur=fournisseur,
        plan=plan,
        date_debut=debut,
        date_fin=fin,
        statut=Abonnement.Statut.ACTIF,
        est_essai=False,
        montant=plan.prix,
        devise=plan.devise,
        reference_paiement=reference_paiement,
        notes=notes,
        cree_par=effectue_par,
    )
    _journaliser(
        abonnement,
        EvenementAbonnement.Type.CREATION,
        nouveau_statut=Abonnement.Statut.ACTIF,
        motif=notes,
        effectue_par=effectue_par,
        montant=plan.prix,
        reference_paiement=reference_paiement,
    )
    _creer_paiement(
        abonnement,
        PaiementAbonnement.TypeOperation.SOUSCRIPTION,
        montant=montant_paiement,
        reference_transaction=reference_paiement,
        moyen_paiement=moyen_paiement,
        cle_idempotence=cle_idempotence,
        commentaire=notes,
        statut=(
            PaiementAbonnement.Statut.CONFIRME
            if paiement_confirme
            else PaiementAbonnement.Statut.EN_ATTENTE
        ),
        confirme_par=effectue_par,
    )
    return abonnement


@transaction.atomic
def renouveler(
    abonnement,
    plan=None,
    effectue_par=None,
    reference_paiement="",
    notes="",
    montant_paiement=None,
    moyen_paiement="",
    paiement_confirme=False,
    cle_idempotence=None,
):
    """Renouvelle un abonnement.

    - Abonnement actif avec le même plan : la date de fin est prolongée sans
      interrompre le service.
    - Abonnement expiré ou suspendu : un nouvel abonnement actif est créé.
    - Changement de plan : autorisé uniquement lorsque l'abonnement en cours
      n'est plus actif, afin d'éviter des règles de prorata implicites.
    """
    abonnement = (
        Abonnement.objects.select_for_update()
        .select_related("fournisseur", "plan")
        .get(pk=abonnement.pk)
    )
    if abonnement.statut == Abonnement.Statut.ANNULE:
        raise ValidationError(
            "Un abonnement annulé ne peut pas être renouvelé. "
            "Souscrivez un nouveau plan."
        )

    plan = plan or abonnement.plan
    if plan.est_essai:
        raise ValidationError(
            "Un abonnement d'essai ne peut pas être renouvelé. "
            "Choisissez un plan payant."
        )

    abonnement.actualiser_statut(effectue_par=effectue_par)

    if abonnement.statut == Abonnement.Statut.ACTIF:
        if plan.pk != abonnement.plan_id:
            raise ValidationError(
                "Le plan ne peut être modifié qu'à l'expiration "
                "de l'abonnement en cours."
            )
        abonnement.date_fin = abonnement.date_fin + timedelta(days=plan.duree_jours)
        abonnement.save(update_fields=["date_fin", "date_modification"])
        _journaliser(
            abonnement,
            EvenementAbonnement.Type.RENOUVELLEMENT,
            ancien_statut=Abonnement.Statut.ACTIF,
            nouveau_statut=Abonnement.Statut.ACTIF,
            motif=(
                f"Prolongation jusqu'au {abonnement.date_fin:%d/%m/%Y}. {notes}"
            ).strip(),
            effectue_par=effectue_par,
            montant=plan.prix,
            reference_paiement=reference_paiement,
        )
        _creer_paiement(
            abonnement,
            PaiementAbonnement.TypeOperation.RENOUVELLEMENT,
            montant=montant_paiement,
            reference_transaction=reference_paiement,
            moyen_paiement=moyen_paiement,
            cle_idempotence=cle_idempotence,
            commentaire=notes,
            statut=(
                PaiementAbonnement.Statut.CONFIRME
                if paiement_confirme
                else PaiementAbonnement.Statut.EN_ATTENTE
            ),
            confirme_par=effectue_par,
        )
        return abonnement

    if plan.pk != abonnement.plan_id and not plan.actif:
        raise ValidationError("Le nouveau plan sélectionné est inactif.")

    if abonnement.statut == Abonnement.Statut.SUSPENDU:
        _changer_statut(
            abonnement,
            Abonnement.Statut.EXPIRE,
            "Abonnement suspendu clôturé par un renouvellement.",
            effectue_par=effectue_par,
            type_evenement=EvenementAbonnement.Type.EXPIRATION,
        )

    debut = timezone.now()
    nouveau = Abonnement.objects.create(
        fournisseur=abonnement.fournisseur,
        plan=plan,
        date_debut=debut,
        date_fin=debut + timedelta(days=plan.duree_jours),
        statut=Abonnement.Statut.ACTIF,
        est_essai=False,
        montant=plan.prix,
        devise=plan.devise,
        reference_paiement=reference_paiement,
        notes=notes,
        cree_par=effectue_par,
    )
    _journaliser(
        nouveau,
        EvenementAbonnement.Type.RENOUVELLEMENT,
        nouveau_statut=Abonnement.Statut.ACTIF,
        motif=f"Renouvellement de l'abonnement #{abonnement.pk}. {notes}".strip(),
        effectue_par=effectue_par,
        montant=plan.prix,
        reference_paiement=reference_paiement,
    )
    _creer_paiement(
        nouveau,
        PaiementAbonnement.TypeOperation.RENOUVELLEMENT,
        montant=montant_paiement,
        reference_transaction=reference_paiement,
        moyen_paiement=moyen_paiement,
        cle_idempotence=cle_idempotence,
        commentaire=notes,
        statut=(
            PaiementAbonnement.Statut.CONFIRME
            if paiement_confirme
            else PaiementAbonnement.Statut.EN_ATTENTE
        ),
        confirme_par=effectue_par,
    )
    return nouveau


@transaction.atomic
def suspendre(abonnement, motif, effectue_par=None):
    abonnement = Abonnement.objects.select_for_update().get(pk=abonnement.pk)
    abonnement.actualiser_statut(effectue_par=effectue_par)
    if abonnement.statut == Abonnement.Statut.SUSPENDU:
        return abonnement
    if abonnement.statut != Abonnement.Statut.ACTIF:
        raise ValidationError("Seul un abonnement actif peut être suspendu.")
    return _changer_statut(
        abonnement,
        Abonnement.Statut.SUSPENDU,
        motif,
        effectue_par=effectue_par,
        type_evenement=EvenementAbonnement.Type.SUSPENSION,
    )


@transaction.atomic
def reactiver(abonnement, effectue_par=None):
    abonnement = Abonnement.objects.select_for_update().get(pk=abonnement.pk)
    if abonnement.statut == Abonnement.Statut.ACTIF:
        return abonnement
    if abonnement.statut != Abonnement.Statut.SUSPENDU:
        raise ValidationError("Seul un abonnement suspendu peut être réactivé.")
    if abonnement.date_fin <= timezone.now():
        raise ValidationError(
            "Cet abonnement est expiré. Renouvelez-le pour rétablir le service."
        )
    return _changer_statut(
        abonnement,
        Abonnement.Statut.ACTIF,
        "Réactivation par le super administrateur.",
        effectue_par=effectue_par,
        type_evenement=EvenementAbonnement.Type.REACTIVATION,
    )


@transaction.atomic
def annuler(abonnement, motif, effectue_par=None):
    abonnement = Abonnement.objects.select_for_update().get(pk=abonnement.pk)
    if abonnement.statut == Abonnement.Statut.ANNULE:
        return abonnement
    if abonnement.statut == Abonnement.Statut.EXPIRE:
        raise ValidationError("Un abonnement expiré ne peut pas être annulé.")
    return _changer_statut(
        abonnement,
        Abonnement.Statut.ANNULE,
        motif,
        effectue_par=effectue_par,
        type_evenement=EvenementAbonnement.Type.ANNULATION,
    )


def abonnement_actif(fournisseur):
    """Retourne l'abonnement actif du fournisseur, ou None.

    Le statut est actualisé à la volée pour que l'expiration soit prise en
    compte même entre deux exécutions de la commande planifiée.
    """
    abonnement = (
        Abonnement.objects.select_related("plan")
        .filter(fournisseur=fournisseur, statut=Abonnement.Statut.ACTIF)
        .order_by("-date_debut")
        .first()
    )
    if abonnement is None:
        return None
    abonnement.actualiser_statut()
    if abonnement.statut != Abonnement.Statut.ACTIF:
        return None
    return abonnement


def verifier_quota_clients(fournisseur):
    abonnement = abonnement_actif(fournisseur)
    if abonnement is None:
        raise AbonnementExpire(
            {
                "detail": (
                    "Votre abonnement est inactif. "
                    "Renouvelez-le pour ajouter des clients."
                ),
                "code": "abonnement_inactif",
            }
        )

    limite = abonnement.plan.max_clients
    if limite is not None:
        utilise = fournisseur.clients.count()
        if utilise >= limite:
            raise QuotaDepasse(
                {
                    "detail": f"Le quota de {limite} clients est atteint.",
                    "code": "quota_clients_atteint",
                    "limite": limite,
                    "utilise": utilise,
                }
            )
    return abonnement


def verifier_quota_agents(fournisseur):
    abonnement = abonnement_actif(fournisseur)
    if abonnement is None:
        raise AbonnementExpire(
            {
                "detail": (
                    "Votre abonnement est inactif. "
                    "Renouvelez-le pour ajouter un agent."
                ),
                "code": "abonnement_inactif",
            }
        )

    limite = abonnement.plan.max_agents
    if limite is not None:
        utilise = _compter_agents(fournisseur)
        if utilise >= limite:
            raise QuotaDepasse(
                {
                    "detail": f"Le quota de {limite} agent(s) est atteint.",
                    "code": "quota_agents_atteint",
                    "limite": limite,
                    "utilise": utilise,
                }
            )
    return abonnement


@transaction.atomic
def enregistrer_paiement(
    abonnement,
    montant=None,
    devise=None,
    type_operation=PaiementAbonnement.TypeOperation.SOUSCRIPTION,
    moyen_paiement="",
    reference_transaction="",
    cle_idempotence=None,
    commentaire="",
    confirmer=False,
    effectue_par=None,
):
    """Enregistre une opération financière pour un abonnement existant.

    Le paiement reste en attente par défaut. ``confirmer=True`` le rend
    immédiatement comptabilisable. L'opération ne modifie jamais les dates
    de l'abonnement : celles-ci relèvent uniquement de la souscription ou du
    renouvellement, afin d'éviter les doubles prolongations.
    """
    abonnement = (
        Abonnement.objects.select_for_update()
        .select_related("plan", "fournisseur")
        .get(pk=abonnement.pk)
    )
    if abonnement.plan.est_essai:
        raise ValidationError(
            "Un abonnement d'essai ne peut pas faire l'objet d'un paiement."
        )
    return _creer_paiement(
        abonnement,
        type_operation,
        montant=montant,
        devise=devise,
        reference_transaction=reference_transaction,
        moyen_paiement=moyen_paiement,
        cle_idempotence=cle_idempotence,
        commentaire=commentaire,
        statut=(
            PaiementAbonnement.Statut.CONFIRME
            if confirmer
            else PaiementAbonnement.Statut.EN_ATTENTE
        ),
        confirme_par=effectue_par,
    )


def _paiement_verrouille(paiement):
    return (
        PaiementAbonnement.objects.select_for_update()
        .select_related("abonnement", "fournisseur", "plan")
        .get(pk=paiement.pk)
    )


@transaction.atomic
def confirmer_paiement(paiement, effectue_par=None):
    """Confirme un paiement en attente, une seule fois (idempotent)."""
    paiement = _paiement_verrouille(paiement)
    if paiement.statut == PaiementAbonnement.Statut.CONFIRME:
        return paiement
    if paiement.statut != PaiementAbonnement.Statut.EN_ATTENTE:
        raise ValidationError(
            "Seul un paiement en attente peut être confirmé."
        )

    paiement.statut = PaiementAbonnement.Statut.CONFIRME
    paiement.date_confirmation = timezone.now()
    paiement.confirme_par = effectue_par
    paiement.save(
        update_fields=[
            "statut",
            "date_confirmation",
            "confirme_par",
            "date_modification",
        ]
    )
    if paiement.reference_transaction:
        Abonnement.objects.filter(pk=paiement.abonnement_id).update(
            reference_paiement=paiement.reference_transaction,
            date_modification=timezone.now(),
        )
    return paiement


@transaction.atomic
def refuser_paiement(paiement, motif="", effectue_par=None):
    """Marque un paiement en attente comme refusé, sans toucher l'abonnement."""
    paiement = _paiement_verrouille(paiement)
    if paiement.statut == PaiementAbonnement.Statut.REFUSE:
        return paiement
    if paiement.statut != PaiementAbonnement.Statut.EN_ATTENTE:
        raise ValidationError("Seul un paiement en attente peut être refusé.")
    paiement.statut = PaiementAbonnement.Statut.REFUSE
    if motif:
        paiement.commentaire = (
            f"{paiement.commentaire}\nRefus : {motif}".strip()
        )
    paiement.save(update_fields=["statut", "commentaire", "date_modification"])
    return paiement


@transaction.atomic
def annuler_paiement(paiement, motif="", effectue_par=None):
    """Annule un paiement en attente. Un paiement confirmé reste immuable."""
    paiement = _paiement_verrouille(paiement)
    if paiement.statut == PaiementAbonnement.Statut.ANNULE:
        return paiement
    if paiement.statut != PaiementAbonnement.Statut.EN_ATTENTE:
        raise ValidationError(
            "Seul un paiement en attente peut être annulé. "
            "Un paiement confirmé ne peut plus être modifié."
        )
    paiement.statut = PaiementAbonnement.Statut.ANNULE
    if motif:
        paiement.commentaire = (
            f"{paiement.commentaire}\nAnnulation : {motif}".strip()
        )
    paiement.save(update_fields=["statut", "commentaire", "date_modification"])
    return paiement


def etat_abonnement_fournisseur(fournisseur):
    """Construit un état lisible de l'abonnement et de son utilisation."""
    abonnement = abonnement_actif(fournisseur)
    utilise_clients = fournisseur.clients.count()
    utilise_agents = _compter_agents(fournisseur)

    if abonnement is None:
        dernier = (
            Abonnement.objects.select_related("plan")
            .filter(fournisseur=fournisseur)
            .order_by("-date_debut", "-date_creation")
            .first()
        )
        plan = dernier.plan if dernier else None
        return {
            "abonnement": dernier,
            "plan": plan,
            "statut": dernier.statut_effectif if dernier else None,
            "date_debut": dernier.date_debut if dernier else None,
            "date_fin": dernier.date_fin if dernier else None,
            "jours_restants": dernier.jours_restants if dernier else 0,
            "est_actif": False,
            "quotas": {
                "clients": plan.max_clients if plan else None,
                "agents": plan.max_agents if plan else None,
            },
            "utilisation": {
                "clients": utilise_clients,
                "agents": utilise_agents,
            },
            "peut_creer_client": False,
            "peut_creer_agent": False,
        }

    plan = abonnement.plan
    return {
        "abonnement": abonnement,
        "plan": plan,
        "statut": abonnement.statut_effectif,
        "date_debut": abonnement.date_debut,
        "date_fin": abonnement.date_fin,
        "jours_restants": abonnement.jours_restants,
        "est_actif": True,
        "quotas": {
            "clients": plan.max_clients,
            "agents": plan.max_agents,
        },
        "utilisation": {
            "clients": utilise_clients,
            "agents": utilise_agents,
        },
        "peut_creer_client": (
            plan.max_clients is None or utilise_clients < plan.max_clients
        ),
        "peut_creer_agent": (
            plan.max_agents is None or utilise_agents < plan.max_agents
        ),
    }
