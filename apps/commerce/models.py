import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


def generer_reference(prefixe):
    return f"{prefixe}-{uuid.uuid4().hex[:10].upper()}"


def generer_reference_vente():
    return generer_reference("VTE")


class CategorieProduit(models.Model):
    """Catégorie de produits propre à un fournisseur."""

    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="categories_commerce",
    )
    nom = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["fournisseur", "nom"],
                name="unique_categorie_par_fournisseur",
            )
        ]

    def __str__(self):
        return self.nom


class Produit(models.Model):
    """Produit d'un fournisseur, stocké dans son unité de base.

    Le stock n'est jamais modifié directement : il évolue exclusivement via
    ``MouvementStock`` (ventes, réceptions, entrées de stock).
    """

    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="produits_commerce",
    )
    categorie = models.ForeignKey(
        CategorieProduit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="produits",
    )
    nom = models.CharField(max_length=255)
    reference = models.CharField(max_length=40, blank=True)
    description = models.TextField(blank=True)
    unite_base = models.CharField(
        max_length=30,
        default="unité",
        help_text="Nom de l'unité de base (bouteille, kilogramme, unité…).",
    )
    prix_achat = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    prix_vente = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Prix de vente d'une unité de base.",
    )
    seuil_alerte = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    stock_actuel = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        editable=False,
        help_text="Stock exprimé dans l'unité de base. Piloté par les mouvements.",
    )
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["fournisseur", "reference"],
                name="unique_reference_produit_par_fournisseur",
            ),
            models.CheckConstraint(
                condition=models.Q(stock_actuel__gte=0),
                name="produit_stock_positif",
            ),
        ]
        indexes = [
            models.Index(fields=["fournisseur", "actif"]),
            models.Index(fields=["fournisseur", "nom"]),
        ]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generer_reference("PRD")
        super().save(*args, **kwargs)

    @property
    def est_en_rupture(self):
        return self.stock_actuel <= 0

    @property
    def en_alerte(self):
        return self.stock_actuel <= self.seuil_alerte

    def __str__(self):
        return f"{self.nom} ({self.fournisseur})"


class ProduitUnite(models.Model):
    """Unité de vente/achat d'un produit avec conversion vers l'unité de base.

    Exemple : un carton de 24 bouteilles a ``facteur_conversion = 24``.
    """

    produit = models.ForeignKey(
        Produit,
        on_delete=models.CASCADE,
        related_name="unites",
    )
    nom = models.CharField(max_length=50)
    symbole = models.CharField(max_length=20, blank=True)
    facteur_conversion = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
        help_text="Nombre d'unités de base contenues dans cette unité.",
    )
    prix_vente = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Prix de vente de cette unité. Vide : prix de base × facteur.",
    )
    est_base = models.BooleanField(default=False)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-est_base", "nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["produit", "nom"],
                name="unique_unite_par_produit",
            ),
            models.UniqueConstraint(
                fields=["produit"],
                condition=models.Q(est_base=True),
                name="unique_unite_base_par_produit",
            ),
            models.CheckConstraint(
                condition=models.Q(facteur_conversion__gt=0),
                name="unite_facteur_positif",
            ),
        ]

    @property
    def prix_effectif(self):
        if self.prix_vente is not None:
            return self.prix_vente
        return (self.produit.prix_vente * self.facteur_conversion).quantize(
            Decimal("0.01")
        )

    def __str__(self):
        return f"{self.nom} ({self.produit.nom})"


class FournisseurMarchandise(models.Model):
    """Fournisseur de marchandises (grossiste) enregistré par un commerçant."""

    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="fournisseurs_marchandises",
    )
    nom = models.CharField(max_length=255)
    telephone = models.CharField(max_length=20, blank=True)
    adresse = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class CommandeAchat(models.Model):
    """Commande passée auprès d'un fournisseur de marchandises."""

    class Statut(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        COMMANDEE = "COMMANDEE", "Commandée"
        PARTIELLEMENT_RECUE = "PARTIELLEMENT_RECUE", "Partiellement reçue"
        RECUE = "RECUE", "Reçue"
        ANNULEE = "ANNULEE", "Annulée"

    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="commandes_achat",
    )
    fournisseur_marchandise = models.ForeignKey(
        FournisseurMarchandise,
        on_delete=models.PROTECT,
        related_name="commandes",
    )
    reference = models.CharField(max_length=40, unique=True, blank=True)
    statut = models.CharField(
        max_length=30,
        choices=Statut.choices,
        default=Statut.COMMANDEE,
    )
    montant_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        editable=False,
    )
    notes = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_commande = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        indexes = [
            models.Index(fields=["fournisseur", "statut"]),
            models.Index(fields=["fournisseur", "-date_creation"]),
        ]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generer_reference("ACH")
        super().save(*args, **kwargs)

    def clean(self):
        if (
            self.fournisseur_marchandise_id
            and self.fournisseur_id
            and self.fournisseur_marchandise.fournisseur_id != self.fournisseur_id
        ):
            raise ValidationError(
                "Le fournisseur de marchandises doit appartenir au même commerçant."
            )

    def __str__(self):
        return self.reference


