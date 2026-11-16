from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.clients.models import Client

from .models import (
    CategorieProduit,
    CommandeAchat,
    ConfigurationNotification,
    DetteCommerciale,
    Facture,
    FournisseurMarchandise,
    LigneCommandeAchat,
    LigneReception,
    LigneVente,
    MouvementStock,
    NotificationClient,
    NotificationExterne,
    PaiementVente,
    Produit,
    ProduitUnite,
    Reception,
    Vente,
)
from .notifications import lien_public_facture
from .services import (
    ajouter_paiement_vente,
    creer_commande_achat,
    creer_vente,
    entree_stock,
)


class CategorieProduitSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategorieProduit
        fields = [
            "id",
            "nom",
            "description",
            "actif",
            "date_creation",
        ]
        read_only_fields = ["date_creation"]

    def validate(self, attrs):
        request = self.context.get("request")
        fournisseur = (
            getattr(request.user, "fournisseur_profile", None)
            if request
            else None
        )
        nom = attrs.get("nom", getattr(self.instance, "nom", None))
        if fournisseur is not None and nom:
            queryset = CategorieProduit.objects.filter(
                fournisseur=fournisseur,
                nom__iexact=nom,
            )
            if self.instance is not None:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError(
                    {"nom": "Une catégorie porte déjà ce nom."}
                )
        return attrs


class ProduitUniteSerializer(serializers.ModelSerializer):
    prix_effectif = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = ProduitUnite
        fields = [
            "id",
            "nom",
            "symbole",
            "facteur_conversion",
            "prix_vente",
            "prix_effectif",
            "est_base",
        ]


class ProduitUniteEcritureSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProduitUnite
        fields = [
            "nom",
            "symbole",
            "facteur_conversion",
            "prix_vente",
            "est_base",
        ]


