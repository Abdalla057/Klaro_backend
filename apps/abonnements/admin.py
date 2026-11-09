from django.contrib import admin

from .models import (
    Abonnement,
    DemandeAbonnement,
    EvenementAbonnement,
    PaiementAbonnement,
    PlanAbonnement,
    WebhookEvenement,
)


@admin.register(PlanAbonnement)
class PlanAbonnementAdmin(admin.ModelAdmin):
    list_display = (
        "nom",
        "code",
        "prix",
        "devise",
        "duree_jours",
        "max_clients",
        "max_agents",
        "est_essai",
        "actif",
    )
    list_filter = ("actif", "est_essai", "devise")
    search_fields = ("nom", "code")
    ordering = ("prix", "duree_jours")


@admin.register(Abonnement)
class AbonnementAdmin(admin.ModelAdmin):
    list_display = (
        "fournisseur",
        "plan",
        "statut",
        "date_debut",
        "date_fin",
        "est_essai",
        "montant",
        "devise",
    )
    list_filter = ("statut", "est_essai", "plan", "devise")
    search_fields = (
        "fournisseur__nom_entreprise",
        "fournisseur__code_entreprise",
        "fournisseur__utilisateur__telephone",
        "reference_paiement",
    )
    readonly_fields = ("date_creation", "date_modification")
    date_hierarchy = "date_debut"
    autocomplete_fields = ("fournisseur", "plan", "cree_par")


@admin.register(EvenementAbonnement)
class EvenementAbonnementAdmin(admin.ModelAdmin):
    list_display = (
        "abonnement",
        "type_evenement",
        "ancien_statut",
        "nouveau_statut",
        "montant",
        "effectue_par",
        "date_creation",
    )
    list_filter = ("type_evenement",)
    search_fields = (
        "abonnement__fournisseur__nom_entreprise",
        "motif",
        "reference_paiement",
    )
    readonly_fields = (
        "abonnement",
        "type_evenement",
        "ancien_statut",
        "nouveau_statut",
        "montant",
        "reference_paiement",
        "motif",
        "effectue_par",
        "date_creation",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PaiementAbonnement)
class PaiementAbonnementAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "fournisseur",
        "plan",
        "type_operation",
        "montant",
        "devise",
        "statut",
        "date_creation",
        "date_confirmation",
    )
    list_filter = ("statut", "type_operation", "devise", "moyen_paiement")
    search_fields = (
        "reference",
        "reference_transaction",
        "fournisseur__nom_entreprise",
        "fournisseur__code_entreprise",
        "fournisseur__utilisateur__telephone",
    )
    readonly_fields = [field.name for field in PaiementAbonnement._meta.fields]
    date_hierarchy = "date_creation"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DemandeAbonnement)
class DemandeAbonnementAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "fournisseur",
        "plan",
        "type_operation",
        "montant",
        "devise",
        "statut",
        "date_creation",
        "date_expiration",
    )
    list_filter = (
        "statut",
        "type_operation",
        "moyen_paiement",
        "prestataire",
        "devise",
    )
    search_fields = (
        "reference",
        "reference_transaction",
        "fournisseur__nom_entreprise",
        "fournisseur__code_entreprise",
        "fournisseur__telephone",
    )
    readonly_fields = [field.name for field in DemandeAbonnement._meta.fields]
    date_hierarchy = "date_creation"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WebhookEvenement)
class WebhookEvenementAdmin(admin.ModelAdmin):
    list_display = (
        "prestataire",
        "provider_event_id",
        "provider_transaction_id",
        "reference_demande",
        "signature_verifiee",
        "statut_traitement",
        "nombre_tentatives",
        "date_reception",
    )
    list_filter = ("prestataire", "statut_traitement", "signature_verifiee")
    search_fields = (
        "provider_event_id",
        "provider_transaction_id",
        "reference_demande",
    )
    readonly_fields = [field.name for field in WebhookEvenement._meta.fields]
    date_hierarchy = "date_reception"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
