import math

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


class PlanAbonnement(models.Model):
    """Offre commerciale proposée aux fournisseurs.

    Un plan définit le prix, la durée et les quotas appliqués pendant la
    période d'abonnement. Les abonnements référencent toujours le plan qui a
    servi à les créer, ce qui permet de faire évoluer l'offre sans casser
    l'historique.
    """

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text="Identifiant stable du plan (ex. essai, mensuel, annuel).",
    )
    nom = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    prix = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    devise = models.CharField(max_length=3, default="XOF")
    duree_jours = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(1)],
        help_text="Durée du plan en jours.",
    )
    max_clients = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Nombre maximal de clients. Laisser vide pour un quota illimité.",
    )
    max_agents = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Nombre maximal d'agents. Laisser vide pour un quota illimité.",
    )
    est_essai = models.BooleanField(
        default=False,
        help_text="Un seul plan d'essai peut être actif à la fois.",
    )
    actif = models.BooleanField(
        default=True,
        help_text="Un plan inactif ne peut plus être souscrit, mais reste valable pour les abonnements existants.",
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["prix", "duree_jours", "nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["est_essai"],
                condition=models.Q(est_essai=True, actif=True),
                name="unique_plan_essai_actif",
            ),
        ]
        verbose_name = "plan d'abonnement"
        verbose_name_plural = "plans d'abonnement"

    def clean(self):
        if self.est_essai and self.prix != 0:
            raise ValidationError(
                {"prix": "Un plan d'essai doit obligatoirement être gratuit."}
            )
        if self.max_agents is not None and self.max_agents < 1:
            raise ValidationError(
                {"max_agents": "Le quota d'agents doit être supérieur à zéro."}
            )
        if self.max_clients is not None and self.max_clients < 1:
            raise ValidationError(
                {"max_clients": "Le quota de clients doit être supérieur à zéro."}
            )

    def __str__(self):
        return f"{self.nom} ({self.duree_jours} jours)"


class Abonnement(models.Model):
    """Contrat d'abonnement d'un fournisseur à un plan donné.

    Un fournisseur possède au plus un abonnement ACTIF à la fois. Chaque
    renouvellement crée une nouvelle ligne afin de conserver un historique
    complet et auditable.
    """

    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        EXPIRE = "EXPIRE", "Expiré"
        SUSPENDU = "SUSPENDU", "Suspendu"
        ANNULE = "ANNULE", "Annulé"

    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="abonnements",
    )
    plan = models.ForeignKey(
        PlanAbonnement,
        on_delete=models.PROTECT,
        related_name="abonnements",
    )
    date_debut = models.DateTimeField()
    date_fin = models.DateTimeField()
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.ACTIF,
    )
    est_essai = models.BooleanField(default=False)
    montant = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Montant facturé au moment de la souscription.",
    )
    devise = models.CharField(max_length=3, default="XOF")
    reference_paiement = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="abonnements_crees",
        help_text="Super administrateur ayant créé ou renouvelé l'abonnement.",
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_debut", "-date_creation"]
        constraints = [
            models.UniqueConstraint(
                fields=["fournisseur"],
                condition=models.Q(statut="ACTIF"),
                name="unique_abonnement_actif_par_fournisseur",
            ),
        ]
        indexes = [
            models.Index(fields=["fournisseur", "statut"]),
            models.Index(fields=["date_fin", "statut"]),
        ]
        verbose_name = "abonnement"
        verbose_name_plural = "abonnements"

    def clean(self):
        if self.date_fin <= self.date_debut:
            raise ValidationError(
                {"date_fin": "La date de fin doit être postérieure à la date de début."}
            )
        if self.montant < 0:
            raise ValidationError(
                {"montant": "Le montant d'un abonnement ne peut pas être négatif."}
            )
        if self.est_essai and self.montant != 0:
            raise ValidationError(
                {"montant": "Un abonnement d'essai doit rester gratuit."}
            )

    @property
    def est_actif(self):
        return self.statut == self.Statut.ACTIF and self.date_fin > timezone.now()

    @property
    def statut_effectif(self):
        if (
            self.statut in (self.Statut.ACTIF, self.Statut.SUSPENDU)
            and self.date_fin <= timezone.now()
        ):
            return self.Statut.EXPIRE
        return self.statut

    @property
    def jours_restants(self):
        restant = self.date_fin - timezone.now()
        if restant.total_seconds() <= 0:
            return 0
        return max(0, math.ceil(restant.total_seconds() / 86400))

    def actualiser_statut(self, effectue_par=None):
        """Marque l'abonnement comme expiré si sa date de fin est dépassée.

        Cette vérification est également faite en masse par la commande
        ``expirer_abonnements``. Le passage paresseux évite qu'un abonnement
        expiré continue d'autoriser des écritures entre deux exécutions de la
        commande planifiée.
        """
        if (
            self.statut not in (self.Statut.ACTIF, self.Statut.SUSPENDU)
            or self.date_fin > timezone.now()
        ):
            return self.statut

        ancien_statut = self.statut
        with transaction.atomic():
            self.statut = self.Statut.EXPIRE
            self.save(update_fields=["statut", "date_modification"])
            if self.pk:
                EvenementAbonnement.objects.create(
                    abonnement=self,
                    type_evenement=EvenementAbonnement.Type.EXPIRATION,
                    ancien_statut=ancien_statut,
                    nouveau_statut=self.Statut.EXPIRE,
                    motif="Date de fin atteinte.",
                    effectue_par=effectue_par,
                )
        return self.statut

    def __str__(self):
        return f"{self.fournisseur} - {self.plan} ({self.get_statut_display()})"



class EvenementAbonnement(models.Model):
    """Journal des changements d'état d'un abonnement."""

    class Type(models.TextChoices):
        CREATION = "CREATION", "Création"
        RENOUVELLEMENT = "RENOUVELLEMENT", "Renouvellement"
        SUSPENSION = "SUSPENSION", "Suspension"
        REACTIVATION = "REACTIVATION", "Réactivation"
        ANNULATION = "ANNULATION", "Annulation"
        EXPIRATION = "EXPIRATION", "Expiration"

    abonnement = models.ForeignKey(
        Abonnement,
        on_delete=models.CASCADE,
        related_name="evenements",
    )
    type_evenement = models.CharField(max_length=20, choices=Type.choices)
    ancien_statut = models.CharField(max_length=20, blank=True)
    nouveau_statut = models.CharField(max_length=20, blank=True)
    montant = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Montant encaissé pour cet événement, si applicable.",
    )
    reference_paiement = models.CharField(max_length=100, blank=True)
    motif = models.TextField(blank=True)
    effectue_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evenements_abonnement",
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation"]
        indexes = [
            models.Index(fields=["abonnement", "-date_creation"]),
        ]
        verbose_name = "événement d'abonnement"
        verbose_name_plural = "événements d'abonnement"

    def __str__(self):
        return f"{self.abonnement_id} - {self.get_type_evenement_display()}"