class ProduitSerializer(serializers.ModelSerializer):
    categorie_nom = serializers.CharField(
        source="categorie.nom",
        read_only=True,
    )
    unites = serializers.SerializerMethodField()
    unites_ecriture = ProduitUniteEcritureSerializer(
        many=True,
        write_only=True,
        required=False,
    )
    est_en_rupture = serializers.BooleanField(read_only=True)
    en_alerte = serializers.BooleanField(read_only=True)
    stock_initial = serializers.DecimalField(
        max_digits=14,
        decimal_places=3,
        write_only=True,
        required=False,
        min_value=Decimal("0"),
        default=Decimal("0"),
    )
    image = serializers.ImageField(required=False, allow_null=True)
    supprimer_image = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
    )

    class Meta:
        model = Produit
        fields = [
            "id",
            "categorie",
            "categorie_nom",
            "nom",
            "reference",
            "description",
            "image",
            "supprimer_image",
            "unite_base",
            "prix_achat",
            "prix_vente",
            "seuil_alerte",
            "stock_initial",
            "stock_actuel",
            "est_en_rupture",
            "en_alerte",
            "actif",
            "unites",
            "unites_ecriture",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "reference",
            "stock_actuel",
            "est_en_rupture",
            "en_alerte",
            "date_creation",
            "date_modification",
        ]

    def get_unites(self, obj):
        return ProduitUniteSerializer(obj.unites.all(), many=True).data

    def validate_categorie(self, value):
        if value is None:
            return value
        request = self.context.get("request")
        fournisseur = (
            getattr(request.user, "fournisseur_profile", None)
            if request
            else None
        )
        if fournisseur is not None and value.fournisseur_id != fournisseur.pk:
            raise serializers.ValidationError(
                "Cette catégorie n'appartient pas à votre entreprise."
            )
        return value

    def create(self, validated_data):
        unites = validated_data.pop("unites_ecriture", [])
        stock_initial = validated_data.pop("stock_initial", Decimal("0"))
        validated_data.pop("supprimer_image", None)
        request = self.context["request"]
        with transaction.atomic():
            produit = Produit.objects.create(
                fournisseur=request.user.fournisseur_profile,
                **validated_data,
            )
            self._creer_unites(produit, unites)
            if stock_initial > 0:
                entree_stock(
                    produit,
                    stock_initial,
                    motif="Stock initial",
                    auteur=request.user,
                )
                produit.refresh_from_db(fields=["stock_actuel"])
        return produit

    def update(self, instance, validated_data):
        unites = validated_data.pop("unites_ecriture", None)
        validated_data.pop("stock_initial", None)
        supprimer_image = validated_data.pop("supprimer_image", False)
        remplacer_image = "image" in validated_data
        with transaction.atomic():
            produit = super().update(instance, validated_data)
            if supprimer_image and not remplacer_image and produit.image:
                produit.image.delete(save=False)
                produit.image = None
                produit.save(update_fields=["image"])
            if "prix_vente" in validated_data:
                produit.unites.filter(est_base=True).update(prix_vente=None)
            if unites is not None:
                self._creer_unites(produit, unites)
        return produit

    def _creer_unites(self, produit, unites):
        existantes = set(produit.unites.values_list("nom", flat=True))
        base_existante = produit.unites.filter(est_base=True).exists()

        if not unites:
            if not produit.unites.exists():
                ProduitUnite.objects.create(
                    produit=produit,
                    nom=produit.unite_base,
                    facteur_conversion=Decimal("1"),
                    prix_vente=None,
                    est_base=True,
                )
            return

        bases = [unite for unite in unites if unite.get("est_base")]
        if len(bases) > 1:
            raise serializers.ValidationError(
                {"unites_ecriture": "Une seule unité de base est autorisée."}
            )
        if bases and base_existante:
            raise serializers.ValidationError(
                {"unites_ecriture": "Le produit possède déjà une unité de base."}
            )
        for base in bases:
            facteur = base.get("facteur_conversion", Decimal("1"))
            if facteur != Decimal("1"):
                raise serializers.ValidationError(
                    {
                        "unites_ecriture": (
                            "L'unité de base doit avoir un facteur de "
                            "conversion égal à 1."
                        )
                    }
                )
            if base.get("nom") != produit.unite_base:
                raise serializers.ValidationError(
                    {
                        "unites_ecriture": (
                            "Le nom de l'unité de base doit correspondre à "
                            "l'unité de base du produit."
                        )
                    }
                )

        noms = set()
        for unite in unites:
            nom = unite["nom"]
            if nom in existantes or nom in noms:
                raise serializers.ValidationError(
                    {"unites_ecriture": f"L'unité « {nom} » existe déjà."}
                )
            noms.add(nom)
            ProduitUnite.objects.create(produit=produit, **unite)

        if not bases and not produit.unites.filter(est_base=True).exists():
            ProduitUnite.objects.create(
                produit=produit,
                nom=produit.unite_base,
                facteur_conversion=Decimal("1"),
                prix_vente=None,
                est_base=True,
            )


class FournisseurMarchandiseSerializer(serializers.ModelSerializer):
    class Meta:
        model = FournisseurMarchandise
        fields = [
            "id",
            "nom",
            "telephone",
            "adresse",
            "notes",
            "actif",
            "date_creation",
        ]
        read_only_fields = ["date_creation"]


class LigneCommandeAchatSerializer(serializers.ModelSerializer):
    produit_nom = serializers.CharField(source="produit.nom", read_only=True)
    unite_nom = serializers.CharField(source="unite.nom", read_only=True)
    unite_facteur = serializers.DecimalField(
        source="unite.facteur_conversion",
        max_digits=12,
        decimal_places=3,
        read_only=True,
    )

    class Meta:
        model = LigneCommandeAchat
        fields = [
            "id",
            "produit",
            "produit_nom",
            "unite",
            "unite_nom",
            "unite_facteur",
            "quantite",
            "quantite_base",
            "quantite_recue_base",
            "prix_achat_unitaire",
            "sous_total",
        ]


class LigneCommandeAchatCreationSerializer(serializers.Serializer):
    produit = serializers.PrimaryKeyRelatedField(queryset=Produit.objects.all())
    unite = serializers.PrimaryKeyRelatedField(
        queryset=ProduitUnite.objects.all()
    )
    quantite = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    prix_achat_unitaire = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
    )


