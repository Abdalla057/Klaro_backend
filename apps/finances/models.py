import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class TransactionFinanciere(models.Model):
    """Grand livre des mouvements financiers validés d'un fournisseur."""

    class Type(models.TextChoices):
        ATTRIBUTION_INITIALE = "ATTRIBUTION_INITIALE", "Attribution initiale"
        ENCAISSEMENT = "ENCAISSEMENT", "Encaissement"
        DECAISSEMENT = "DECAISSEMENT", "Décaissement"
        REGULARISATION = "REGULARISATION", "Régularisation"

    class Sens(models.TextChoices):
        CREDIT = "CREDIT", "Crédit"
        DEBIT = "DEBIT", "Débit"

    class Statut(models.TextChoices):
        VALIDEE = "VALIDEE", "Validée"
        ANNULEE = "ANNULEE", "Annulée"

    reference = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="transactions_financieres",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="transactions_financieres",
    )
    agent = models.ForeignKey(
        "agents.Agent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions_financieres",
    )
    encaissement = models.OneToOneField(
        "encaissements.Encaissement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transaction_financiere",
    )
    decaissement = models.OneToOneField(
        "finances.DemandeDecaissement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transaction_financiere",
    )
    type_transaction = models.CharField(max_length=30, choices=Type.choices)
    sens = models.CharField(max_length=10, choices=Sens.choices)
    montant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.VALIDEE,
    )
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions_financieres_effectuees",
    )
    commentaire = models.TextField(blank=True)
    cle_idempotence = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_validation = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["client"],
                condition=models.Q(
                    type_transaction="ATTRIBUTION_INITIALE",
                    statut="VALIDEE",
                ),
                name="unique_attribution_initiale_validee",
            )
        ]
        indexes = [
            models.Index(fields=["fournisseur", "-date_creation"]),
            models.Index(fields=["client", "-date_creation"]),
            models.Index(fields=["type_transaction", "statut"]),
        ]

    @property
    def montant_signe(self):
        if self.sens == self.Sens.CREDIT:
            return self.montant
        return -self.montant

    def __str__(self):
        return f"{self.get_type_transaction_display()} {self.montant}"


class DemandeDecaissement(models.Model):
    """Demande de retrait initiée par un client et suivie par le fournisseur."""

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        CONFIRMATION_AGENT = "CONFIRMATION_AGENT", "Confirmée par l'agent"
        EN_ATTENTE_VALIDATION = (
            "EN_ATTENTE_VALIDATION",
            "En attente de validation",
        )
        APPROUVE = "APPROUVE", "Approuvée"
        REFUSE = "REFUSE", "Refusée"
        PAYE = "PAYE", "Payée"
        ANNULE = "ANNULE", "Annulée"

    STATUTS_EN_COURS = (
        Statut.EN_ATTENTE,
        Statut.CONFIRMATION_AGENT,
        Statut.EN_ATTENTE_VALIDATION,
        Statut.APPROUVE,
    )

    reference = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="demandes_decaissement",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="demandes_decaissement",
    )
    agent = models.ForeignKey(
        "agents.Agent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="demandes_decaissement",
    )
    montant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    statut = models.CharField(
        max_length=30,
        choices=Statut.choices,
        default=Statut.EN_ATTENTE,
    )
    commentaire = models.TextField(blank=True)
    motif_refus = models.TextField(blank=True)
    cle_idempotence = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
    )
    confirme_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decaissements_confirmes",
    )
    decide_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decaissements_decides",
    )
    paye_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decaissements_payes",
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_confirmation_agent = models.DateTimeField(null=True, blank=True)
    date_decision = models.DateTimeField(null=True, blank=True)
    date_paiement = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        indexes = [
            models.Index(fields=["fournisseur", "statut", "-date_creation"]),
            models.Index(fields=["client", "statut", "-date_creation"]),
        ]

    def clean(self):
        if (
            self.client_id
            and self.fournisseur_id
            and self.client.fournisseur_id != self.fournisseur_id
        ):
            raise ValidationError(
                "Le client doit appartenir au même fournisseur que la demande."
            )
        if self.agent_id and self.agent.fournisseur_id != self.fournisseur_id:
            raise ValidationError(
                "L'agent doit appartenir au même fournisseur que la demande."
            )

    @property
    def est_en_cours(self):
        return self.statut in self.STATUTS_EN_COURS

    def __str__(self):
        return f"{self.montant} - {self.client} - {self.get_statut_display()}"
