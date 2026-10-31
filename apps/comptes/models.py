from django.contrib.auth.models import AbstractUser
from django.db import models

from .managers import UtilisateurManager


class Utilisateur(AbstractUser):
    ROLES = [
        ("fournisseur", "Fournisseur"),
        ("client", "Client"),
        ("agent", "Agent"),
        ("super_admin", "Super administrateur"),
    ]

    telephone = models.CharField(max_length=20, unique=True)
    role = models.CharField(max_length=20, choices=ROLES)

    USERNAME_FIELD = 'telephone'
    REQUIRED_FIELDS = []

    objects = UtilisateurManager()

    def __str__(self):
        return f"{self.telephone} ({self.role})"