class CommandeAchatSerializer(serializers.ModelSerializer):
    fournisseur_marchandise_nom = serializers.CharField(
        source="fournisseur_marchandise.nom",
        read_only=True,
    )
    statut_display = serializers.CharField(
        source="get_statut_display",
        read_only=True,
    )
    lignes = LigneCommandeAchatSerializer(many=True, read_only=True)

    class Meta:
        model = CommandeAchat
        fields = [
            "id",
            "reference",
            "fournisseur_marchandise",
            "fournisseur_marchandise_nom",
            "statut",
            "statut_display",
            "montant_total",
            "notes",
            "lignes",
            "date_commande",
            "date_creation",
        ]


class CommandeAchatCreationSerializer(serializers.Serializer):
    fournisseur_marchandise = serializers.PrimaryKeyRelatedField(
        queryset=FournisseurMarchandise.objects.all()
    )
    lignes = LigneCommandeAchatCreationSerializer(many=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    statut = serializers.ChoiceField(
        choices=[
            CommandeAchat.Statut.BROUILLON,
            CommandeAchat.Statut.COMMANDEE,
        ],
        required=False,
        default=CommandeAchat.Statut.COMMANDEE,
    )

    def validate_fournisseur_marchandise(self, value):
        request = self.context["request"]
        fournisseur = request.user.fournisseur_profile
        if value.fournisseur_id != fournisseur.pk:
            raise serializers.ValidationError(
                "Ce fournisseur de marchandises n'appartient pas à votre entreprise."
            )
        return value

    def validate_lignes(self, value):
        if not value:
            raise serializers.ValidationError(
                "Au moins une ligne est requise."
            )
        return value

    def create(self, validated_data):
        request = self.context["request"]
        lignes = validated_data.pop("lignes")
        return creer_commande_achat(
            fournisseur=request.user.fournisseur_profile,
            auteur=request.user,
            lignes=lignes,
            **validated_data,
        )


class LigneReceptionSerializer(serializers.ModelSerializer):
    produit_nom = serializers.CharField(
        source="ligne_commande.produit.nom",
        read_only=True,
    )
    unite_nom = serializers.CharField(
        source="ligne_commande.unite.nom",
        read_only=True,
    )

    class Meta:
        model = LigneReception
        fields = [
            "id",
            "ligne_commande",
            "produit_nom",
            "unite_nom",
            "quantite",
            "quantite_base",
        ]


class ReceptionSerializer(serializers.ModelSerializer):
    commande_reference = serializers.CharField(
        source="commande.reference",
        read_only=True,
    )
    lignes = LigneReceptionSerializer(many=True, read_only=True)

    class Meta:
        model = Reception
        fields = [
            "id",
            "reference",
            "commande",
            "commande_reference",
            "notes",
            "lignes",
            "date_reception",
        ]


class LigneReceptionCreationSerializer(serializers.Serializer):
    ligne_commande = serializers.PrimaryKeyRelatedField(
        queryset=LigneCommandeAchat.objects.all()
    )
    quantite = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )


class ReceptionCreationSerializer(serializers.Serializer):
    lignes = LigneReceptionCreationSerializer(many=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_lignes(self, value):
        if not value:
            raise serializers.ValidationError(
                "Au moins une ligne est requise."
            )
        return value


class LigneVenteSerializer(serializers.ModelSerializer):
    class Meta:
        model = LigneVente
        fields = [
            "id",
            "produit",
            "nom_produit",
            "unite_nom",
            "quantite",
            "quantite_base",
            "prix_unitaire",
            "sous_total",
        ]


class LigneVenteCreationSerializer(serializers.Serializer):
    produit = serializers.PrimaryKeyRelatedField(queryset=Produit.objects.all())
    unite = serializers.PrimaryKeyRelatedField(
        queryset=ProduitUnite.objects.all()
    )
    quantite = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    prix_unitaire = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
    )


