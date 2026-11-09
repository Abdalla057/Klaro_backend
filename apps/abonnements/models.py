import math
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


def generer_reference_demande():
    """Référence KLARO publique, unique et non devinable d'une demande."""
    return f"KLARO-DEM-{uuid.uuid4().hex[:12].upper()}"


def chemin_preuve_paiement(instance, nom_fichier):
    """Génère un chemin privé : aucun nom fourni par le client n'est conservé."""
    suffixe = Path(nom_fichier).suffix.lower()
    return f"preuves_paiement/{instance.pk}/{uuid.uuid4().hex}{suffixe}"


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


class PaiementAbonnement(models.Model):
    """Opération financière liée à une souscription ou à un renouvellement.

    Un paiement n'alimente le chiffre d'affaires qu'après confirmation. Le
    montant et la devise sont figés au moment de l'opération afin que les
    évolutions ultérieures du plan ou de l'abonnement ne réécrivent pas
    l'historique.
    """

    class Statut(models.TextChoices):
        INITIE = "INITIE", "Initié"
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        EN_VERIFICATION = "EN_VERIFICATION", "En vérification"
        CONFIRME = "CONFIRME", "Confirmé"
        REFUSE = "REFUSE", "Refusé"
        ECHEC = "ECHEC", "Échec"
        ANNULE = "ANNULE", "Annulé"
        REMBOURSE = "REMBOURSE", "Remboursé"

    class TypeOperation(models.TextChoices):
        SOUSCRIPTION = "SOUSCRIPTION", "Souscription"
        RENOUVELLEMENT = "RENOUVELLEMENT", "Renouvellement"
        REGULARISATION = "REGULARISATION", "Régularisation"

    class MoyenPaiement(models.TextChoices):
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile money"
        VIREMENT = "VIREMENT", "Virement"
        ESPECES = "ESPECES", "Espèces"
        CARTE = "CARTE", "Carte bancaire"
        AUTRE = "AUTRE", "Autre"

    reference = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="Identifiant unique et immuable du paiement.",
    )
    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="paiements_abonnement",
    )
    abonnement = models.ForeignKey(
        Abonnement,
        on_delete=models.PROTECT,
        related_name="paiements",
        null=True,
        blank=True,
        help_text=(
            "Abonnement activé ou prolongé. Nul tant que la demande n'est "
            "pas confirmée : le paiement précède la création de l'abonnement."
        ),
    )
    demande_abonnement = models.ForeignKey(
        "DemandeAbonnement",
        on_delete=models.PROTECT,
        related_name="paiements",
        null=True,
        blank=True,
        help_text="Demande à l'origine du paiement dans le nouveau flux.",
    )
    prestataire = models.CharField(
        max_length=50,
        blank=True,
        help_text="Prestataire de paiement en ligne (wave, orange_money…).",
    )
    provider_transaction_id = models.CharField(
        max_length=190,
        blank=True,
        help_text="Identifiant de transaction fourni par le prestataire.",
    )
    date_initiation = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date d'initiation du paiement en ligne.",
    )
    plan = models.ForeignKey(
        PlanAbonnement,
        on_delete=models.PROTECT,
        related_name="paiements",
        help_text="Plan concerné au moment du paiement.",
    )
    montant = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    devise = models.CharField(max_length=3, default="XOF")
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.EN_ATTENTE,
    )
    type_operation = models.CharField(
        max_length=20,
        choices=TypeOperation.choices,
        default=TypeOperation.SOUSCRIPTION,
    )
    moyen_paiement = models.CharField(
        max_length=20,
        choices=MoyenPaiement.choices,
        blank=True,
    )
    reference_transaction = models.CharField(
        max_length=150,
        blank=True,
        help_text="Référence externe de la transaction (opérateur, banque…).",
    )
    cle_idempotence = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
        help_text="Empêche l'enregistrement en double d'une même opération.",
    )
    confirme_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paiements_abonnement_confirmes",
    )
    commentaire = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_confirmation = models.DateTimeField(null=True, blank=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        indexes = [
            models.Index(fields=["statut", "-date_confirmation"]),
            models.Index(fields=["fournisseur", "-date_creation"]),
            models.Index(fields=["devise", "statut", "-date_confirmation"]),
            models.Index(fields=["demande_abonnement", "statut"]),
            models.Index(fields=["prestataire", "provider_transaction_id"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(montant__gte=0),
                name="paiement_abonnement_montant_positif",
            ),
            models.UniqueConstraint(
                fields=["prestataire", "provider_transaction_id"],
                condition=~models.Q(provider_transaction_id=""),
                name="unique_transaction_prestataire",
            ),
            models.UniqueConstraint(
                fields=["demande_abonnement"],
                condition=models.Q(
                    statut="CONFIRME",
                    demande_abonnement__isnull=False,
                ),
                name="unique_paiement_confirme_par_demande",
            ),
        ]
        verbose_name = "paiement d'abonnement"
        verbose_name_plural = "paiements d'abonnement"


    def clean(self):
        if self.montant is not None and self.montant < 0:
            raise ValidationError(
                {"montant": "Le montant d'un paiement ne peut pas être négatif."}
            )
        if (
            self.abonnement_id
            and self.fournisseur_id
            and self.abonnement.fournisseur_id != self.fournisseur_id
        ):
            raise ValidationError(
                "Le paiement doit appartenir au même fournisseur que l'abonnement."
            )
        if (
            self.demande_abonnement_id
            and self.fournisseur_id
            and self.demande_abonnement.fournisseur_id != self.fournisseur_id
        ):
            raise ValidationError(
                "Le paiement doit appartenir au même fournisseur que la demande."
            )

    @property
    def est_confirme(self):
        return self.statut == self.Statut.CONFIRME

    def __str__(self):
        return f"{self.reference} - {self.montant} {self.devise} ({self.get_statut_display()})"


class EvenementAbonnement(models.Model):
    """Journal immuable des changements d'état métier et financiers.

    L'événement peut concerner soit un abonnement, soit une demande en cours
    de traitement. Cette double référence permet de tracer tout le cycle de
    vie, y compris avant qu'un abonnement n'existe.
    """

    class Type(models.TextChoices):
        CREATION = "CREATION", "Création"
        RENOUVELLEMENT = "RENOUVELLEMENT", "Renouvellement"
        SUSPENSION = "SUSPENSION", "Suspension"
        REACTIVATION = "REACTIVATION", "Réactivation"
        ANNULATION = "ANNULATION", "Annulation"
        EXPIRATION = "EXPIRATION", "Expiration"
        DEMANDE_CREEE = "DEMANDE_CREEE", "Demande créée"
        PAIEMENT_SOUMIS = "PAIEMENT_SOUMIS", "Paiement soumis"
        PAIEMENT_INITIE = "PAIEMENT_INITIE", "Paiement initié"
        PAIEMENT_CONFIRME = "PAIEMENT_CONFIRME", "Paiement confirmé"
        PAIEMENT_REFUSE = "PAIEMENT_REFUSE", "Paiement refusé"
        DEMANDE_ANNULEE = "DEMANDE_ANNULEE", "Demande annulée"
        DEMANDE_REFUSEE = "DEMANDE_REFUSEE", "Demande refusée"
        DEMANDE_EXPIREE = "DEMANDE_EXPIREE", "Demande expirée"
        ABONNEMENT_ACTIVE = "ABONNEMENT_ACTIVE", "Abonnement activé"
        ABONNEMENT_RENOUVELE = "ABONNEMENT_RENOUVELE", "Abonnement renouvelé"
        ABONNEMENT_SUSPENDU = "ABONNEMENT_SUSPENDU", "Abonnement suspendu"
        ABONNEMENT_REACTIVE = "ABONNEMENT_REACTIVE", "Abonnement réactivé"
        ABONNEMENT_ANNULE = "ABONNEMENT_ANNULE", "Abonnement annulé"
        ANOMALIE_PAIEMENT = "ANOMALIE_PAIEMENT", "Anomalie de paiement"

    abonnement = models.ForeignKey(
        Abonnement,
        on_delete=models.CASCADE,
        related_name="evenements",
        null=True,
        blank=True,
    )
    demande_abonnement = models.ForeignKey(
        "DemandeAbonnement",
        on_delete=models.CASCADE,
        related_name="evenements",
        null=True,
        blank=True,
    )
    type_evenement = models.CharField(max_length=30, choices=Type.choices)
    ancien_statut = models.CharField(max_length=30, blank=True)
    nouveau_statut = models.CharField(max_length=30, blank=True)
    montant = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Montant encaissé pour cet événement, si applicable.",
    )
    reference_paiement = models.CharField(max_length=150, blank=True)
    motif = models.TextField(blank=True)
    identifiant_correlation = models.CharField(max_length=100, blank=True)
    metadonnees = models.JSONField(
        default=dict,
        blank=True,
        help_text="Contexte technique non sensible de l'événement.",
    )
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
            models.Index(fields=["demande_abonnement", "-date_creation"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(abonnement__isnull=False)
                    | models.Q(demande_abonnement__isnull=False)
                ),
                name="evenement_abonnement_ou_demande_requis",
            ),
        ]
        verbose_name = "événement d'abonnement"
        verbose_name_plural = "événements d'abonnement"

    def __str__(self):
        cible = self.abonnement_id or self.demande_abonnement_id
        return f"{cible} - {self.get_type_evenement_display()}"


