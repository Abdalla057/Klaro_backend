import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


class Encaissement(models.Model):
    """
    Paiement initié par le client (il saisit le montant qu'il veut payer),
    puis confirmé par un agent sur le terrain — soit en scannant le QR code
    (code_confirmation), soit en cliquant sur "Confirmer" dans sa liste
    d'encaissements en attente. Le solde du client n'est déduit qu'à la
    confirmation, jamais à la création.
    """

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente de confirmation"
        CONFIRME = "CONFIRME", "Confirmé"
        ANNULE = "ANNULE", "Annulé"

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="encaissements"
    )
    agent = models.ForeignKey(
        "agents.Agent",
        on_delete=models.PROTECT,
        related_name="encaissements",
        null=True,
        blank=True,
        help_text="Rempli automatiquement au moment de la confirmation.",
    )
    montant = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    statut = models.CharField(
        max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE
    )
    code_confirmation = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text="Encodé dans le QR code affiché au client.",
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_confirmation = models.DateTimeField(null=True, blank=True)
    commentaire = models.TextField(blank=True)

    def clean(self):
        if self.agent and self.agent.fournisseur_id != self.client.fournisseur_id:
            raise ValidationError(
                "L'agent doit appartenir au même fournisseur que le client."
            )

    @transaction.atomic
    def confirmer(self, agent):
        """Confirme le paiement (scan QR ou clic bouton) et déduit le solde du client."""
        if self.statut != self.Statut.EN_ATTENTE:
            raise ValidationError("Cet encaissement a déjà été traité.")
        if agent.fournisseur_id != self.client.fournisseur_id:
            raise ValidationError("Cet agent ne peut pas confirmer ce paiement.")
        self.agent = agent
        self.statut = self.Statut.CONFIRME
        self.date_confirmation = timezone.now()
        self.full_clean()
        self.save(update_fields=["agent", "statut", "date_confirmation"])
        self.client.solde_du -= self.montant
        self.client.save(update_fields=["solde_du"])

    def annuler(self):
        if self.statut != self.Statut.EN_ATTENTE:
            raise ValidationError("Seul un encaissement en attente peut être annulé.")
        self.statut = self.Statut.ANNULE
        self.save(update_fields=["statut"])

    def __str__(self):
        return f"{self.montant} - {self.client} - {self.get_statut_display()}"