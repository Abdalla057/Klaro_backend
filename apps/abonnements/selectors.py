"""Sélecteurs en lecture seule pour les demandes d'abonnement."""

from django.db.models import Q

from .models import DemandeAbonnement, PaiementAbonnement


def demandes_du_fournisseur(fournisseur):
    """Toutes les demandes d'un fournisseur, sans fuite inter-fournisseurs."""
    return (
        DemandeAbonnement.objects.select_related(
            "fournisseur",
            "plan",
            "abonnement_cible",
            "abonnement_genere",
            "traite_par",
        )
        .filter(fournisseur=fournisseur)
        .order_by("-date_creation")
    )


def paiements_du_fournisseur(fournisseur):
    return (
        PaiementAbonnement.objects.select_related(
            "demande_abonnement",
            "abonnement",
            "plan",
            "fournisseur",
            "confirme_par",
        )
        .filter(fournisseur=fournisseur)
        .order_by("-date_creation")
    )


def filtrer_demandes_pour_admin(queryset, params):
    """Applique les filtres documentés de l'espace super administrateur."""
    fournisseur = params.get("fournisseur")
    if fournisseur:
        queryset = queryset.filter(fournisseur_id=fournisseur)

    plan = params.get("plan")
    if plan:
        queryset = queryset.filter(plan_id=plan)

    statut = params.get("statut")
    if statut:
        queryset = queryset.filter(statut=statut)

    type_operation = params.get("type_operation")
    if type_operation:
        queryset = queryset.filter(type_operation=type_operation)

    prestataire = params.get("prestataire")
    if prestataire:
        queryset = queryset.filter(prestataire=prestataire)

    moyen_paiement = params.get("moyen_paiement")
    if moyen_paiement:
        queryset = queryset.filter(moyen_paiement=moyen_paiement)

    date_debut = params.get("date_debut")
    if date_debut:
        queryset = queryset.filter(date_creation__date__gte=date_debut)

    date_fin = params.get("date_fin")
    if date_fin:
        queryset = queryset.filter(date_creation__date__lte=date_fin)

    reference = params.get("reference")
    if reference:
        queryset = queryset.filter(reference__icontains=reference)

    reference_transaction = params.get("reference_transaction")
    if reference_transaction:
        queryset = queryset.filter(
            Q(reference_transaction__icontains=reference_transaction)
            | Q(
                paiements__reference_transaction__icontains=reference_transaction
            )
        ).distinct()

    recherche = params.get("recherche")
    if recherche:
        queryset = queryset.filter(
            Q(reference__icontains=recherche)
            | Q(fournisseur__nom_entreprise__icontains=recherche)
            | Q(fournisseur__code_entreprise__icontains=recherche)
            | Q(fournisseur__telephone__icontains=recherche)
        )

    return queryset