class DemandeAbonnement(models.Model):
    """Intention de souscription ou de renouvellement d'un fournisseur.

    La demande est le point d'entrée obligatoire du nouveau flux : elle est
    créée avant tout paiement et ne devient jamais un abonnement actif tant
    que le paiement n'a pas été vérifié puis confirmé atomiquement.
    """

    class TypeOperation(models.TextChoices):
        SOUSCRIPTION = "SOUSCRIPTION", "Souscription"
        RENOUVELLEMENT = "RENOUVELLEMENT", "Renouvellement"

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        EN_VERIFICATION = "EN_VERIFICATION", "En vérification"
        CONFIRMEE = "CONFIRMEE", "Confirmée"
        REFUSEE = "REFUSEE", "Refusée"
        ANNULEE = "ANNULEE", "Annulée"
        EXPIREE = "EXPIREE", "Expirée"
        ECHEC = "ECHEC", "Échec"

    class MoyenPaiement(models.TextChoices):
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile money"
        VIREMENT = "VIREMENT", "Virement"
        ESPECES = "ESPECES", "Espèces"
        CARTE = "CARTE", "Carte bancaire"
        AUTRE = "AUTRE", "Autre"

    STATUTS_ANNULABLES = (Statut.EN_ATTENTE, Statut.EN_VERIFICATION)
    STATUTS_TRAITABLES = (Statut.EN_ATTENTE, Statut.EN_VERIFICATION)
    STATUTS_FIGES = (
        Statut.CONFIRMEE,
        Statut.REFUSEE,
        Statut.ANNULEE,
        Statut.EXPIREE,
        Statut.ECHEC,
    )

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Identifiant interne unique de la demande.",
    )
    reference = models.CharField(
        max_length=40,
        unique=True,
        editable=False,
        default=generer_reference_demande,
        help_text="Référence KLARO publique communiquée au fournisseur.",
    )
    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="demandes_abonnement",
    )
    plan = models.ForeignKey(
        PlanAbonnement,
        on_delete=models.PROTECT,
        related_name="demandes_abonnement",
    )
    type_operation = models.CharField(
        max_length=20,
        choices=TypeOperation.choices,
        default=TypeOperation.SOUSCRIPTION,
    )
    abonnement_cible = models.ForeignKey(
        Abonnement,
        on_delete=models.PROTECT,
        related_name="demandes_renouvellement",
        null=True,
        blank=True,
        help_text="Abonnement renouvelé lorsque le type d'opération l'exige.",
    )
    montant = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Montant figé par le backend à la création de la demande.",
    )
    devise = models.CharField(max_length=3, default="XOF")
    moyen_paiement = models.CharField(
        max_length=20,
        choices=MoyenPaiement.choices,
        blank=True,
    )
    prestataire = models.CharField(max_length=50, blank=True)
    reference_transaction = models.CharField(max_length=150, blank=True)
    preuve_paiement = models.FileField(
        upload_to=chemin_preuve_paiement,
        null=True,
        blank=True,
        help_text="Justificatif privé de paiement manuel.",
    )
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.EN_ATTENTE,
    )
    cle_idempotence = models.CharField(
        max_length=100,
        help_text="Clé anti-doublon fournie par le client.",
    )
    commentaire_fournisseur = models.TextField(blank=True)
    motif_traitement = models.TextField(blank=True)
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="demandes_abonnement_creees",
    )
    traite_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="demandes_abonnement_traitees",
    )
    date_traitement = models.DateTimeField(null=True, blank=True)
    date_expiration = models.DateTimeField(null=True, blank=True)
    abonnement_genere = models.ForeignKey(
        Abonnement,
        on_delete=models.SET_NULL,
        related_name="demandes_source",
        null=True,
        blank=True,
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        indexes = [
            models.Index(fields=["fournisseur", "statut"]),
            models.Index(fields=["statut", "date_expiration"]),
            models.Index(fields=["type_operation", "statut"]),
            models.Index(fields=["reference"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["fournisseur", "cle_idempotence"],
                name="unique_demande_idempotence_fournisseur",
            ),
            models.UniqueConstraint(
                fields=["abonnement_cible"],
                condition=(
                    models.Q(type_operation="RENOUVELLEMENT")
                    & models.Q(
                        statut__in=["EN_ATTENTE", "EN_VERIFICATION"]
                    )
                ),
                name="unique_renouvellement_en_cours_par_abonnement",
            ),
            models.CheckConstraint(
                condition=models.Q(montant__gte=0),
                name="demande_abonnement_montant_positif",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(type_operation="SOUSCRIPTION")
                    | models.Q(abonnement_cible__isnull=False)
                ),
                name="demande_renouvellement_cible_requise",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(statut="CONFIRMEE")
                    | models.Q(abonnement_genere__isnull=False)
                ),
                name="demande_confirmee_abonnement_requis",
            ),
        ]
        verbose_name = "demande d'abonnement"
        verbose_name_plural = "demandes d'abonnement"

    def clean(self):
        if self.montant is not None and self.montant < 0:
            raise ValidationError(
                {"montant": "Le montant d'une demande ne peut pas être négatif."}
            )
        if self.type_operation == self.TypeOperation.RENOUVELLEMENT:
            if self.abonnement_cible_id is None:
                raise ValidationError(
                    {"abonnement_cible": "Un renouvellement doit cibler un abonnement."}
                )
            if (
                self.fournisseur_id
                and self.abonnement_cible.fournisseur_id != self.fournisseur_id
            ):
                raise ValidationError(
                    {
                        "abonnement_cible": (
                            "L'abonnement ciblé doit appartenir au même fournisseur."
                        )
                    }
                )

    @property
    def est_annulable(self):
        return self.statut in self.STATUTS_ANNULABLES

    @property
    def est_expiree(self):
        return (
            self.date_expiration is not None
            and self.date_expiration <= timezone.now()
            and self.statut in self.STATUTS_TRAITABLES
        )

    def __str__(self):
        return f"{self.reference} - {self.fournisseur} ({self.get_statut_display()})"