class LigneCommandeAchat(models.Model):
    commande = models.ForeignKey(
        CommandeAchat,
        on_delete=models.CASCADE,
        related_name="lignes",
    )
    produit = models.ForeignKey(
        Produit,
        on_delete=models.PROTECT,
        related_name="lignes_commande_achat",
    )
    unite = models.ForeignKey(
        ProduitUnite,
        on_delete=models.PROTECT,
        related_name="lignes_commande_achat",
    )
    quantite = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    quantite_base = models.DecimalField(max_digits=14, decimal_places=3)
    quantite_recue_base = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )
    prix_achat_unitaire = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    sous_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.produit} × {self.quantite}"


class Reception(models.Model):
    """Réception de marchandises liée à une commande d'achat."""

    commande = models.ForeignKey(
        CommandeAchat,
        on_delete=models.PROTECT,
        related_name="receptions",
    )
    reference = models.CharField(max_length=40, unique=True, blank=True)
    notes = models.TextField(blank=True)
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receptions_commerce",
    )
    date_reception = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_reception", "-id"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generer_reference("REC")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.reference


class LigneReception(models.Model):
    reception = models.ForeignKey(
        Reception,
        on_delete=models.CASCADE,
        related_name="lignes",
    )
    ligne_commande = models.ForeignKey(
        LigneCommandeAchat,
        on_delete=models.PROTECT,
        related_name="receptions",
    )
    quantite = models.DecimalField(max_digits=12, decimal_places=3)
    quantite_base = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta:
        ordering = ["id"]


class MouvementStock(models.Model):
    """Historique immuable des entrées et sorties de stock."""

    class Type(models.TextChoices):
        ENTREE = "ENTREE", "Entrée"
        SORTIE = "SORTIE", "Sortie"
        AJUSTEMENT = "AJUSTEMENT", "Ajustement"

    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="mouvements_stock",
    )
    produit = models.ForeignKey(
        Produit,
        on_delete=models.PROTECT,
        related_name="mouvements",
    )
    type_mouvement = models.CharField(max_length=20, choices=Type.choices)
    quantite_base = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    stock_apres = models.DecimalField(max_digits=14, decimal_places=3)
    motif = models.CharField(max_length=255, blank=True)
    vente = models.ForeignKey(
        "commerce.Vente",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mouvements_stock",
    )
    reception = models.ForeignKey(
        Reception,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mouvements_stock",
    )
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mouvements_stock_commerce",
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        indexes = [
            models.Index(fields=["fournisseur", "-date_creation"]),
            models.Index(fields=["produit", "-date_creation"]),
            models.Index(fields=["type_mouvement"]),
        ]

    def __str__(self):
        return f"{self.get_type_mouvement_display()} {self.quantite_base}"


class Vente(models.Model):
    """Vente validée, rattachée à un client du fournisseur."""

    class Statut(models.TextChoices):
        VALIDEE = "VALIDEE", "Validée"
        ANNULEE = "ANNULEE", "Annulée"

    class ModePaiement(models.TextChoices):
        ESPECES = "ESPECES", "Espèces"
        WAVE = "WAVE", "Wave"
        ORANGE_MONEY = "ORANGE_MONEY", "Orange Money"
        VIREMENT = "VIREMENT", "Virement bancaire"
        PARTIEL = "PARTIEL", "Paiement partiel"
        CREDIT = "CREDIT", "Vente à crédit"

    reference = models.CharField(
        max_length=40,
        unique=True,
        editable=False,
        default=generer_reference_vente,
    )
    numero_facture = models.CharField(
        max_length=30,
        unique=True,
        null=True,
        blank=True,
    )
    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="ventes_commerce",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="ventes_commerce",
    )
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.VALIDEE,
    )
    montant_total = models.DecimalField(max_digits=14, decimal_places=2)
    montant_paye = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    reste_a_payer = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    mode_paiement = models.CharField(
        max_length=20,
        choices=ModePaiement.choices,
        default=ModePaiement.ESPECES,
    )
    cle_idempotence = models.CharField(max_length=100, blank=True, null=True)
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ventes_commerce_effectuees",
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["fournisseur", "cle_idempotence"],
                condition=models.Q(cle_idempotence__isnull=False),
                name="unique_vente_idempotence_fournisseur",
            ),
            models.CheckConstraint(
                condition=models.Q(montant_total__gte=0),
                name="vente_montant_total_positif",
            ),
            models.CheckConstraint(
                condition=models.Q(montant_paye__gte=0),
                name="vente_montant_paye_positif",
            ),
            models.CheckConstraint(
                condition=models.Q(reste_a_payer__gte=0),
                name="vente_reste_positif",
            ),
        ]
        indexes = [
            models.Index(fields=["fournisseur", "-date_creation"]),
            models.Index(fields=["client", "-date_creation"]),
            models.Index(fields=["statut", "-date_creation"]),
        ]

    def clean(self):
        if self.client_id and self.fournisseur_id:
            if self.client.fournisseur_id != self.fournisseur_id:
                raise ValidationError(
                    "Le client doit appartenir au même fournisseur que la vente."
                )
        if self.montant_paye is not None and self.montant_total is not None:
            if self.montant_paye > self.montant_total:
                raise ValidationError(
                    "Le montant payé ne peut pas dépasser le montant total."
                )

    @property
    def est_soldee(self):
        return self.reste_a_payer <= 0

    def __str__(self):
        return f"{self.reference} - {self.montant_total}"