class VenteSerializer(serializers.ModelSerializer):
    client_nom = serializers.CharField(
        source="client.nom_complet",
        read_only=True,
    )
    client_telephone = serializers.CharField(
        source="client.telephone",
        read_only=True,
    )
    statut_display = serializers.CharField(
        source="get_statut_display",
        read_only=True,
    )
    mode_paiement_display = serializers.CharField(
        source="get_mode_paiement_display",
        read_only=True,
    )
    lignes = LigneVenteSerializer(many=True, read_only=True)
    est_soldee = serializers.BooleanField(read_only=True)
    est_partielle = serializers.SerializerMethodField()
    facture_id = serializers.IntegerField(
        source="facture.id",
        read_only=True,
    )
    dette_statut = serializers.SerializerMethodField()
    nombre_paiements = serializers.SerializerMethodField()

    class Meta:
        model = Vente
        fields = [
            "id",
            "reference",
            "numero_facture",
            "client",
            "client_nom",
            "client_telephone",
            "statut",
            "statut_display",
            "montant_total",
            "montant_paye",
            "reste_a_payer",
            "mode_paiement",
            "mode_paiement_display",
            "est_soldee",
            "est_partielle",
            "lignes",
            "facture_id",
            "dette_statut",
            "nombre_paiements",
            "date_creation",
        ]

    def get_est_partielle(self, obj):
        return obj.montant_paye > 0 and obj.reste_a_payer > 0

    def get_dette_statut(self, obj):
        dette = getattr(obj, "dette_commerciale", None)
        return dette.statut if dette is not None else None

    def get_nombre_paiements(self, obj):
        return obj.paiements.count()


class VenteCreationSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all())
    lignes = LigneVenteCreationSerializer(many=True)
    montant_paye = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        default=Decimal("0"),
    )
    mode_paiement = serializers.ChoiceField(
        choices=Vente.ModePaiement.choices,
        required=False,
        default=Vente.ModePaiement.ESPECES,
    )
    cle_idempotence = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    def validate_client(self, value):
        request = self.context["request"]
        fournisseur = request.user.fournisseur_profile
        if value.fournisseur_id != fournisseur.pk:
            raise serializers.ValidationError(
                "Ce client n'appartient pas à votre entreprise."
            )
        return value

    def validate_lignes(self, value):
        if not value:
            raise serializers.ValidationError(
                "Au moins une ligne est requise."
            )
        return value

    def create(self, validated_data):
        request = self.context["request"]
        return creer_vente(
            fournisseur=request.user.fournisseur_profile,
            auteur=request.user,
            **validated_data,
        )


class PaiementVenteSerializer(serializers.ModelSerializer):
    mode_paiement_display = serializers.CharField(
        source="get_mode_paiement_display",
        read_only=True,
    )

    class Meta:
        model = PaiementVente
        fields = [
            "id",
            "montant",
            "mode_paiement",
            "mode_paiement_display",
            "reference_transaction",
            "commentaire",
            "date_creation",
        ]


class PaiementVenteCreationSerializer(serializers.Serializer):
    montant = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    mode_paiement = serializers.ChoiceField(
        choices=[
            Vente.ModePaiement.ESPECES,
            Vente.ModePaiement.WAVE,
            Vente.ModePaiement.ORANGE_MONEY,
            Vente.ModePaiement.VIREMENT,
        ],
        required=False,
        default=Vente.ModePaiement.ESPECES,
    )
    reference_transaction = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=150,
    )
    commentaire = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )

    def create(self, validated_data):
        request = self.context.get("request")
        return ajouter_paiement_vente(
            vente=self.context["vente"],
            auteur=request.user if request else None,
            **validated_data,
        )


class FactureSerializer(serializers.ModelSerializer):
    montant_paye = serializers.SerializerMethodField()
    reste_a_payer = serializers.SerializerMethodField()
    montant_paye_emission = serializers.DecimalField(
        source="montant_paye",
        max_digits=14,
        decimal_places=2,
        read_only=True,
    )
    reste_a_payer_emission = serializers.DecimalField(
        source="reste_a_payer",
        max_digits=14,
        decimal_places=2,
        read_only=True,
    )
    vente_reference = serializers.CharField(
        source="vente.reference",
        read_only=True,
    )
    vente_statut = serializers.CharField(
        source="vente.statut",
        read_only=True,
    )
    lien_pdf = serializers.SerializerMethodField()
    lien_public = serializers.SerializerMethodField()
    est_payee = serializers.SerializerMethodField()

    class Meta:
        model = Facture
        fields = [
            "id",
            "numero",
            "vente",
            "vente_reference",
            "vente_statut",
            "date_emission",
            "nom_commerce",
            "fournisseur_adresse",
            "fournisseur_telephone",
            "client_nom",
            "client_telephone",
            "client_adresse",
            "montant_total",
            "montant_paye",
            "reste_a_payer",
            "montant_paye_emission",
            "reste_a_payer_emission",
            "devise",
            "modes_paiement",
            "lignes",
            "est_payee",
            "lien_pdf",
            "lien_public",
            "date_creation",
        ]

    def get_montant_paye(self, obj):
        return str(obj.vente.montant_paye)

    def get_reste_a_payer(self, obj):
        return str(obj.vente.reste_a_payer)

    def get_est_payee(self, obj):
        return obj.vente.reste_a_payer <= 0

    def get_lien_pdf(self, obj):
        request = self.context.get("request")
        chemin = f"/api/commerce/factures/{obj.pk}/pdf/"
        return request.build_absolute_uri(chemin) if request else chemin

    def get_lien_public(self, obj):
        return lien_public_facture(obj, self.context.get("request"))


class NotificationClientSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(
        source="get_type_notification_display",
        read_only=True,
    )
    fournisseur_nom = serializers.CharField(
        source="fournisseur.nom_entreprise",
        read_only=True,
    )
    facture_numero = serializers.CharField(
        source="facture.numero",
        read_only=True,
    )
    vente_reference = serializers.CharField(
        source="vente.reference",
        read_only=True,
    )

    class Meta:
        model = NotificationClient
        fields = [
            "id",
            "type_notification",
            "type_display",
            "titre",
            "message",
            "lien",
            "lue",
            "date_lecture",
            "fournisseur_nom",
            "vente",
            "vente_reference",
            "facture",
            "facture_numero",
            "date_creation",
        ]


class NotificationExterneSerializer(serializers.ModelSerializer):
    canal_display = serializers.CharField(
        source="get_canal_display",
        read_only=True,
    )
    statut_display = serializers.CharField(
        source="get_statut_display",
        read_only=True,
    )
    notification_titre = serializers.CharField(
        source="notification.titre",
        read_only=True,
    )
    client_nom = serializers.CharField(
        source="notification.client.nom_complet",
        read_only=True,
    )

    class Meta:
        model = NotificationExterne
        fields = [
            "id",
            "notification",
            "notification_titre",
            "client_nom",
            "canal",
            "canal_display",
            "statut",
            "statut_display",
            "tentatives",
            "tentatives_max",
            "derniere_erreur",
            "provider_message_id",
            "date_creation",
            "date_envoi",
            "date_prochaine_tentative",
        ]


class MouvementStockSerializer(serializers.ModelSerializer):
    produit_nom = serializers.CharField(source="produit.nom", read_only=True)
    type_display = serializers.CharField(
        source="get_type_mouvement_display",
        read_only=True,
    )
    vente_reference = serializers.CharField(
        source="vente.reference",
        read_only=True,
    )
    reception_reference = serializers.CharField(
        source="reception.reference",
        read_only=True,
    )

    class Meta:
        model = MouvementStock
        fields = [
            "id",
            "produit",
            "produit_nom",
            "type_mouvement",
            "type_display",
            "quantite_base",
            "stock_apres",
            "motif",
            "vente",
            "vente_reference",
            "reception",
            "reception_reference",
            "date_creation",
        ]


class DetteCommercialeSerializer(serializers.ModelSerializer):
    client_nom = serializers.CharField(
        source="client.nom_complet",
        read_only=True,
    )
    vente_reference = serializers.CharField(
        source="vente.reference",
        read_only=True,
    )
    numero_facture = serializers.CharField(
        source="vente.numero_facture",
        read_only=True,
    )
    statut_display = serializers.CharField(
        source="get_statut_display",
        read_only=True,
    )

    class Meta:
        model = DetteCommerciale
        fields = [
            "id",
            "client",
            "client_nom",
            "vente",
            "vente_reference",
            "numero_facture",
            "montant_initial",
            "montant_restant",
            "statut",
            "statut_display",
            "date_creation",
            "date_modification",
        ]


class ConfigurationNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConfigurationNotification
        fields = [
            "sms_actif",
            "whatsapp_actif",
            "date_modification",
        ]
        read_only_fields = ["date_modification"]


class EntreeStockSerializer(serializers.Serializer):
    quantite = serializers.DecimalField(
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    motif = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=255,
    )
