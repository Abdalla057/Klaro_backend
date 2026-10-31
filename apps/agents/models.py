from django.conf import settings
from django.db import models

from apps.comptes.mixins import ProfilActivableParTelephone


class Agent(ProfilActivableParTelephone):
    """Agent de recouvrement rattaché à un seul fournisseur.

    Un fournisseur peut créer plusieurs agents. Le compte est créé inactif,
    sans mot de passe, puis activé par code à six chiffres communiqué
    manuellement par le fournisseur.
    """

    utilisateur = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_profile",
    )
    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.CASCADE,
        related_name="agents",
    )
    zone = models.CharField(
        max_length=255, blank=True, help_text="Zone / secteur de travail"
    )

    class Meta:
        ordering = ["-date_creation"]

    def __str__(self):
        return f"{self.nom_complet} - Agent de {self.fournisseur}"