class LigneVente(models.Model):
    """Ligne de vente avec instantané du nom, de l'unité et des prix."""

    vente = models.ForeignKey(
        Vente,
        on_delete=models.CASCADE,
        related_name="lignes",
    )
    produit = models.ForeignKey(
        Produit,
        on_delete=models.PROTECT,
        related_name="lignes_vente",
    )
    unite = models.ForeignKey(
        ProduitUnite,
        on_delete=models.PROTECT,
        related_name="lignes_vente",
    )
    nom_produit = models.CharField(max_length=255)
    unite_nom = models.CharField(max_length=50)
    quantite = models.DecimalField(max_digits=12, decimal_places=3)
    quantite_base = models.DecimalField(max_digits=14, decimal_places=3)
    prix_unitaire = models.DecimalField(max_digits=12, decimal_places=2)
    prix_achat_unitaire = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    sous_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.nom_produit} × {self.quantite}"


class PaiementVente(models.Model):
    """Paiement encaissé sur une vente commerciale."""

    vente = models.ForeignKey(
        Vente,
        on_delete=models.CASCADE,
        related_name="paiements",
    )
    montant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    mode_paiement = models.CharField(
        max_length=20,
        choices=Vente.ModePaiement.choices,
        default=Vente.ModePaiement.ESPECES,
    )
    reference_transaction = models.CharField(max_length=150, blank=True)
    commentaire = models.TextField(blank=True)
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paiements_vente_commerce",
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        indexes = [models.Index(fields=["vente", "-date_creation"])]

    def __str__(self):
        return f"{self.montant} - {self.vente.reference}"


