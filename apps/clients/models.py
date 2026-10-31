from django.conf import settings
from django.db import models

from apps.comptes.mixins import ProfilActivableParTelephone


class Client(ProfilActivableParTelephone):
    """
    Client d'un fournisseur, avec un solde représentant le crédit dû.
    Le fournisseur ajoute le client avec juste son numéro de téléphone : le
    compte est créé inactif, sans mot de passe, et un code d'activation à 6
    chiffres est envoyé par SMS. Le client définit son mot de passe au moment
    de l'activation (voir ProfilActivableParTelephone.activer() dans comptes).
    """

    utilisateur = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="client_profile",
    )
    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur", on_delete=models.CASCADE, related_name="clients"
    )
    adresse = models.CharField(max_length=255, blank=True)
    solde_du = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        nom = self.utilisateur.get_full_name() or self.telephone
        return f"{nom} ({self.fournisseur})"