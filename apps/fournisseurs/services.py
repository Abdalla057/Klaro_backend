from django.db import transaction
from django.utils import timezone

from .models import Fournisseur


@transaction.atomic
def suspendre_fournisseur(fournisseur, motif, effectue_par=None):
    """Suspend un fournisseur et désactive son accès à la plateforme.

    La suspension est indépendante de l'abonnement : un fournisseur suspendu
    conserve son historique d'abonnement, mais ne peut plus se connecter. La
    réactivation ne rétablit pas un abonnement expiré.
    """
    fournisseur = (
        Fournisseur.objects.select_for_update()
        .select_related("utilisateur")
        .get(pk=fournisseur.pk)
    )
    if fournisseur.est_suspendu:
        return fournisseur

    fournisseur.est_suspendu = True
    fournisseur.date_suspension = timezone.now()
    fournisseur.motif_suspension = motif
    fournisseur.suspendu_par = effectue_par
    fournisseur.save(
        update_fields=[
            "est_suspendu",
            "date_suspension",
            "motif_suspension",
            "suspendu_par",
        ]
    )

    utilisateur = fournisseur.utilisateur
    if utilisateur.is_active:
        utilisateur.is_active = False
        utilisateur.save(update_fields=["is_active"])

    return fournisseur


@transaction.atomic
def reactiver_fournisseur(fournisseur):
    """Réactive un fournisseur précédemment suspendu."""
    fournisseur = (
        Fournisseur.objects.select_for_update()
        .select_related("utilisateur")
        .get(pk=fournisseur.pk)
    )
    if not fournisseur.est_suspendu:
        return fournisseur

    fournisseur.est_suspendu = False
    fournisseur.date_suspension = None
    fournisseur.motif_suspension = ""
    fournisseur.suspendu_par = None
    fournisseur.save(
        update_fields=[
            "est_suspendu",
            "date_suspension",
            "motif_suspension",
            "suspendu_par",
        ]
    )

    utilisateur = fournisseur.utilisateur
    if not utilisateur.is_active:
        utilisateur.is_active = True
        utilisateur.save(update_fields=["is_active"])

    return fournisseur