class Facture(models.Model):
    """Facture générée automatiquement à la validation d'une vente.

    Les informations de la vente sont figées dans ``lignes`` et dans les
    champs d'instantané. Une facture n'est jamais modifiée silencieusement :
    toute correction doit passer par ``EvenementFacture``.
    """

    vente = models.OneToOneField(
        Vente,
        on_delete=models.CASCADE,
        related_name="facture",
    )
    numero = models.CharField(max_length=30, unique=True)
    date_emission = models.DateTimeField(auto_now_add=True)
    nom_commerce = models.CharField(max_length=255)
    fournisseur_adresse = models.CharField(max_length=255, blank=True)
    fournisseur_telephone = models.CharField(max_length=20, blank=True)
    client_nom = models.CharField(max_length=255)
    client_telephone = models.CharField(max_length=20, blank=True)
    client_adresse = models.CharField(max_length=255, blank=True)
    montant_total = models.DecimalField(max_digits=14, decimal_places=2)
    montant_paye = models.DecimalField(max_digits=14, decimal_places=2)
    reste_a_payer = models.DecimalField(max_digits=14, decimal_places=2)
    devise = models.CharField(max_length=3, default="XOF")
    modes_paiement = models.JSONField(default=list, blank=True)
    lignes = models.JSONField(default=list, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_emission", "-id"]
        indexes = [
            models.Index(fields=["numero"]),
            models.Index(fields=["-date_emission"]),
        ]

    def save(self, *args, **kwargs):
        """Une facture émise est un document immuable.

        Les corrections doivent passer par un service dédié qui journalise un
        ``EvenementFacture`` et utilise ``QuerySet.update``.
        """
        if self.pk and not getattr(self, "_correction_autorisee", False):
            raise ValidationError(
                "Une facture émise est immuable. Utilisez une correction "
                "journalisée pour la modifier."
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero


class EvenementFacture(models.Model):
    """Trace des créations et corrections apportées à une facture."""

    class Type(models.TextChoices):
        CREATION = "CREATION", "Création"
        CORRECTION = "CORRECTION", "Correction"

    facture = models.ForeignKey(
        Facture,
        on_delete=models.CASCADE,
        related_name="evenements",
    )
    type_evenement = models.CharField(max_length=20, choices=Type.choices)
    description = models.TextField(blank=True)
    donnees = models.JSONField(default=dict, blank=True)
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evenements_facture_commerce",
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation", "-id"]


class DetteCommerciale(models.Model):
    """Dette née d'une vente à crédit ou partiellement payée.

    Cette dette est totalement indépendante du solde de remboursement et des
    encaissements existants.
    """

    class Statut(models.TextChoices):
        OUVERTE = "OUVERTE", "Ouverte"
        PARTIELLE = "PARTIELLE", "Partiellement réglée"
        SOLDEE = "SOLDEE", "Soldée"
        ANNULEE = "ANNULEE", "Annulée"

    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="dettes_commerciales",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="dettes_commerciales",
    )
    vente = models.OneToOneField(
        Vente,
        on_delete=models.CASCADE,
        related_name="dette_commerciale",
    )
    montant_initial = models.DecimalField(max_digits=14, decimal_places=2)
    montant_restant = models.DecimalField(max_digits=14, decimal_places=2)
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.OUVERTE,
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        indexes = [
            models.Index(fields=["fournisseur", "statut"]),
            models.Index(fields=["client", "statut"]),
        ]

    def __str__(self):
        return f"{self.client} - {self.montant_restant}"


class NotificationClient(models.Model):
    """Notification interne visible dans l'espace du client."""

    class Type(models.TextChoices):
        NOUVELLE_VENTE = "NOUVELLE_VENTE", "Nouvelle vente"
        NOUVELLE_FACTURE = "NOUVELLE_FACTURE", "Nouvelle facture"
        PAIEMENT_RECU = "PAIEMENT_RECU", "Paiement reçu"
        RAPPEL_DETTE = "RAPPEL_DETTE", "Rappel de dette"
        VENTE_ANNULEE = "VENTE_ANNULEE", "Vente annulée"

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
        related_name="notifications_commerce",
    )
    fournisseur = models.ForeignKey(
        "fournisseurs.Fournisseur",
        on_delete=models.PROTECT,
        related_name="notifications_commerce",
    )
    vente = models.ForeignKey(
        Vente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    facture = models.ForeignKey(
        Facture,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    type_notification = models.CharField(max_length=30, choices=Type.choices)
    titre = models.CharField(max_length=255)
    message = models.TextField()
    lien = models.CharField(max_length=255, blank=True)
    lue = models.BooleanField(default=False)
    date_lecture = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        indexes = [
            models.Index(fields=["client", "lue", "-date_creation"]),
            models.Index(fields=["client", "-date_creation"]),
        ]

    def marquer_lue(self):
        if not self.lue:
            self.lue = True
            self.date_lecture = timezone.now()
            self.save(update_fields=["lue", "date_lecture"])

    def __str__(self):
        return self.titre


class NotificationExterne(models.Model):
    """Tentative d'envoi d'une notification par SMS ou WhatsApp."""

    class Canal(models.TextChoices):
        SMS = "SMS", "SMS"
        WHATSAPP = "WHATSAPP", "WhatsApp"

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        ENVOYE = "ENVOYE", "Envoyé"
        ECHOUE = "ECHOUE", "Échoué"

    notification = models.ForeignKey(
        NotificationClient,
        on_delete=models.CASCADE,
        related_name="envois",
    )
    canal = models.CharField(max_length=20, choices=Canal.choices)
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.EN_ATTENTE,
    )
    tentatives = models.PositiveIntegerField(default=0)
    tentatives_max = models.PositiveIntegerField(default=5)
    derniere_erreur = models.TextField(blank=True)
    provider_message_id = models.CharField(max_length=190, blank=True)
    date_prochaine_tentative = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_envoi = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "canal"],
                name="unique_envoi_notification_canal",
            )
        ]
        indexes = [
            models.Index(fields=["statut", "date_prochaine_tentative"]),
            models.Index(fields=["statut", "-date_creation"]),
        ]

    def __str__(self):
        return f"{self.canal} - {self.get_statut_display()}"


class ConfigurationNotification(models.Model):
    """Canaux de notification activés pour un fournisseur."""

    fournisseur = models.OneToOneField(
        "fournisseurs.Fournisseur",
        on_delete=models.CASCADE,
        related_name="configuration_notifications",
    )
    sms_actif = models.BooleanField(default=False)
    whatsapp_actif = models.BooleanField(default=False)
    date_modification = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Notifications de {self.fournisseur}"