class WebhookEvenement(models.Model):
    """Notification externe reçue d'un prestataire de paiement.

    Chaque événement est conservé afin de garantir l'idempotence : un webhook
    rejoué ne doit jamais produire un second effet financier.
    """

    class StatutTraitement(models.TextChoices):
        RECU = "RECU", "Reçu"
        TRAITE = "TRAITE", "Traité"
        IGNORE = "IGNORE", "Ignoré"
        ERREUR = "ERREUR", "Erreur"
        ANOMALIE = "ANOMALIE", "Anomalie"

    prestataire = models.CharField(max_length=50)
    provider_event_id = models.CharField(max_length=190, blank=True)
    provider_transaction_id = models.CharField(max_length=190, blank=True)
    reference_demande = models.CharField(max_length=40, blank=True)
    payload_brut_protege = models.TextField(blank=True)
    signature_verifiee = models.BooleanField(default=False)
    statut_traitement = models.CharField(
        max_length=20,
        choices=StatutTraitement.choices,
        default=StatutTraitement.RECU,
    )
    nombre_tentatives = models.PositiveIntegerField(default=1)
    date_reception = models.DateTimeField(auto_now_add=True)
    date_traitement = models.DateTimeField(null=True, blank=True)
    message_erreur = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_reception", "-id"]
        indexes = [
            models.Index(fields=["prestataire", "provider_transaction_id"]),
            models.Index(fields=["statut_traitement", "-date_reception"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["prestataire", "provider_event_id"],
                condition=~models.Q(provider_event_id=""),
                name="unique_webhook_evenement_prestataire",
            ),
        ]
        verbose_name = "événement webhook"
        verbose_name_plural = "événements webhook"

    def __str__(self):
        return f"{self.prestataire} - {self.provider_event_id or self.pk}"
