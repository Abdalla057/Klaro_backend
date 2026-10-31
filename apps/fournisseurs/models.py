import random
import string

from django.conf import settings
from django.db import models


def generer_code_entreprise():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


class Fournisseur(models.Model):
    """Profil fournisseur, lié à un compte Utilisateur (comptes.Utilisateur)."""

    utilisateur = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="fournisseur_profile",
    )
    nom_entreprise = models.CharField(max_length=255)
    code_entreprise = models.CharField(
        max_length=10,
        unique=True,
        editable=False,
        blank=True,
        help_text="Étiquette unique de l'entreprise, générée automatiquement (visible du client/agent dans le SMS d'invitation).",
    )
    adresse = models.CharField(max_length=255, blank=True)
    telephone = models.CharField(max_length=20, blank=True)
    est_suspendu = models.BooleanField(
        default=False,
        help_text="Un fournisseur suspendu ne peut plus se connecter à la plateforme.",
    )
    date_suspension = models.DateTimeField(null=True, blank=True)
    motif_suspension = models.TextField(blank=True)
    suspendu_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fournisseurs_suspendus",
        help_text="Super administrateur ayant prononcé la suspension.",
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.code_entreprise:
            code = generer_code_entreprise()
            while Fournisseur.objects.filter(code_entreprise=code).exists():
                code = generer_code_entreprise()
            self.code_entreprise = code
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nom_entreprise