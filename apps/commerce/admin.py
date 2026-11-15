from django.contrib import admin

from .models import (
    CategorieProduit,
    CommandeAchat,
    ConfigurationNotification,
    DetteCommerciale,
    EvenementFacture,
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


@admin.register(CategorieProduit)
class CategorieProduitAdmin(admin.ModelAdmin):
    list_display = ("nom", "fournisseur", "actif", "date_creation")
    list_filter = ("actif", "fournisseur")
    search_fields = ("nom",)


class ProduitUniteInline(admin.TabularInline):
    model = ProduitUnite
    extra = 0


@admin.register(Produit)
class ProduitAdmin(admin.ModelAdmin):
    list_display = (
        "nom",
        "reference",
        "fournisseur",
        "stock_actuel",
        "seuil_alerte",
        "prix_vente",
        "actif",
    )
    list_filter = ("actif", "fournisseur", "categorie")
    search_fields = ("nom", "reference")
    inlines = [ProduitUniteInline]


@admin.register(FournisseurMarchandise)
class FournisseurMarchandiseAdmin(admin.ModelAdmin):
    list_display = ("nom", "fournisseur", "telephone", "actif")
    list_filter = ("actif", "fournisseur")
    search_fields = ("nom", "telephone")


class LigneCommandeAchatInline(admin.TabularInline):
    model = LigneCommandeAchat
    extra = 0


@admin.register(CommandeAchat)
class CommandeAchatAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "fournisseur",
        "fournisseur_marchandise",
        "statut",
        "montant_total",
        "date_creation",
    )
    list_filter = ("statut", "fournisseur")
    search_fields = ("reference", "fournisseur_marchandise__nom")
    inlines = [LigneCommandeAchatInline]


class LigneReceptionInline(admin.TabularInline):
    model = LigneReception
    extra = 0


@admin.register(Reception)
class ReceptionAdmin(admin.ModelAdmin):
    list_display = ("reference", "commande", "date_reception")
    search_fields = ("reference", "commande__reference")
    inlines = [LigneReceptionInline]


@admin.register(MouvementStock)
class MouvementStockAdmin(admin.ModelAdmin):
    list_display = (
        "produit",
        "type_mouvement",
        "quantite_base",
        "stock_apres",
        "fournisseur",
        "date_creation",
    )
    list_filter = ("type_mouvement", "fournisseur")
    search_fields = ("produit__nom", "motif")


class LigneVenteInline(admin.TabularInline):
    model = LigneVente
    extra = 0
    readonly_fields = (
        "nom_produit",
        "unite_nom",
        "quantite",
        "quantite_base",
        "prix_unitaire",
        "sous_total",
    )


class PaiementVenteInline(admin.TabularInline):
    model = PaiementVente
    extra = 0
    readonly_fields = ("montant", "mode_paiement", "date_creation")


@admin.register(Vente)
class VenteAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "numero_facture",
        "fournisseur",
        "client",
        "montant_total",
        "montant_paye",
        "reste_a_payer",
        "date_creation",
    )
    list_filter = ("statut", "mode_paiement", "fournisseur")
    search_fields = ("reference", "numero_facture", "client__telephone")
    inlines = [LigneVenteInline, PaiementVenteInline]


class EvenementFactureInline(admin.TabularInline):
    model = EvenementFacture
    extra = 0
    readonly_fields = ("type_evenement", "description", "donnees", "date_creation")


@admin.register(Facture)
class FactureAdmin(admin.ModelAdmin):
    list_display = (
        "numero",
        "nom_commerce",
        "client_nom",
        "montant_total",
        "reste_a_payer",
        "date_emission",
    )
    search_fields = ("numero", "client_nom", "client_telephone")
    inlines = [EvenementFactureInline]


@admin.register(DetteCommerciale)
class DetteCommercialeAdmin(admin.ModelAdmin):
    list_display = (
        "client",
        "fournisseur",
        "montant_initial",
        "montant_restant",
        "statut",
        "date_creation",
    )
    list_filter = ("statut", "fournisseur")
    search_fields = ("client__telephone",)


@admin.register(NotificationClient)
class NotificationClientAdmin(admin.ModelAdmin):
    list_display = (
        "titre",
        "client",
        "fournisseur",
        "type_notification",
        "lue",
        "date_creation",
    )
    list_filter = ("type_notification", "lue", "fournisseur")
    search_fields = ("titre", "client__telephone")


@admin.register(NotificationExterne)
class NotificationExterneAdmin(admin.ModelAdmin):
    list_display = (
        "notification",
        "canal",
        "statut",
        "tentatives",
        "date_creation",
        "date_envoi",
    )
    list_filter = ("canal", "statut")
    search_fields = ("notification__titre",)


@admin.register(ConfigurationNotification)
class ConfigurationNotificationAdmin(admin.ModelAdmin):
    list_display = ("fournisseur", "sms_actif", "whatsapp_actif")
    list_filter = ("sms_actif", "whatsapp_actif")
